import sqlite3
class ChatMessage:
    def __init__(self, dbFilename):
        self.conn = sqlite3.connect(dbFilename)
        self.cursor = self.conn.cursor()  
        self.cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
               role TEXT NOT NULL,
               content TEXT NOT NULL
               )
""")
        self.conn.commit()

    def saveMessage(self, role, content):
        self.cursor.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
        self.conn.commit()

    def getMessageHistory(self, limit = 10):
        self.cursor.execute("SELECT role, content FROM messages ORDER by id DESC LIMIT ?", (limit,))
        rows = self.cursor.fetchall()
        return[{"role": role, "content": content} for role, content in rows[::-1]]
    
    def clearHistory(self):
        pass