import sqlite3

conn = sqlite3.connect('users.db')  # your database file
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    email TEXT
)
''')

conn.commit()
conn.close()
print("Users table created successfully!")
