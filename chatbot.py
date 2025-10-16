from openai import OpenAI
import sqlite3
import json
from mcp import StdioServerParameters
from mcp.client.session import ClientSession  
from mcp.client.stdio import stdio_client
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from watchfiles import awatch
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
conn = sqlite3.connect("chatMemory.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
               role TEXT NOT NULL,
               content TEXT NOT NULL
               )
""")
conn.commit()

def saveMessage(role, content):
    cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()

def getMessageHistory(limit = 10):
    cursor.execute("SELECT role, content FROM messages ORDER by id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()

    return[{"role": role, "content": content} for role, content in rows[::-1]]

def loadMCPConfig(configFilePath = "mcpConfig.json"):
    try:
        with open(configFilePath, "r") as f:
            config = json.load(f)

        mcpServers = {}
        for name, serverConfig in config.get("mcpServers", {}).items():
            mcpServers[name] = StdioServerParameters(
                command=serverConfig["command"],
                args=serverConfig.get("args", []),
                env=serverConfig.get("env",None)
            )
        return mcpServers
    except FileNotFoundError:
        print(f"Config file '{configFilePath}' not found. Now using empty config")
        return{}    
    except json.JSONDecodeError as e:
        print(f"Error parsing config file: {e}")
        return{}

def mcpToolToOpenAIFormat(mcpTool, serverName):
    safe_name = f"{serverName}_{mcpTool.name}".replace(":", "_")
    return{
        "type": "function",
        "function":{
            "name": safe_name,
            "description": f"[{serverName}] {mcpTool.description}",
            "parameters": mcpTool.inputSchema
            
        }
    }

def approveToolCall(toolName, arguments):
    print("\n" + "="*60)
    print(f"Tool Call Request: {toolName}")
    print("="*60)
    print(f"Arugments: {json.dumps(arguments, indent=2)}")
    print("="*60)
    
    while True:
        response = input("\nApprove this tool call? (yes/no): ").lower().strip()
        if response in ['yes', 'y']:
            reason = ""
            return True, reason
        elif response in ['no','n']:
            #add extra option to add a reason to give the llm more context to why it was not approved
            giveReason = input("Give a reason why tool call was denied? (yes/no): ").lower().strip()
            if giveReason in ['yes','y']:
                reason = input("Reason: ")
                return False, reason
            reason = ""
            return False, reason    
        else:
            print("Please enter 'yes' or 'no'")

async def watchForScheduledPrompts(callback):
    """Watch for pending_prompt.json creation and call callback(prompt)."""
    async for changes in awatch(".", debounce=200):
        for _, path in changes:  # use _ to ignore the change type
            if os.path.basename(path) == "pending_prompt.json":
                try:
                    await asyncio.sleep(0.05)  # small delay to ensure file is written
                    with open(path, "r") as f:
                        data = json.load(f)
                    os.remove(path)
                    system_prompt = data.get("systemPrompt")
                    if system_prompt:
                        await callback(system_prompt)
                except Exception as e:
                    print(f"⚠️ Error handling scheduled prompt: {e}")


    
async def chat(input, role, sessionsDict, toolsDict):
    
    saveMessage(role, input)
    messages = getMessageHistory()

    openAITools = toolsDict
    openAITools = []
    if toolsDict != None:
        openAITools = []
        for serverName, tools in toolsDict.items():
            openAITools.extend([mcpToolToOpenAIFormat(tool, serverName) for tool in tools])
    
    maxIteration = 10
    iteration = 0

    while iteration < maxIteration:
        iteration +=1

        response =client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=openAITools if openAITools else None
        )

        message = response.choices[0].message

        if not message.tool_calls:
            reply = message.content
            saveMessage("assistant", reply)
            return reply
        
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls":[
                {
                    "id": tc.id,
                    "type": "function",
                    "function":{
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in message.tool_calls
            ]
        })

        for toolCall in message.tool_calls:
            fullToolName = toolCall.function.name
            toolArgs = json.loads(toolCall.function.arguments)

            if ":" in fullToolName:
                serverName, toolName = fullToolName.split(":", 1)
            elif "_" in fullToolName:
                serverName, toolName = fullToolName.split("_", 1)
            else:
                toolName = fullToolName
                serverName = None

            approvalResult, reason = approveToolCall(fullToolName,toolArgs)
            
            if approvalResult:
                print(f"✅ Executing {fullToolName}...")
                try:
                    # Find the correct session
                    if serverName and serverName in sessionsDict:
                        session = sessionsDict[serverName]
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
                        
                        print(f"✅ Tool executed successfully")
                    else:
                        toolResult = json.dumps({"error": f"Server '{serverName}' not found"})
                        print(f"❌ Server not found: {serverName}")
                
                except Exception as e:
                    toolResult = json.dumps({"error": str(e)})
                    print(f"❌ Tool execution failed: {e}")

            else:
                print(f"❌ Tool call denied by user")
                if reason != "":
                    toolResult = json.dumps({"error": f"Tool call denied by user because user said: {reason}"})
                else:
                    toolResult = json.dumps({"error": "Tool call denied by user"})
            messages.append({
                "role": "tool",
                "tool_call_id": toolCall.id,
                "content": toolResult
            })
    return "Maximum iterations reached. Please try again"

async def connect_to_server(serverName, serverParams):
    """Connect to a single MCP server and return session info"""
    try:
        print(f"Connecting to {serverName}...")
        read, write = await stdio_client(serverParams).__aenter__()
        session = ClientSession(read, write)
        await session.initialize()
        
        serverTools = await session.list_tools()
        print(f"✅ {serverName}: {len(serverTools.tools)} tools available")
        for tool in serverTools.tools:
            print(f"   - {tool.name}: {tool.description}")
        
        return serverName, session, serverTools.tools, None
    except Exception as e:
        print(f"❌ Failed to connect to {serverName}: {e}")
        return serverName, None, None, str(e)

async def run_with_servers(mcpServers):
    """Run the chatbot with all MCP servers connected"""
    sessions = {}
    tools = {}
    
    # Create async context managers for all servers
    server_contexts = []
    for serverName, serverParams in mcpServers.items():
        server_contexts.append((serverName, stdio_client(serverParams)))
    
    async def handleScheduledPrompt(prompt):
        print(f"\n🕐 System Trigger: {prompt}")
        reply = await chat(prompt, "system",sessions, None)
        print(f"\nAssistant: {reply}\n")

    # Use nested async with to keep all connections alive
    async def connect_all(contexts, index=0):
        if index >= len(contexts):
            # All connected, now run the chat loop
            if not sessions:
                print("❌ No servers connected successfully.")
                return
            
            print("\n" + "="*60)
            print("Chatbot Ready! Type 'quit' to exit.")
            print("="*60 + "\n")
            
            asyncio.create_task(watchForScheduledPrompts(handleScheduledPrompt))
            # Chat loop
            while True:

                
                user_input = await asyncio.to_thread(input, "You: ")
                user_input = user_input.strip()
                
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                
                if not user_input:
                    continue
                
                reply = await chat(user_input, "user", sessions, tools)
                print(f"\nAssistant: {reply}\n")
            return
        
        serverName, client_ctx = contexts[index]
        try:
            print(f"Connecting to {serverName}...")
            async with client_ctx as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    serverTools = await session.list_tools()
                    sessions[serverName] = session
                    tools[serverName] = serverTools.tools
                    
                    print(f"✅ {serverName}: {len(serverTools.tools)} tools available")
                    for tool in serverTools.tools:
                        print(f"   - {tool.name}: {tool.description}")
                    
                    # Recursively connect to next server
                    await connect_all(contexts, index + 1)
        except Exception as e:
            print(f"❌ Failed to connect to {serverName}: {e}")
            import traceback
            traceback.print_exc()
            # Continue to next server even if this one failed
            await connect_all(contexts, index + 1)
    
    await connect_all(server_contexts)

async def main():
    # Load MCP servers from config
    mcpServers = loadMCPConfig("mcpServers/mcpConfig.json")
    
    if not mcpServers:
        print("❌ No MCP servers configured. Please create mcp_config.json")
        return
    
    print("🔌 Connecting to MCP servers...\n")
    
    await run_with_servers(mcpServers)
if __name__ == "__main__":
    asyncio.run(main())