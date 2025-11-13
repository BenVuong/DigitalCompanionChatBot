import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("timerServer")

@mcp.tool()
async def scheduleMessage(delaySeconds: int, systemPrompt: str):
    """
    This tool schedules a message to appear later.
    the systemPrompt should be like "Tell the user to watch some anime now"
    Arguments:
      delaySeconds: Number of seconds to wait
      systemPrompt: The system message the chatbot should receive when time expires, should be like "remind the user to enjoy some anime"
    """
    asyncio.create_task(backgroundTimer(delaySeconds, systemPrompt))
    return(f"scheduleMessage tool executed successfully. Message scheduled in {delaySeconds} seconds")


async def backgroundTimer(delay, prompt):
    await asyncio.sleep(delay)
    filename= "pending_prompt.json"
    try:
        if os.path.exists(filename):
            with open(filename, "r") as f:
                data = json.load(f)
                prompts_queue = data.get("prompts", [])
        else:
            prompts_queue = []
        
        # Add new prompt to the queue
        prompts_queue.append({
            "systemPrompt": prompt,
            "timestamp": asyncio.get_event_loop().time()  # Optional: track when added
        })
        
        # Write updated queue back to file
        with open(filename, "w") as f:
            json.dump({"prompts": prompts_queue}, f, indent=2)
        
        print(f"Timer expired - added prompt to {filename} (total: {len(prompts_queue)})")
        
    except Exception as e:
        print(f"Failed to write prompt file: {e}")

if __name__ == "__main__":
    mcp.run(transport="stdio")