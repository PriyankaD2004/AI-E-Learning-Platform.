import os
import json
import time
import datetime
from flask import Blueprint, render_template, redirect, url_for, session, request, jsonify, send_file, flash
from dotenv import load_dotenv
import traceback
from utils import COURSES_DB, LESSONS_DB, COURSES_EXCEL_DB,QUIZ_DB,NOTES_DB,query_all, query_one, execute, call_gemini_with_backoff
from utils import insert_quiz_question, delete_quiz_by_topic,get_notes_by_topic,insert_note,list_all_notes,delete_note

# ==============================
# ENV & PATHS
# ==============================
load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================
# Blueprint
# ==============================
student_bp = Blueprint("student", __name__, url_prefix="/student")

# ==============================
# Notes Storage
# ==============================
NOTES_DIR = os.path.join("static", "notes")
os.makedirs(NOTES_DIR, exist_ok=True)

NOTES_INDEX = os.path.join(NOTES_DIR, "notes.json")
if not os.path.exists(NOTES_INDEX):
    with open(NOTES_INDEX, "w", encoding="utf-8") as f:
        json.dump([], f)

# ==============================
# Root & Dashboard
# ==============================
@student_bp.route("/")
def root():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return redirect(url_for("student.dashboard"))

@student_bp.route("/dashboard")
def dashboard():
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    stats = {
        "courses": len(query_all(COURSES_DB, "SELECT id FROM courses")),
        "lessons": len(query_all(LESSONS_DB, "SELECT id FROM lessons")),
        "completed": 0,
        "progress": "0%",
        "ai_chats": 0
    }

    return render_template("student/student_dashboard.html", user=session.get("user"), stats=stats)

# ==============================
# Courses
# ==============================
@student_bp.route("/courses")
def courses():
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    try:
        # Fetch all courses ordered by latest
        rows = query_all(COURSES_DB, "SELECT id, title, description FROM courses ORDER BY id DESC")
        courses = [dict(r) for r in rows]
        return render_template("student/courses.html", courses=courses)
    except Exception as e:
        flash(f"Error loading courses: {str(e)}", "danger")
        return render_template("student/courses.html", courses=[])


@student_bp.route("/generate_courses", methods=["POST"])
def generate_courses_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    try:
        # Check if course already exists in DB
        existing = query_one(COURSES_DB, "SELECT id, description FROM courses WHERE title=?", (topic,))
        if existing:
            return jsonify({
                "course": existing["description"],
                "course_id": existing["id"],
                "from_db": True
            })

        # Generate course using AI
        course_text = call_gemini_with_backoff(f"Create a structured course outline on: {topic}") or "Fallback content."

        # Insert new course into DB
        course_id = execute(
            COURSES_DB,
            "INSERT INTO courses (title, description) VALUES (?, ?)",
            (topic, course_text)
        )

        return jsonify({
            "course": course_text,
            "course_id": course_id,
            "from_db": False
        })

    except Exception as e:
        return jsonify({"error": f"Failed to generate course: {str(e)}"}), 500


# ==============================
# Lesson Generator
# ==============================
@student_bp.route("/generate_lesson", methods=["GET", "POST"])
def generate_lesson():

    # ------------------------------
    # Authentication check
    # ------------------------------
    if not session.get("user"):
        if request.method == "POST":
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("auth.login"))

    # ------------------------------
    # GET → Render page
    # ------------------------------
    if request.method == "GET":
        return render_template("student/generate_lesson.html")

    # ------------------------------
    # POST → Generate lesson (API)
    # ------------------------------
    try:
        data = request.get_json(silent=True) or {}
        topic = (data.get("topic") or "").strip()

        if not topic:
            return jsonify({"error": "Topic required"}), 400

        # OPTIONAL: If course_id is stored in session later
        course_id = session.get("course_id")  # None is OK for now

        # 1️⃣ Check DB first (avoid duplicates)
        existing = query_one(
            LESSONS_DB,
            """
            SELECT id, content
            FROM lessons
            WHERE title = ?
            """,
            (topic,)
        )

        if existing:
            return jsonify({
                "lesson": existing["content"],
                "lesson_id": existing["id"],
                "from_db": True
            })

        # 2️⃣ Generate lesson using AI
        lesson_text = call_gemini_with_backoff(
            f"Create a detailed lesson on: {topic}. "
            "Include explanation, key points, and examples."
        ) or "Fallback lesson content."

        # 3️⃣ Save lesson to DB (schema-correct)
        lesson_id = execute(
            LESSONS_DB,
            """
            INSERT INTO lessons (course_id, title, content)
            VALUES (?, ?, ?)
            """,
            (course_id, topic, lesson_text)
        )

        return jsonify({
            "lesson": lesson_text,
            "lesson_id": lesson_id,
            "from_db": False
        })

    except Exception as e:
        # 🔴 Always return JSON (never HTML)
        return jsonify({"error": str(e)}), 500
    
# ==============================
# Quiz
# ==============================
@student_bp.route("/quiz")
def quiz_page():
    if not session.get("user"):
        return "You must login first", 401
    return render_template("student/generate_quiz.html")

@student_bp.route("/generate_quiz", methods=["POST"]) 
def generate_quiz_api():
    if not session.get("user"):
        return jsonify({"error": "You must login first"}), 401

    try:
        data = request.get_json(force=True)
        topic = data.get("topic", "").strip() or "General Knowledge"
        num_questions = int(data.get("num_questions", 5))

        prompt = f"""
You are a quiz generator.
Generate {num_questions} multiple-choice questions on "{topic}".
Return ONLY a JSON array with keys: question, options (4), correct_answer, explanation.
        """
        result = call_gemini_with_backoff(prompt, is_json=False)

        if not result:
            return jsonify({"error": "Empty response from AI"}), 500

        # Clean JSON
        try:
            if result.startswith("```"):
                result = "\n".join(result.split("\n")[1:-1])
            quiz = json.loads(result)
        except Exception:
            return jsonify({"error": "Invalid JSON from AI"}), 500

        clean_quiz = [
            q for q in quiz
            if all(k in q for k in ["question", "options", "correct_answer", "explanation"])
        ]

        # 🔥 INSERT INTO quiz.db (ADDED)
        delete_quiz_by_topic(topic)

        for q in clean_quiz:
            insert_quiz_question(
                topic=topic,
                question=q["question"],
                options=q["options"],
                correct_answer=q["correct_answer"],
                explanation=q.get("explanation", "")
            )

        return jsonify({"quiz": clean_quiz})

    except Exception:
        print(traceback.format_exc())
        return jsonify({"error": "Server error"}), 500
    
# ==============================
# Notes
# ==============================

@student_bp.route("/notes")
def notes_page():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return render_template("student/notes.html")

@student_bp.route("/ai_notes")
def ai_notes_page():
    return redirect(url_for("student.notes_page"))



@student_bp.route("/api_generate_notes", methods=["POST"])
def api_generate_notes():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()

    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # 1️⃣ Check DB first
    existing_notes = get_notes_by_topic(topic)
    if existing_notes:
        note = existing_notes[0]  # latest
        return jsonify({
            "notes": note["content"],
            "download_url": url_for(
                "student.download_note",
                filename=note["filename"]
            ),
            "from_db": True
        })

    try:
        # 2️⃣ Generate notes
        notes = call_gemini_with_backoff(
            f"Create detailed notes on: {topic}"
        ) or "Fallback notes content."

        safe = "".join(c if c.isalnum() else "_" for c in topic)
        filename = f"{safe}_{int(time.time())}.txt"
        full_path = os.path.join(NOTES_DIR, filename)

        # 3️⃣ Save file
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(notes)

        # 4️⃣ Save to DB
        insert_note(
            topic=topic,
            title=f"Notes on {topic}",
            content=notes,
            filename=filename
        )

        return jsonify({
            "notes": notes,
            "download_url": url_for(
                "student.download_note",
                filename=filename
            ),
            "from_db": False
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@student_bp.route("/notes/list")
def list_notes_api():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    notes = list_all_notes()

    response = []
    for n in notes:
        response.append({
            "id": n["id"],
            "topic": n["topic"],
            "title": n["title"],
            "created_at": n["created_at"],
            "url": url_for(
                "student.download_note",
                filename=n["filename"]
            )
        })

    return jsonify({"notes": response})

@student_bp.route("/notes/download/<filename>")
def download_note(filename):
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    full_path = os.path.join(NOTES_DIR, filename)
    if not os.path.exists(full_path):
        flash("File not found", "danger")
        return redirect(url_for("student.notes_page"))

    return send_file(full_path, as_attachment=True)



# ==============================
# student course list
# ==============================

@student_bp.route("/student_course_list", endpoint="student_course_list")
def student_course_list():
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    rows = query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel")

    courses = []

    for r in rows:
        courses.append({
            # ❌ DO NOT USE r["id"]

            "course_title": r.get("Course Title") or r.get("course_title") or "Untitled Course",

            "instructor_name": r.get("Course Instructor Name") or r.get("instructor_name") or "Not specified",

            "skills": r.get("Skills You Gain") or r.get("skills") or "",

            "ratings": r.get("Ratings") or r.get("ratings") or 0,

            "thumbnail": (
                r.get("Course Thumbnail Image")
                or r.get("thumbnail")
                or url_for("static", filename="images/default_thumbnail.png")
            ),

            "link": r.get("Course Links") or r.get("link")
        })

    return render_template("student/all_courses.html", courses=courses)


# ==============================
# AI Tutor
# ==============================
@student_bp.route("/ai_tutor_api", methods=["POST"])
def ai_tutor_api():
    if not session.get("user"):
        return jsonify({"answer": "Login required"}), 401

    data = request.get_json(force=True)
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"answer": "Ask a valid question"}), 400

    answer = call_gemini_with_backoff(question)
    return jsonify({"answer": answer})

# ==============================
# Avatar Page
# ==============================
@student_bp.route("/avatar")
def avatar_page():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return render_template("student/avatar.html")
