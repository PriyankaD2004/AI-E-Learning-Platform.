#!/usr/bin/env python3
"""
Initialize all SQLite databases for AI E-Learning Platform.
Run this file ONLY ONCE to create tables.
"""

import sqlite3
import os
import pandas as pd
from db_config import *   # ✅ single source of DB paths


def create_table(db_path, sql):
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql)
        conn.commit()


def create_users_table():
    create_table(USERS_DB, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_courses_table():
    create_table(COURSES_DB, """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_courses_excel_table():
    create_table(COURSES_EXCEL_DB, """
        CREATE TABLE IF NOT EXISTS courses_excel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_title TEXT NOT NULL,
            instructor_name TEXT,
            skills TEXT,
            duration TEXT,
            lectures INTEGER,
            ratings REAL,
            reviews INTEGER,
            level TEXT,
            link TEXT,
            thumbnail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_lessons_table():
    create_table(LESSONS_DB, """
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER,
            title TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ----------------------------------------------------
# Quiz → quiz.db ✅
# ----------------------------------------------------
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



# ----------------------------------------------------
# Notes → notes.db ✅
# ----------------------------------------------------
def create_notes_table():
    create_table(NOTES_DB, """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,       -- Added filename for downloads
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ----------------------------------------------------
# Helper
# ----------------------------------------------------
def safe_value(val, default=""):
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    return val



def load_courses_from_excel(file_path="courses.xlsx"):
    if not os.path.exists(file_path):
        print("⚠ Excel file not found")
        return

    df = pd.read_excel(file_path)
    df.columns = df.columns.str.strip()

    with sqlite3.connect(COURSES_EXCEL_DB) as conn:
        for _, row in df.iterrows():
            conn.execute("""
                INSERT INTO courses_excel
                (course_title, instructor_name, skills, duration, lectures,
                 ratings, reviews, level, link, thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                safe_value(row["Course Title"]),
                safe_value(row["Course Instructor Name"]),
                safe_value(row["Skills You Gain"]),
                safe_value(row["Total Course Hours"]),
                int(row["Total No of Lectures"]),
                float(row["Ratings"]),
                int(row["Total Reviews"]),
                safe_value(row["Course Levels"]),
                safe_value(row["Course Links"]),
                safe_value(row["Course Thumbnail Image"]),
            ))
        conn.commit()

    print("✅ Excel imported successfully")


if __name__ == "__main__":
    create_users_table()
    create_courses_table()
    create_courses_excel_table()
    create_lessons_table()
    create_quiz_questions_table()
    create_notes_table()

    if os.path.exists("courses.xlsx"):
        load_courses_from_excel("courses.xlsx")
