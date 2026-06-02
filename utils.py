#!/usr/bin/env python3
"""
Helper functions for AI E-Learning Platform
Includes database helpers, quiz functions, notes, and Gemini AI helpers.
"""

import os
import sqlite3
import json
import traceback
import time
from dotenv import load_dotenv
import google.generativeai as genai
from db_config import *   # ✅ SINGLE SOURCE OF DB PATHS

# ----------------------------------------------------
# Load GEMINI API Key
# ----------------------------------------------------
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY or GOOGLE_API_KEY not found in .env")

genai.configure(api_key=API_KEY)

try:
    GEMINI_MODEL = genai.GenerativeModel("gemini-2.5-flash")
except Exception:
    GEMINI_MODEL = genai.GenerativeModel("gemini-1.5-flash")

# ----------------------------------------------------
# Database Helpers (Safe + Dict Rows)
# ----------------------------------------------------
def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def query_all(db_path, sql, params=()):
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(db_path, sql, params=()):
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def execute(db_path, sql, params=()):
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        if sql.strip().upper().startswith("INSERT"):
            return cur.lastrowid
        return cur.rowcount
    finally:
        conn.close()


def table_exists(table_name, db_path):
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


# ----------------------------------------------------
# Safe Value Helper
# ----------------------------------------------------
def safe_value(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        return value if value else default
    return value


# ----------------------------------------------------
# Courses Helper
# ----------------------------------------------------
def get_course_description(topic):
    row = query_one(
        COURSES_DB,
        "SELECT description FROM courses WHERE topic=?",
        (topic,)
    )
    return row["description"] if row else None


# ----------------------------------------------------
# JSON Cleaner (Gemini)
# ----------------------------------------------------
def parse_gemini_json(raw_text):
    try:
        if not raw_text:
            return None

        text = raw_text.strip()

        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

        return json.loads(text)

    except Exception:
        print("❌ JSON parse error:")
        print(traceback.format_exc())
        return None


# ----------------------------------------------------
# Safe GEMINI Calls
# ----------------------------------------------------
def call_gemini_safe(prompt, is_json=False):
    try:
        response = GEMINI_MODEL.generate_content(prompt)

        text = ""
        if hasattr(response, "text"):
            text = response.text
        elif hasattr(response, "candidates") and response.candidates:
            text = response.candidates[0].content.parts[0].text

        text = (text or "").strip()
        return parse_gemini_json(text) if is_json else text

    except Exception as e:
        print("⚠ Gemini API Error:", e)
        return None


def call_gemini_with_backoff(prompt, is_json=False, retries=3, delay=2):
    for _ in range(retries):
        result = call_gemini_safe(prompt, is_json)
        if result is not None:
            return result
        time.sleep(delay)
        delay *= 2
    return None




# ==============================
# QUIZ HELPERS
# ==============================

def insert_quiz_question(topic, question, options, correct_answer, explanation=""):
    """
    Insert a new quiz question into the database.
    
    :param topic: Topic name (string)
    :param question: Question text (string)
    :param options: List of possible answers
    :param correct_answer: The correct answer (must be in options)
    :param explanation: Optional explanation for the answer
    """
    if not isinstance(options, list) or len(options) == 0:
        raise ValueError("Options must be a non-empty list.")
    if correct_answer not in options:
        raise ValueError("Correct answer must be one of the options.")
    
    return execute(
        QUIZ_DB,
        """
        INSERT INTO quiz_questions
        (topic, question, options_json, correct_answer, explanation)
        VALUES (?, ?, ?, ?, ?)
        """,
        (topic, question, json.dumps(options), correct_answer, explanation)
    )


def get_quiz_by_topic(topic):
    """
    Retrieve all quiz questions for a given topic.
    
    :param topic: Topic name
    :return: List of dictionaries with question, options, correct_answer, explanation
    """
    rows = query_all(
        QUIZ_DB,
        """
        SELECT question, options_json, correct_answer, explanation
        FROM quiz_questions
        WHERE topic=?
        ORDER BY id ASC
        """,
        (topic,)
    )

    return [
        {
            "question": safe_value(r["question"], ""),
            "options": json.loads(r["options_json"] or "[]"),
            "correct_answer": safe_value(r["correct_answer"], ""),
            "explanation": safe_value(r["explanation"], "")
        }
        for r in rows
    ]


def delete_quiz_by_topic(topic):
    """
    Delete all quiz questions for a given topic.
    
    :param topic: Topic name
    """
    return execute(
        QUIZ_DB,
        "DELETE FROM quiz_questions WHERE topic=?",
        (topic,)
    )


def get_random_quiz_question(topic):
    """
    Fetch a single random quiz question for a given topic.
    
    :param topic: Topic name
    :return: Dictionary with question, options, correct_answer, explanation
    """
    rows = query_all(
        QUIZ_DB,
        """
        SELECT question, options_json, correct_answer, explanation
        FROM quiz_questions
        WHERE topic=?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (topic,)
    )

    if not rows:
        return None

    r = rows[0]
    return {
        "question": safe_value(r["question"], ""),
        "options": json.loads(r["options_json"] or "[]"),
        "correct_answer": safe_value(r["correct_answer"], ""),
        "explanation": safe_value(r["explanation"], "")
    }

def get_quiz_topics_summary():
    """
    Returns list of topics with number of questions
    """
    return query_all(
        QUIZ_DB,
        """
        SELECT 
            topic,
            COUNT(*) AS total_questions,
            MAX(created_at) AS created_at
        FROM quiz_questions
        GROUP BY topic
        ORDER BY created_at DESC
        """
    )

# ====================================================
# NOTES HELPERS → notes.db ✅
# ====================================================

# ------------------------------
# Insert a new note
# ------------------------------
def insert_note(topic, title, content, filename):
    """
    Inserts a note into the notes table. `filename` is required for download.
    """
    return execute(
        NOTES_DB,
        """
        INSERT INTO notes (topic, title, filename, content)
        VALUES (?, ?, ?, ?)
        """,
        (topic, title, filename, content)
    )

# ------------------------------
# Get notes by topic
# ------------------------------
def get_notes_by_topic(topic):
    """
    Returns all notes for a given topic, ordered by newest first.
    """
    return query_all(
        NOTES_DB,
        """
        SELECT id, topic, title, filename, content, created_at
        FROM notes
        WHERE topic=?
        ORDER BY id DESC
        """,
        (topic,)
    )

# ------------------------------
# Get all notes
# ------------------------------
def list_all_notes():
    """
    Returns all notes ordered by creation date descending.
    """
    return query_all(
        NOTES_DB,
        "SELECT id, topic, title, filename, content, created_at FROM notes ORDER BY created_at DESC"
    )

# ------------------------------
# Update an existing note
# ------------------------------
def update_note(note_id, title=None, content=None, filename=None):
    """
    Update a note's title, content, or filename.
    """
    fields = []
    values = []

    if title is not None:
        fields.append("title=?")
        values.append(title)

    if content is not None:
        fields.append("content=?")
        values.append(content)

    if filename is not None:
        fields.append("filename=?")
        values.append(filename)

    if not fields:
        return 0  # Nothing to update

    values.append(note_id)
    return execute(
        NOTES_DB,
        f"UPDATE notes SET {', '.join(fields)} WHERE id=?",
        tuple(values)
    )

# ------------------------------
# Delete a note
# ------------------------------
def delete_note(note_id):
    """
    Delete a note by its ID.
    """
    return execute(
        NOTES_DB,
        "DELETE FROM notes WHERE id=?",
        (note_id,)
    )
