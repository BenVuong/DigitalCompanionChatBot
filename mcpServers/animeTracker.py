import sqlite3
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Anime-Episodes-Tracker")
db = "/home/ben/chatbot/mydatabase.db"
# @mcp.resource("Anime-Episodes-Tracker://anime",
#               description=("A table of all tracked anime. "
#                           "Each row has the following fields:\n"
#                           "- id: integer, unique identifier for the anime entry.\n"
#                           "- title: text, the name of the anime.\n"
#                           "- episodesWatched: integer, how many episodes the user has watched.\n"
#                           "- totalEpisodes: integer, the total number of episodes in the series (if known)."))
@mcp.tool()
def getAnimeTable() ->str:
    "This tool returns the animes tracked within the database. You can use this tool to see how the anime title is formatted so you can use the title to update the anime"
    db_path = "mydatabase.db"

    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, episodesWatched, totalEpisodes FROM anime")
    rows = cursor.fetchall()
    conn.close()  # Fixed: added parentheses
    
    header = "id | title | episodes watched | total episodes\n"
    data = "\n".join(
        [f"{r[0]} | {r[1]} | {r[2]} | {r[3]}" for r in rows]
    )
    data = "getAnimeTable executed sucessfully with the return info: \n"+ header + data + "\n"f"Database location: {os.path.abspath(db_path)}"
    
    # return TextResourceContents(
    #     uri="Anime-Episodes-Tracker://anime",
    #     mimeType="text/plain",
    #     text=data
    # )

    return(str(data))

def checkIfTableExists():
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='anime'")
    result = cursor.fetchone()
    if result is None:
        cursor.execute("CREATE TABLE anime (id INTEGER PRIMARY KEY, title TEXT UNIQUE, episodesWatched INTEGER, totalEpisodes INTEGER)")
    conn.commit()
    conn.close() 

@mcp.tool()
def insertNewAnime(title: str, episodesWatched: int, totalEpisodes: int):
    """
    This tool allows to enter in a new anime into the database.
    If user does not give you the anime's total episode count, then use the searchAnime and getAnimeInfo tools to get the episode count. 
    If the searchAnime and getAnimeInfo tool is not available then ask the user for the information
    Args:
        title: title of the anime to entered in. This parameter should only be the show's title and not include extra information like this: (TV, 2004)
        episodesWatched: the number of episodes the user have already watched
        totalEpisodes: the total number of episodes in the anime series
    """
    checkIfTableExists()
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    cursor.execute("INSERT INTO anime (title, episodesWatched, totalEpisodes) VALUES (?, ?, ?)",
                   (title, episodesWatched, totalEpisodes))
    conn.commit()
    conn.close() 
    return f"Successfully added '{title}' to the database."

@mcp.tool()
def updateAnimeProgress(title: str, episodesWatched: int):
    """
    Tool to update the number of episodes watched for an anime.
    Only ever use this tool when the user asks so. If you want to use this tool ask the user
    Args:
        title: title of the anime to update
        episodesWatched: the new number of episodes watched
    """
    checkIfTableExists()
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    
    # Using parameterized query to prevent SQL injection
    cursor.execute("UPDATE anime SET episodesWatched = ? WHERE title = ?", (episodesWatched, title))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    
    if rows_affected > 0:
        return f"Successfully updated '{title}' to {episodesWatched} episodes watched."
    else:
        return f"No anime found with title '{title}'."

if __name__ == "__main__":
    mcp.run(transport="stdio")