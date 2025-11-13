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
from watchfiles import awatch
import os
from dotenv import load_dotenv
load_dotenv()
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
active_websockets: set = set()  # Track all active WebSocket connections


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
    
    # Start watching for scheduled prompts
    task = asyncio.create_task(watch_for_scheduled_prompts())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    await initialize_mcp_servers()


async def watch_for_scheduled_prompts():
    """Watch for pending_prompt.json creation and trigger chatbot"""
    print("👀 Watching for scheduled prompts...")
    try:
        async for changes in awatch(".", debounce=200):
            for _, path in changes:  # use _ to ignore the change type
                if os.path.basename(path) == "pending_prompt.json":
                    try:
                        await asyncio.sleep(0.05)  # small delay to ensure file is written
                        with open(path, "r") as f:
                            data = json.load(f)
                            prompts_queue = data.get("prompts", [])
                            system_prompt = prompts_queue[0]["systemPrompt"] if prompts_queue else None
                        
                        if system_prompt == None:
                            continue

                        print(f"\n🕐 System Trigger: {system_prompt}")
                        
                        # Clear the file
                        with open(path, "w") as f:
                            prompts_queue = prompts_queue[1:]
                            json.dump({"prompts": prompts_queue}, f)
                       
                        # Handle the scheduled prompt
                        await handle_scheduled_prompt(system_prompt)
                        
                    except Exception as e:
                        print(f"⚠️ Error handling scheduled prompt: {e}")
    except Exception as e:
        print(f"❌ Error in scheduled prompt watcher: {e}")


async def handle_scheduled_prompt(prompt: str):
    """Process a scheduled prompt and broadcast to all connected clients"""
    if not prompt:
        return
    
    # Process using the shared chat function (no approval needed, pass None for websocket)
    response = await process_chat(prompt, "system",False,None, None, auto_approve=False)
    
    # Broadcast to all connected clients
    await broadcast_scheduled_message(prompt, response)
    
    print(f"\nAssistant: {response}\n")
    return response


async def broadcast_scheduled_message(system_prompt: str, response: str):
    """Broadcast scheduled message to all connected WebSocket clients"""
    message_data = {
        "type": "scheduled_message",
        "system_prompt": system_prompt,
        "response": response
    }
    
    # Send to all connected clients
    disconnected = set()
    for ws in active_websockets:
        try:
            await ws.send_json(message_data)
        except Exception as e:
            print(f"Failed to send to client: {e}")
            disconnected.add(ws)
    
    # Remove disconnected clients
    active_websockets.difference_update(disconnected)


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
    
    # Add to active websockets
    active_websockets.add(websocket)
    
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
                
                
                # Process chat with tool calls
                response = await process_chat(user_message, "user", True,websocket, connection_id)
                
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
        
        # Remove from active websockets
        active_websockets.discard(websocket)
        
        if connection_id in pending_approvals:
            del pending_approvals[connection_id]
        try:
            await websocket.close()
        except:
            pass


async def process_chat(message: str, role: str, tools: bool, websocket: Optional[WebSocket], connection_id: Optional[int], auto_approve: bool = False):
    """Process chat with tool call handling
    
    Args:
        websocket: WebSocket connection for user approval (None for auto-approve)
        connection_id: Connection ID for tracking approvals (None for auto-approve)
        auto_approve: If True, automatically approve all tool calls without user interaction
    """

    systemPrompt = "You are a friendly companion named Aelita"
    
    if role == "system":
        messages = db.getMessageHistory()
        messages.append({"role":role, "content": message})
    else:
        db.saveMessage(role, message)
        messages = db.getMessageHistory()

    # Prepare OpenAI tools
    openAITools = []
    if mcp_tools:
        for serverName, tools in mcp_tools.items():
            openAITools.extend([mcpToolToOpenAIFormat(tool, serverName) for tool in tools])
    
    maxIteration = 10
    iteration = 0
    if tools == False:
        openAITools = None
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
            
            # Handle approval based on mode
            if auto_approve:
                # Auto-approve for scheduled messages
                approved = True
                reason = ""
                print(f"⚙️ Executing {fullToolName} (auto-approved)...")
            else:
                # Request approval from user via WebSocket
                if not websocket or connection_id is None:
                    # Safety check
                    approved = False
                    reason = "No websocket connection"
                else:
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
                if websocket and not auto_approve:
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
                        
                        if websocket and not auto_approve:
                            await websocket.send_json({
                                "type": "tool_success",
                                "tool_name": fullToolName,
                                "tool_call_id": toolCall.id
                            })
                        else:
                            print(f"✅ Tool executed successfully")
                    else:
                        toolResult = json.dumps({"error": f"Server '{serverName}' not found"})
                        if websocket and not auto_approve:
                            await websocket.send_json({
                                "type": "tool_error",
                                "tool_name": fullToolName,
                                "tool_call_id": toolCall.id,
                                "error": f"Server '{serverName}' not found"
                            })
                        else:
                            print(f"❌ Server not found: {serverName}")
                
                except Exception as e:
                    toolResult = json.dumps({"error": str(e)})
                    if websocket and not auto_approve:
                        await websocket.send_json({
                            "type": "tool_error",
                            "tool_name": fullToolName,
                            "tool_call_id": toolCall.id,
                            "error": str(e)
                        })
                    else:
                        print(f"❌ Tool execution failed: {e}")
            else:
                # Tool call denied
                if reason:
                    toolResult = json.dumps({"error": f"Tool call denied by user because: {reason}"})
                else:
                    toolResult = json.dumps({"error": "Tool call denied by user"})
                
                if websocket and not auto_approve:
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