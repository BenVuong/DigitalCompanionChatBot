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
        with open(filename, "w") as f:
            json.dump({"systemPrompt":prompt}, f)
        print(f"Timer expired - wrote {filename}")
    except Exception as e:
        print(f"failed to write prompt file: {e}")

if __name__ == "__main__":
    mcp.run(transport="stdio")