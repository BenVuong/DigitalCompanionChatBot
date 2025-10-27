from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI
import json
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
import asyncio
from typing import Optional, Dict, Any
from chatMessage import ChatMessage
from mcpManager import loadMCPConfig, mcpToolToOpenAIFormat

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client and database
client = OpenAI(api_key="none", base_url="http://localhost:5001/v1")
db = ChatMessage("chatMemory.db")

# Global storage for MCP sessions and tools
mcp_sessions: Dict[str, ClientSession] = {}
mcp_tools: Dict[str, list] = {}
pending_approvals: Dict[str, asyncio.Queue] = {}
background_tasks = set()


class ChatRequest(BaseModel):
    message: str


class ToolApprovalResponse(BaseModel):
    approved: bool
    reason: Optional[str] = ""


async def maintain_mcp_connection(serverName: str, serverParams: dict):
    """Maintain a persistent connection to an MCP server"""
    try:
        print(f"Connecting to {serverName}...")
        async with stdio_client(serverParams) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                serverTools = await session.list_tools()
                mcp_sessions[serverName] = session
                mcp_tools[serverName] = serverTools.tools
                
                print(f"✅ {serverName}: {len(serverTools.tools)} tools available")
                for tool in serverTools.tools:
                    print(f"   - {tool.name}: {tool.description}")
                
                # Keep the connection alive indefinitely
                while True:
                    await asyncio.sleep(1)
                    
    except Exception as e:
        print(f"❌ Failed to connect to {serverName}: {e}")
        # Remove from sessions if connection fails
        if serverName in mcp_sessions:
            del mcp_sessions[serverName]
        if serverName in mcp_tools:
            del mcp_tools[serverName]


async def initialize_mcp_servers():
    """Initialize all MCP servers on startup"""
    mcpServers = loadMCPConfig("mcpServers/mcpConfig.json")
    
    if not mcpServers:
        print("⚠️ No MCP servers configured")
        return
    
    print("🔌 Connecting to MCP servers...\n")
    
    # Create background tasks for each server connection
    for serverName, serverParams in mcpServers.items():
        task = asyncio.create_task(maintain_mcp_connection(serverName, serverParams))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
    
    # Give servers time to connect
    await asyncio.sleep(2)


@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    await initialize_mcp_servers()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("Shutting down MCP connections...")
    for task in background_tasks:
        task.cancel()


@app.get("/")
async def read_root():
    """Serve the HTML frontend"""
    return FileResponse("static/index.html")


@app.get("/api/history")
async def get_history():
    """Get chat message history"""
    messages = db.getMessageHistory()
    return {"messages": messages}


@app.post("/api/clear-history")
async def clear_history():
    """Clear chat history"""
    db.clearHistory()
    return {"status": "success", "message": "History cleared"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat and tool approval"""
    await websocket.accept()
    connection_id = id(websocket)
    pending_approvals[connection_id] = {}
    
    # Queue for chat requests
    chat_queue = asyncio.Queue()
    
    async def message_receiver():
        """Continuously receive messages from WebSocket"""
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                print(f"Received message type: {message_type}")
                
                if message_type == "chat":
                    await chat_queue.put(data)
                    
                elif message_type == "tool_approval":
                    # Handle tool approval response
                    tool_call_id = data.get("tool_call_id")
                    approval_data = data.get("data")
                    
                    print(f"Tool approval received: {tool_call_id}, approved: {approval_data.get('approved')}")
                    print(f"Pending approvals for connection: {list(pending_approvals[connection_id].keys())}")
                    
                    if tool_call_id and tool_call_id in pending_approvals[connection_id]:
                        print(f"Putting approval in queue for {tool_call_id}")
                        await pending_approvals[connection_id][tool_call_id].put(approval_data)
                    else:
                        print(f"Tool call ID {tool_call_id} not found in pending approvals")
                        
        except WebSocketDisconnect:
            print(f"Client {connection_id} disconnected")
        except Exception as e:
            print(f"WebSocket receive error: {e}")
    
    async def message_processor():
        """Process chat messages"""
        try:
            while True:
                data = await chat_queue.get()
                user_message = data.get("message")
                
                if not user_message:
                    continue
                
                # Save user message
                db.saveMessage("user", user_message)
                
                # Process chat with tool calls
                response = await process_chat(websocket, connection_id)
                
                # Send final response
                await websocket.send_json({
                    "type": "message",
                    "role": "assistant",
                    "content": response
                })
                
        except Exception as e:
            print(f"Chat processor error: {e}")
    
    # Start both tasks
    receiver_task = asyncio.create_task(message_receiver())
    processor_task = asyncio.create_task(message_processor())
    
    try:
        # Wait for either task to complete (which means an error occurred)
        done, pending_tasks = await asyncio.wait(
            [receiver_task, processor_task],
            return_when=asyncio.FIRST_COMPLETED
        )
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Cancel both tasks
        receiver_task.cancel()
        processor_task.cancel()
        
        if connection_id in pending_approvals:
            del pending_approvals[connection_id]
        try:
            await websocket.close()
        except:
            pass


async def process_chat(websocket: WebSocket, connection_id: int):
    """Process chat with tool call handling"""
    messages = db.getMessageHistory()
    
    # Prepare OpenAI tools
    openAITools = []
    if mcp_tools:
        for serverName, tools in mcp_tools.items():
            openAITools.extend([mcpToolToOpenAIFormat(tool, serverName) for tool in tools])
    
    maxIteration = 10
    iteration = 0
    
    while iteration < maxIteration:
        iteration += 1
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=openAITools if openAITools else None
        )
        
        message = response.choices[0].message
        
        # No tool calls - return response
        if not message.tool_calls:
            reply = message.content
            db.saveMessage("assistant", reply)
            return reply
        
        # Add assistant message with tool calls
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in message.tool_calls
            ]
        })
        
        # Process each tool call
        for toolCall in message.tool_calls:
            fullToolName = toolCall.function.name
            toolArgs = json.loads(toolCall.function.arguments)
            
            # Parse server and tool name
            if ":" in fullToolName:
                serverName, toolName = fullToolName.split(":", 1)
            elif "_" in fullToolName:
                serverName, toolName = fullToolName.split("_", 1)
            else:
                toolName = fullToolName
                serverName = None
            
            # Create a queue for this specific tool call
            pending_approvals[connection_id][toolCall.id] = asyncio.Queue()
            
            # Request approval from user
            await websocket.send_json({
                "type": "tool_call_request",
                "tool_name": fullToolName,
                "arguments": toolArgs,
                "tool_call_id": toolCall.id
            })
            
            # Wait for approval
            approval_response = await pending_approvals[connection_id][toolCall.id].get()
            approved = approval_response.get("approved", False)
            reason = approval_response.get("reason", "")
            
            # Clean up the queue for this tool call
            del pending_approvals[connection_id][toolCall.id]
            
            if approved:
                # Execute tool
                await websocket.send_json({
                    "type": "tool_executing",
                    "tool_name": fullToolName,
                    "tool_call_id": toolCall.id
                })
                
                try:
                    if serverName and serverName in mcp_sessions:
                        session = mcp_sessions[serverName]
                        result = await session.call_tool(toolName, toolArgs)
                        
                        if hasattr(result, 'content') and isinstance(result.content, list):
                            contentParts = []
                            for item in result.content:
                                if hasattr(item, 'text'):
                                    contentParts.append(item.text)
                                elif hasattr(item, 'type') and item.type == 'text':
                                    contentParts.append(item.text if hasattr(item, 'text') else str(item))
                                else:
                                    contentParts.append(str(item))
                            toolResult = "\n".join(contentParts)
                        else:
                            toolResult = str(result.content)
                        
                        await websocket.send_json({
                            "type": "tool_success",
                            "tool_name": fullToolName,
                            "tool_call_id": toolCall.id
                        })
                    else:
                        toolResult = json.dumps({"error": f"Server '{serverName}' not found"})
                        await websocket.send_json({
                            "type": "tool_error",
                            "tool_name": fullToolName,
                            "tool_call_id": toolCall.id,
                            "error": f"Server '{serverName}' not found"
                        })
                
                except Exception as e:
                    toolResult = json.dumps({"error": str(e)})
                    await websocket.send_json({
                        "type": "tool_error",
                        "tool_name": fullToolName,
                        "tool_call_id": toolCall.id,
                        "error": str(e)
                    })
            else:
                # Tool call denied
                if reason:
                    toolResult = json.dumps({"error": f"Tool call denied by user because: {reason}"})
                else:
                    toolResult = json.dumps({"error": "Tool call denied by user"})
                
                await websocket.send_json({
                    "type": "tool_denied",
                    "tool_name": fullToolName,
                    "tool_call_id": toolCall.id,
                    "reason": reason
                })
            
            # Add tool result to messages
            messages.append({
                "role": "tool",
                "tool_call_id": toolCall.id,
                "content": toolResult
            })
    
    return "Maximum iterations reached. Please try again"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)