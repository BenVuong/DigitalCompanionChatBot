from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from openai import OpenAI
import json
import asyncio
from typing import Optional, Dict
from chatMessage import ChatMessage
from watchfiles import awatch
import os
import glob
import tools
from dotenv import load_dotenv
from tts import TTS
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
ttsClient = OpenAI(
    api_key="none",base_url="http://localhost:7778/v1"
)
ttsGen = TTS(ttsClient) 
client = OpenAI(api_key="none", base_url="http://localhost:5001/v1")
db = ChatMessage("chatMemory.db")

background_tasks = set()
active_websockets: set = set()
pending_approvals: Dict[int, Dict[str, asyncio.Queue]] = {}


async def watch_for_scheduled_prompts():
    """Watch for pending_prompt.json creation and trigger chatbot"""
    print("👀 Watching for scheduled prompts...")
    try:
        async for changes in awatch(".", debounce=200):
            for _, path in changes:
                if os.path.basename(path) == "pending_prompt.json":
                    try:
                        await asyncio.sleep(0.05)
                        with open(path, "r") as f:
                            data = json.load(f)
                            prompts_queue = data.get("prompts", [])
                            system_prompt = prompts_queue[0]["developerPrompt"] if prompts_queue else None
                        
                        if system_prompt is None:
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
    
    # Process without tools for developer prompts
    response = await process_chat(
        message=prompt,
        role="developer",
        use_tools=False,
        websocket=None,
        connection_id=None,
        auto_approve=True
    )
    
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
    
    disconnected = set()
    for ws in active_websockets:
        try:
            await ws.send_json(message_data)
        except Exception as e:
            print(f"Failed to send to client: {e}")
            disconnected.add(ws)
    
    active_websockets.difference_update(disconnected)


@app.on_event("startup")
async def startup_event():
    """Start watching for scheduled prompts"""
    task = asyncio.create_task(watch_for_scheduled_prompts())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    print("✅ Server started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("Shutting down...")
    for task in background_tasks:
        task.cancel()


@app.get("/")
async def read_root():
    return FileResponse("static/index.html")


@app.get("/api/history")
async def get_history():
    messages = db.getMessageHistory()
    return {"messages": messages}


@app.post("/api/clear-history")
async def clear_history():
    db.clearHistory()
    return {"status": "success", "message": "History cleared"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with tool approval"""
    await websocket.accept()
    connection_id = id(websocket)
    pending_approvals[connection_id] = {}
    active_websockets.add(websocket)
    
    # Queue for chat requests
    chat_queue = asyncio.Queue()
    
    async def message_receiver():
        """Continuously receive messages from WebSocket"""
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                
                if message_type == "chat":
                    await chat_queue.put(data)
                    
                elif message_type == "tool_approval":
                    # Handle tool approval response
                    tool_call_id = data.get("tool_call_id")
                    approval_data = data.get("data")
                    
                    print(f"📥 Tool approval received: {tool_call_id}, approved: {approval_data.get('approved')}")
                    
                    if tool_call_id and tool_call_id in pending_approvals[connection_id]:
                        await pending_approvals[connection_id][tool_call_id].put(approval_data)
                    else:
                        print(f"⚠️ Tool call ID {tool_call_id} not found in pending approvals")
                        
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
                
                # Process chat with tools and approval
                await process_chat(
                    message=user_message,
                    role="user",
                    use_tools=True,
                    websocket=websocket,
                    connection_id=connection_id,
                    auto_approve=False
                )
                
                # # Send final response
                # await websocket.send_json({
                #     "type": "message",
                #     "role": "assistant",
                #     "content": response
                # })
                
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
        
        # Cleanup
        active_websockets.discard(websocket)
        if connection_id in pending_approvals:
            del pending_approvals[connection_id]
        try:
            await websocket.close()
        except:
            pass

async def process_chat(
    message: str,
    role: str,
    use_tools: bool,
    websocket: Optional[WebSocket],
    connection_id: Optional[int],
    auto_approve: bool = False
):
    """Process chat with optional tool calling and approval
    
    Args:
        message: The message to process
        role: Message role (user/developer/assistant)
        use_tools: Whether to enable tool calling
        websocket: WebSocket for sending approval requests (None = auto-approve)
        connection_id: Connection ID for tracking approvals (None = auto-approve)
        auto_approve: If True, skip approval requests and execute immediately
    """
    
    system_prompt = (
        "You are a friendly companion named Aelita. You have the ability to see by using "
        "Developer prompts will tell you what you see and you use "
        "then to craft a human-like response for the user.\n\n"
        "IMPORTANT RULES FOR TOOLS:\n"
        "- DO NOT use emojis or markdown formatting in your response to the user"
        "- Avoid using markdown formatting in your text repsonse"
        "- ONLY use tools when the user EXPLICITLY asks you to perform an action\n"
        "- DO NOT use tools for greetings, questions, or casual conversation\n"
        "- DO NOT use tools unless absolutely necessary\n"
        "- If you're unsure whether to use a tool, DON'T use it\n"
        "- Examples of when NOT to use tools: 'hello', 'how are you', 'what can you do', general questions\n"
        "- Examples of when TO use tools: 'add this anime', 'search for anime', 'update my progress'"
    )
    
    tempAudioFiles = glob.glob("static/tts/temp_audio*.mp3")
    print(f"Temp audio files found: {tempAudioFiles}")
        #delete temp files if previous temp files exists

    for temp_file in tempAudioFiles:
        if os.path.exists(temp_file):
            os.remove(temp_file)
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add chat history
    history = db.getMessageHistory()
    if history:
        messages.extend(history)

    # Add current message
    if role == "developer":
        messages.append({"role": role, "content": message})
    else:
        db.saveMessage(role, message)
        messages.append({"role": role, "content": message})

    max_iterations = 10
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Build API call
        api_params = {
            "model": "gpt-4o-mini",
            "messages": messages,
        }
        
        # Only add tools if enabled
        if use_tools:
            api_params["tools"] = tools.toolset
            api_params["tool_choice"] = "auto"
        
        response = client.chat.completions.create(**api_params)
        response_message = response.choices[0].message
        
        # No tool calls - return response
        if not response_message.tool_calls:
            reply = response_message.content
            db.saveMessage("assistant", reply)

            if websocket:
                await websocket.send_json({
                    "type": "message",
                    "role": "assistant",
                    "content": reply
                })

            if websocket:
                asyncio.create_task(generateAndStream(reply, websocket, ttsGen))
            
            return reply
        
        # If tools disabled but got tool calls anyway (shouldn't happen)
        if not use_tools:
            reply = response_message.content or "I cannot use tools right now."
            db.saveMessage("assistant", reply)
            
            return reply
        
        # Add assistant message with tool calls
        messages.append({
            "role": "assistant",
            "content": response_message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in response_message.tool_calls
            ]
        })
        
        # Process each tool call
        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            print(f"🔧 Model wants to call: {function_name}")
            print(f"📝 Arguments: {function_args}")
            
            # Handle approval
            approved = False
            denial_reason = ""
            
            if auto_approve:
                # Auto-approve for scheduled/system messages
                approved = True
                print(f"✅ Auto-approved")
            else:
                # Request user approval via WebSocket
                if not websocket or connection_id is None:
                    # No websocket = deny by default
                    approved = False
                    denial_reason = "No websocket connection for approval"
                else:
                    # Create approval queue for this tool call
                    pending_approvals[connection_id][tool_call.id] = asyncio.Queue()
                    
                    # Request approval from user
                    await websocket.send_json({
                        "type": "tool_call_request",
                        "tool_name": function_name,
                        "arguments": function_args,
                        "tool_call_id": tool_call.id
                    })
                    
                    print(f"⏳ Waiting for user approval...")
                    
                    # Wait for approval response
                    approval_response = await pending_approvals[connection_id][tool_call.id].get()
                    approved = approval_response.get("approved", False)
                    denial_reason = approval_response.get("reason", "")
                    
                    # Clean up
                    del pending_approvals[connection_id][tool_call.id]
                    
                    if approved:
                        print(f"✅ User approved")
                    else:
                        print(f"❌ User denied: {denial_reason}")
            
            # Execute or deny the tool call
            if approved:
                # Notify execution started
                if websocket and not auto_approve:
                    await websocket.send_json({
                        "type": "tool_executing",
                        "tool_name": function_name,
                        "tool_call_id": tool_call.id
                    })
              
                try:    
                    function_response=tools.executeTools(function_name, function_args)
                    print(f"✅ Function executed: {function_response}\n")
                    
                    if websocket and not auto_approve:
                        await websocket.send_json({
                            "type": "tool_success",
                            "tool_name": function_name,
                            "tool_call_id": tool_call.id
                        })
                        
                except Exception as e:
                    function_response = json.dumps({"error": str(e)})
                    print(f"❌ Function error: {e}\n")
                    
                    if websocket and not auto_approve:
                        await websocket.send_json({
                            "type": "tool_error",
                            "tool_name": function_name,
                            "tool_call_id": tool_call.id,
                            "error": str(e)
                        })
            else:
                # Tool call was denied
                if denial_reason:
                    function_response = json.dumps({
                        "error": f"Tool call denied by user: {denial_reason}"
                    })
                else:
                    function_response = json.dumps({
                        "error": "Tool call denied by user"
                    })
                
                if websocket and not auto_approve:
                    await websocket.send_json({
                        "type": "tool_denied",
                        "tool_name": function_name,
                        "tool_call_id": tool_call.id,
                        "reason": denial_reason
                    })
            
            # Add the function response to messages
            # This feedback helps the LLM understand it shouldn't retry
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": function_response
            })
    
    return "Maximum iterations reached. Please try again"


async def generateAndStream(text: str, websocket: WebSocket, ttsGenerator: TTS):
    try:
        chunks = ttsGenerator.chunk_text(text, 500)

        async for audio_chunk_info in ttsGenerator.generateStreaming(chunks, "./static/tts"):
           await asyncio.sleep(0.01)
           
           await websocket.send_json({
                "type": "audio_chunk",
                "chunk_index": audio_chunk_info["chunk_index"],
                "total_chunks": audio_chunk_info["total_chunks"],
                "audio_file": audio_chunk_info["audio_file"]
            })
            # 

        # await websocket.send_json({
        #     "type": "audio_complete",
        #     "audio_file": "audio.mp3"
        # })
    except Exception as e:
        print(f"Error generating audio: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)