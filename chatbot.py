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
client = OpenAI(api_key="none",base_url="http://localhost:5001/v1")
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

def mcpToolToOpenAIFormat(mcpTool):
    return{
        "type": "function",
        "function":{
            "name": mcpTool.name,
            "description": mcpTool.description,
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
            return True
        elif response in ['no','n']:
            return False    
        else:
            print("Please enter 'yes' or 'no'")
async def chat(userInput, session, mcpTools):
    saveMessage("user", userInput)
    messages = getMessageHistory()

    openAITools = [mcpToolToOpenAIFormat(tool) for tool in mcpTools]
    
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
            toolName = toolCall.function.name
            toolArgs = json.loads(toolCall.function.arguments)

            if approveToolCall(toolName,toolArgs):
                print(f"Executing {toolName}...")

                try:
                    result = await session.call_tool(toolName,toolArgs)
                    
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

                    print(f"Tool executed successfully")
                
                except Exception as e:
                    toolResult = json.dumps({"error":str(e)})
                    print(f"Tool execution failed: {e}")

            else:
                print(f"Tool called denied by user")
                toolResult = json.dumps({"error":"Tool call denied by user"})

            messages.append({
                "role": "tool",
                "tool_call_id": toolCall.id,
                "content": toolResult
            })
    return "Maximum iterations reached. Please try again"

async def main():
    # Create server parameters
    server_params = StdioServerParameters(
        command="python",
        args=["mcpServer.py"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("✅ Connected to MCP server!")
            print(f"Found {len(tools.tools)} tools:")
            for tool in tools.tools:
                print(f" - {tool.name}: {tool.description}")
                # print(f"{tool.inputSchema}")

            print("\n" + "="*60)
            print("Chatbot Ready")
            print("="*60+"\n")
                
            while True:
                user_input = input("You: ").strip()
                
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                
                if not user_input:
                    continue
                
                reply = await chat(user_input, session, tools.tools)
                print(f"\nAssistant: {reply}\n")
if __name__ == "__main__":
    asyncio.run(main())