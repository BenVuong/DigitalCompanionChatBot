import json
from mcp import StdioServerParameters

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
