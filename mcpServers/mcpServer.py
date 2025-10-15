from jikanpy import Jikan
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("anime")
jikan = Jikan()


@mcp.tool()
def searchAnime(queryTitle: str) -> str:
    """Query search a title of an anime. Returns a list of anime that best fit the search term along with its respective mal_id
        When displaying the search result to the user, just list the anime title and not its mal_id
        Args:
            queryTitle: title of anime as search term
       """
    result = "searchAnime Tool used sucessfully and has returned the given information: \n"
    
    anime = jikan.search("anime", queryTitle)
    for x in anime["data"]:
        if x["year"] == None:
            year = x["aired"]["prop"]["from"]["year"]
        else:
            year = x["year"]
        title = x["title"]
        if x["title_english"] != None:
            title = x["title_english"]
        result += ("title: "+title+ " ("+ x["type"]+", "+str(year)+") mal_id: " + str(x["mal_id"]) + "\n" )
    return(result)
    
@mcp.tool()
def getAnimeInfo(mal_id: int) -> str:
    """Search more info about an anime by using it's respective mal_id. 
        To get the anime's respective mal_id use searchAnime tool.
        This tool will return the anime's title, type, number of episodes, its score, when it premired, and synopsis
        Args:
            mal_id: the anime's respective mal_id
    """
    info = "getAnimeInfo Tool used sucessfully and has returned the given information: \n"
    anime = jikan.anime(id=mal_id)
    anime = anime["data"]
    if anime["title_english"] != None:
        info+= ("Title: " + anime["title_english"]+"\n")
    else:
        info+=("Title: " + anime["title"] + "\n")
    info+=("Type: " + anime["type"] + "\n")
    info+=("Score: " +str(anime["score"])+"/10 \n")
    
    if (anime["type"] == "TV" or anime["type"] == "OVA"):
        info+=("Premired in "+ anime["season"]+" " +str(anime["year"])+"\n")
        info+=("Number of Episodes: " + str(anime["episodes"])+"\n")
    else:
        info+=("Premired in "+ str(anime["aired"]["prop"]["from"]["year"])+"\n")
    if (anime["synopsis"] != None):
        info+=("Synopsis: " + anime["synopsis" ]+"\n")
     
    return(info)

    

if __name__ == "__main__":
    mcp.run(transport="stdio")

