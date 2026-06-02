import os
import sqlite3

# ===============================
# Base Directory
# ===============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===============================
# Database Paths
# ===============================
ADMIN_DB = os.path.join(BASE_DIR, "admins.db")
USERS_DB = os.path.join(BASE_DIR, "users.db")
COURSES_DB = os.path.join(BASE_DIR, "courses.db")
LESSONS_DB = os.path.join(BASE_DIR, "lessons.db")
QUIZ_DB = os.path.join(BASE_DIR, "quiz.db")        # ✅ New for quiz questions
NOTES_DB = os.path.join(BASE_DIR, "notes.db")        # ✅ New for notes
COURSES_EXCEL_DB = os.path.join(BASE_DIR, "courses_excel.db")

# ===============================
# DB Helpers
# ===============================
def query_all(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def query_one(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.close()
    return row

def execute(db, sql, params=()):
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id

def create_table(db, sql):
    """Helper to create tables."""
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    conn.close()

# ===============================
# Admin Authentication
# ===============================
def create_admin_table():
    create_table(ADMIN_DB, """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def admin_exists(username):
    return query_one(ADMIN_DB, "SELECT id FROM admins WHERE username=?", (username,))

def create_admin(username, password):
    return execute(ADMIN_DB, "INSERT INTO admins (username, password) VALUES (?, ?)", (username, password))

def get_admin(username):
    return query_one(ADMIN_DB, "SELECT * FROM admins WHERE username=?", (username,))

# ===============================
# User Management
# ===============================
def get_all_users():
    return query_all(USERS_DB, "SELECT * FROM users ORDER BY id DESC")

def delete_user(user_id):
    return execute(USERS_DB, "DELETE FROM users WHERE id=?", (user_id,))

# ===============================
# Course Management
# ===============================
def get_all_courses():
    return query_all(COURSES_DB, "SELECT * FROM courses ORDER BY id DESC")

def delete_course(course_id):
    return execute(COURSES_DB, "DELETE FROM courses WHERE id=?", (course_id,))

# ===============================
# Lesson Management
# ===============================
def get_all_lessons():
    return query_all(LESSONS_DB, "SELECT * FROM lessons ORDER BY id DESC")

def delete_lesson(lesson_id):
    return execute(LESSONS_DB, "DELETE FROM lessons WHERE id=?", (lesson_id,))

# ===============================
# Quiz Management
# ===============================
def create_quiz_questions_table():
    create_table(QUIZ_DB, """
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            explanation TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def get_all_quizzes():
    return query_all(QUIZ_DB, """
        SELECT 
            topic AS title,
            COUNT(*) AS total_questions,
            MAX(created_at) AS created_at
        FROM quiz_questions
        GROUP BY topic
        ORDER BY created_at DESC
    """)

def delete_quiz(topic):
    return execute(QUIZ_DB, "DELETE FROM quiz_questions WHERE topic=?", (topic,))

# ===============================
# Notes Management
# ===============================
def create_notes_table():
    create_table(NOTES_DB, """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,   -- For downloads
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def get_all_notes():
    return query_all(NOTES_DB, "SELECT * FROM notes ORDER BY id DESC")

def delete_note(note_id):
    return execute(NOTES_DB, "DELETE FROM notes WHERE id=?", (note_id,))

# ===============================
# External Courses
# ===============================
def get_external_courses():
    return query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel ORDER BY rowid DESC")

# ===============================
# AUTO INITIALIZE TABLES
# ===============================
create_admin_table()
create_quiz_questions_table()
create_notes_table()
