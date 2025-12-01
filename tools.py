import json
from mcpServers.mcpServer import searchAnime, getAnimeInfo
toolset = [
    {
        "type": "function",
        "function": {
            "name": "searchAnime",
            "description": "Query search a title of an anime. Returns a list of anime that best fit the search term along with its respective mal_id When displaying the search result to the user, just list the anime title and not its mal_id Args: queryTitle: title of anime as search term",
            "parameters": {
                "type": "object",
                "properties": {
                    "queryTitle": {"type": "string", "description": " title of anime as search term"}
                },
                "required": ["queryTitle"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "getAnimeInfo",
            "description": "Search more info about an anime by using it's respective mal_id. To get the anime's respective mal_id use searchAnime tool.This tool will return the anime's title, type, number of episodes, its score, when it premired, and synopsis Args:mal_id: the anime's respective mal_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "mal_id": {"type": "integer", "description": "the anime's respective mal_id"}
                },
                "required": ["mal_id"]
            }
        }
    }
]

def executeTools(name, args):

    match name:
        case "searchAnime":
            return(searchAnime(**args))
        case "getAnimeInfo":
            return(getAnimeInfo(**args))
        case _:
            return(json.dumps({"error": "Unknown function"}))
    