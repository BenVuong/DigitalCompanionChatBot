import json
from mcpManager import StdioServerParameters

def loadMCPConfig(configPath = "mcpConfig.json"):
    try:
        with open(configPath, "r") as f:
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
        print(f"Config file '{configPath}' not found. Now using empty config")
        return{}    
    except json.JSONDecodeError as e:
        print(f"Error parsing config file: {e}")
        return{}