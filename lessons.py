import sqlite3

conn = sqlite3.connect("lessons.db")
cur = conn.cursor()

cur.execute("ALTER TABLE lessons ADD COLUMN title TEXT")
conn.commit()
conn.close()
