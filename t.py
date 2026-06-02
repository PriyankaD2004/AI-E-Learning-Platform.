import os
import json
import time
import datetime
from flask import Blueprint, render_template, redirect, url_for, session, request, jsonify, send_file, flash
from dotenv import load_dotenv
import traceback
from flask import jsonify
import sqlite3



# ==============================
# ENV & PATHS
# ==============================
load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================
# DB HELPERS
# ==============================
from utils import (
    COURSES_DB, LESSONS_DB, COURSES_EXCEL_DB,
    query_all, query_one, execute,
    call_gemini_with_backoff,
    insert_quiz_question
)

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

#  ==============================
# Courses
# ==============================
@student_bp.route("/courses")
def courses():
    if not session.get("user"):
        return redirect(url_for("login"))

    try:
        rows = query_all(COURSES_DB, "SELECT id, topic, description FROM courses ORDER BY id DESC")
        return render_template("student/courses.html", courses=[dict(r) for r in rows])
    except Exception as e:
        flash(str(e), "danger")
        return render_template("student/courses.html", courses=[])

@student_bp.route("/generate_courses", methods=["POST"])
def generate_courses_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # ✅ Check if course already exists in DB
    existing = query_one(COURSES_DB, "SELECT id, description FROM courses WHERE topic=?", (topic,))
    if existing:
        return jsonify({
            "course": existing["description"],
            "course_id": existing["id"],
            "from_db": True
        })

    # ✅ Generate via API if not in DB
    try:
        course_text = call_gemini_with_backoff(f"Create a structured course outline on: {topic}") or "Fallback content."
        course_id = execute(
            COURSES_DB,
            "INSERT INTO courses (topic, description) VALUES (?, ?)",
            (topic, course_text)
        )
        return jsonify({"course": course_text, "course_id": course_id, "from_db": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================
# Lesson Generator Page
# ==============================
@student_bp.route("/generate_lesson")
def generate_lesson():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("student/generate_lesson.html")

# ==============================
# Generate Lesson (POST API)
# ==============================
@student_bp.route("/generate_lesson", methods=["POST"])
def generate_lesson_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # ✅ Check if lesson already exists
    existing = query_one(LESSONS_DB, "SELECT id, content FROM lessons WHERE topic=?", (topic,))
    if existing:
        return jsonify({"lesson": existing["content"], "lesson_id": existing["id"], "from_db": True})

    # ✅ Generate via API if not in DB
    try:
        lesson_text = call_gemini_with_backoff(
            f"Create a detailed lesson on: {topic}. Include explanation, key points, and examples."
        ) or "Fallback lesson content."
        lesson_id = execute(
            LESSONS_DB,
            "INSERT INTO lessons (topic, content) VALUES (?, ?)",
            (topic, lesson_text)
        )
        return jsonify({"lesson": lesson_text, "lesson_id": lesson_id, "from_db": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
# -----------------------------
# Quiz page
# -----------------------------

@student_bp.route("/quiz")
def quiz_page():
    if not session.get("user"):
        return "You must login first", 401
    return render_template("student/generate_quiz.html")

# -----------------------------
# Generate quiz API
# -----------------------------
@student_bp.route("/generate_quiz", methods=["POST"])
def generate_quiz_api():
    if not session.get("user"):
        return jsonify({"error": "You must login first"}), 401

    try:
        data = request.get_json(force=True)
        topic = data.get("topic", "").strip() or "General Knowledge"
        num_questions = int(data.get("num_questions", 5))

        # ✅ Updated strict prompt to force explanation field
        prompt = f"""
You are a quiz generator.
Generate {num_questions} multiple-choice questions on "{topic}".

⚠️ Rules:
- Return ONLY a JSON ARRAY. No extra text. No markdown. No code fences.
- Each object MUST contain exactly:
    "question": string,
    "options": array of 4 strings,
    "correct_answer": string (must match one option),
    "explanation": string (2–3 lines explaining WHY this option is correct)

Make explanations clear, educational, and concise.

STRICT JSON OUTPUT FORMAT EXAMPLE:
[
  {{
    "question": "Which data type is mutable in Python?",
    "options": ["Tuple","List","String","Int"],
    "correct_answer": "List",
    "explanation": "List is mutable, meaning it can be modified after creation using methods like append(). Tuple and String are immutable, and Int is a numeric type."
  }}
]
"""

        # ✅ Call Gemini API (now expecting explanation but not forcing JSON mode)
        result = call_gemini_with_backoff(prompt, is_json=False)
        print("🔍 Raw Gemini Response:", result)

        if not result:
            return jsonify({"error": "Empty response from Gemini"}), 500

        # ✅ Extract JSON if Gemini returns as string
        if isinstance(result, str):
            json_text = result.strip()
            if json_text.startswith("```"):  # remove code fences
                json_text = "\n".join(json_text.split("\n")[1:-1])

            try:
                quiz = json.loads(json_text)
            except Exception as je:
                print("🔥 JSON Parsing Failed:", je)
                return jsonify({"error": "Gemini did not return valid JSON"}), 500
        else:
            quiz = result

        # ✅ Validate must be list
        if not isinstance(quiz, list):
            return jsonify({"error": "Invalid quiz format from Gemini (not a JSON list)"}), 500

        # ✅ Validate required fields including explanation
        clean_quiz = []
        for q in quiz:
            if all(k in q for k in ["question","options","correct_answer","explanation"]):
                if isinstance(q["options"], list) and len(q["options"]) == 4:
                    if q["correct_answer"] in q["options"]:
                        clean_quiz.append(q)

        if not clean_quiz:
            return jsonify({"error": "Gemini JSON missing required fields"}), 500

        return jsonify({"quiz": clean_quiz})

    except Exception as e:
        print("❌ Quiz API Error:", e)
        print(traceback.format_exc())
        return jsonify({"error": "Server crash"}), 500
    
# ==============================
# Notes
# ==============================
@student_bp.route("/notes")
def notes_page():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("student/notes.html")

@student_bp.route("/ai_notes", endpoint="ai_notes_page")
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

    # ✅ Load existing notes index
    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))

    # ✅ Check if note already exists
    existing = next((n for n in notes_list if n["topic"].lower() == topic.lower()), None)
    if existing:
        return jsonify({
            "notes": existing.get("content"),
            "download_url": url_for("student.download_note", filename=existing["filename"]),
            "from_db": True
        })

    # ✅ Generate note via AI if not found
    try:
        notes = call_gemini_with_backoff(f"Create detailed notes on: {topic}") or "Fallback notes content."

        safe = "".join(c if c.isalnum() else "_" for c in topic)
        filename = f"{safe}_{int(time.time())}.txt"
        full_path = os.path.join(NOTES_DIR, filename)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(notes)

        # ✅ Append new note to index with content
        notes_list.append({
            "topic": topic,
            "filename": filename,
            "content": notes,
            "date": datetime.datetime.utcnow().isoformat()
        })

        with open(NOTES_INDEX, "w", encoding="utf-8") as f:
            json.dump(notes_list, f, indent=2)

        return jsonify({
            "notes": notes,
            "download_url": url_for("student.download_note", filename=filename),
            "from_db": False
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@student_bp.route("/notes/list")
def list_notes_api():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    for n in notes_list:
        n["url"] = url_for("student.download_note", filename=n["filename"])
        # Optionally remove content in list view if too large
        n.pop("content", None)

    return jsonify({"notes": notes_list})

@student_bp.route("/notes/download/<filename>")
def download_note(filename):
    if not session.get("user"):
        return redirect(url_for("login"))

    full_path = os.path.join(NOTES_DIR, filename)
    if not os.path.exists(full_path):
        flash("File not found", "danger")
        return redirect(url_for("student.notes_page"))
    
    return send_file(full_path, as_attachment=True)

# ==============================
# excel courses
# ==============================

@student_bp.route("/student/student_course_list", endpoint="student_course_list")
def student_course_list():
    if not session.get("user"):
        return redirect(url_for("login"))

    search_query = request.args.get("q", "").strip().lower()

    rows = query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel")

    courses = []

    for r in rows:
        # Access dictionary keys safely instead of indices
        title = str(r.get("Course Title", "") or "")
        instructor = str(r.get("Course Instructor Name", "Not specified") or "Not specified")
        skills = str(r.get("Skills You Gain", "") or "")
        duration = str(r.get("Total Course Hours", "") or "")
        lectures = r.get("Total No of Lectures", 0) or 0
        rating = r.get("Ratings", 0) or 0
        reviews = r.get("Total Reviews", 0) or 0
        level = str(r.get("Course Levels", "Not specified") or "Not specified")
        link = r.get("Course Links", "#")
        if link and not str(link).startswith(("http://", "https://")):
            link = "#"
        thumbnail = r.get("Course Thumbnail Image")
        if not thumbnail or not str(thumbnail).startswith("http"):
            thumbnail = url_for("static", filename="images/default_thumbnail.png")

        # Apply search filter
        if search_query and search_query not in title.lower():
            continue

        courses.append({
            "title": title,
            "instructor": instructor,
            "skills": skills,
            "duration": duration,
            "lectures": lectures,
            "rating": rating,
            "reviews": reviews,
            "level": level,
            "course_link_url": link,
            "thumbnail": thumbnail
        })

    return render_template(
        "student/all_courses.html",
        courses=courses,
        search_query=search_query
    )


@student_bp.route("/student/<int:course_id>", endpoint="course_details")
def course_details(course_id):
    if not session.get("user"):
        return redirect(url_for("login"))

    r = query_one(
        COURSES_EXCEL_DB,
        "SELECT * FROM courses_excel WHERE id=?",
        (course_id,)
    )

    if not r:
        return "Course not found", 404

    # Use dictionary keys instead of indices
    course = {
        "id": r.get("id"),
        "name": str(r.get("Course Title", "Untitled Course") or "Untitled Course"),
        "instructor": str(r.get("Course Instructor Name", "Not specified") or "Not specified"),
        "skills": str(r.get("Skills You Gain", "") or ""),
        "duration": str(r.get("Total Course Hours", "") or ""),
        "lectures": r.get("Total No of Lectures", 0) or 0,
        "ratings": r.get("Ratings", 0) or 0,
        "reviews": r.get("Total Reviews", 0) or 0,
        "level": str(r.get("Course Levels", "Not specified") or "Not specified"),
        "course_link_url": (
            r.get("Course Links", "#") if r.get("Course Links") and str(r.get("Course Links")).startswith(("http://", "https://")) else "#"
        ),
        "thumbnail": (
            r.get("Course Thumbnail Image") if r.get("Course Thumbnail Image") and str(r.get("Course Thumbnail Image")).startswith("http")
            else url_for("static", filename="images/default_thumbnail.png")
        )
    }

    return render_template("student/course_details.html", course=course)

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
# Avatar
# ==============================
@student_bp.route("/avatar")
def avatar_page():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return render_template("student/avatar.html")



@student_bp.route("/student/student_course_list", endpoint="student_course_list")
def student_course_list():
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    # Fetch all courses
    rows = query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel")

    courses = []

    for r in rows:
        # Safely get values, fallback to default if missing
        link = r.get("link") or r.get("Course Links")
        thumbnail = r.get("thumbnail") or r.get("Course Thumbnail Image")

        courses.append({
            "id": r.get("id"),
            "title": r.get("course_title") or "Untitled Course",
            "instructor": r.get("instructor_name") or "Not specified",
            "skills": r.get("skills") or "",
            "lectures": r.get("lectures") or 0,
            "ratings": r.get("ratings") or 0,
            "thumbnail": thumbnail if isinstance(thumbnail, str) and thumbnail.startswith("http")
                         else url_for("static", filename="images/default_thumbnail.png"),
            "link": link if isinstance(link, str) and link.startswith("http") else None
        })

    print("COURSES SENT TO TEMPLATE:", len(courses))

    return render_template("student/all_courses.html", courses=courses)


# ==============================
# student course list
# ==============================

@student_bp.route("/student/student_course_list", endpoint="student_course_list")
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


# html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>All Courses</title>

    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">

    <style>
        body {
            background: #f5f7fb;
            font-family: "Poppins", sans-serif;
        }
        .course-card {
            transition: transform 0.3s, box-shadow 0.3s;
            border-radius: 12px;
            overflow: hidden;
        }
        .course-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
        }
        .course-img {
            height: 180px;
            object-fit: cover;
        }
        .card-title {
            min-height: 3rem;
        }
        .skills-text {
            min-height: 4rem;
        }
    </style>
</head>

<body>

<nav class="navbar navbar-dark bg-dark">
    <div class="container">
        <a class="navbar-brand fw-bold" href="{{ url_for('student.student_course_list') }}">
            AI E-Learning
        </a>

        <ul class="navbar-nav ms-auto flex-row gap-3">
            <li class="nav-item">
                <a class="nav-link" href="{{ url_for('student.dashboard') }}">Dashboard</a>
            </li>
            <li class="nav-item">
                <a class="nav-link" href="{{ url_for('logout') }}">Logout</a>
            </li>
        </ul>
    </div>
</nav>

<div class="container py-5">
    <h2 class="fw-bold mb-4 text-center">Available Courses</h2>

    <div class="row g-4">
        {% for course in courses %}
        <div class="col-md-4">
            <div class="card course-card h-100">

                <img src="{{ course.thumbnail }}" class="course-img" alt="{{ course.title }}">

                <div class="card-body d-flex flex-column">

                    <h5 class="card-title fw-bold">
                        {{ course.title }}
                    </h5>

                    <p class="skills-text small">
                        {% if course.skills %}
                            {{ course.skills[:120] }}{% if course.skills|length > 120 %}...{% endif %}
                        {% else %}
                            <span class="text-muted">Skills not available</span>
                        {% endif %}
                    </p>

                     <p class="small mb-3"></p><strong>Instructor:</strong> {{ course.instructor }}</p>


                    <p class="small mb-3">
                        <strong>Rating:</strong> {{ course.ratings }} / 5
                    </p>

                    {% if course.link %}
                        <a href="{{ course.link }}" target="_blank" class="btn btn-primary w-100 mt-auto">
                            Go to Course
                        </a>
                    {% else %}
                        <button class="btn btn-secondary w-100 mt-auto" disabled>
                            No Course Link Available
                        </button>
                    {% endif %}

                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<footer class="bg-dark text-white text-center py-3">
    <small>&copy; 2025 AI E-Learning Platform</small>
</footer>

</body>
</html>

search = request.args.get("q", "").strip()

    if search:
        # Search courses by title, instructor, or skills
        rows = query_all(
            COURSES_EXCEL_DB,
            """
            SELECT * FROM courses_excel
            WHERE [Course Title] LIKE ?
               OR [Course Instructor Name] LIKE ?
               OR [Skills You Gain] LIKE ?
            ORDER BY rowid DESC
            """,
            (f"%{search}%", f"%{search}%", f"%{search}%")
        )
    else:
        # Get all courses ordered by rowid
        rows = query_all(
            COURSES_EXCEL_DB,
            "SELECT * FROM courses_excel ORDER BY rowid DESC"
        )


        import os
import json
import time
import datetime
from flask import Blueprint, render_template, redirect, url_for, session, request, jsonify, send_file, flash
from dotenv import load_dotenv
import traceback
from flask import jsonify
import sqlite3
import os






# ==============================
# ENV & PATHS
# ==============================
load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================
# DB HELPERS
# ==============================
from utils import (
    COURSES_DB, LESSONS_DB, COURSES_EXCEL_DB,
    query_all, query_one, execute,
    call_gemini_with_backoff,
    insert_quiz_question
)

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

#  ==============================
# Courses
# ==============================
@student_bp.route("/courses")
def courses():
    if not session.get("user"):
        return redirect(url_for("login"))

    try:
        rows = query_all(COURSES_DB, "SELECT id, topic, description FROM courses ORDER BY id DESC")
        return render_template("student/courses.html", courses=[dict(r) for r in rows])
    except Exception as e:
        flash(str(e), "danger")
        return render_template("student/courses.html", courses=[])

@student_bp.route("/generate_courses", methods=["POST"])
def generate_courses_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # ✅ Check if course already exists in DB
    existing = query_one(COURSES_DB, "SELECT id, description FROM courses WHERE topic=?", (topic,))
    if existing:
        return jsonify({
            "course": existing["description"],
            "course_id": existing["id"],
            "from_db": True
        })

    # ✅ Generate via API if not in DB
    try:
        course_text = call_gemini_with_backoff(f"Create a structured course outline on: {topic}") or "Fallback content."
        course_id = execute(
            COURSES_DB,
            "INSERT INTO courses (topic, description) VALUES (?, ?)",
            (topic, course_text)
        )
        return jsonify({"course": course_text, "course_id": course_id, "from_db": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================
# Lesson Generator Page
# ==============================
@student_bp.route("/generate_lesson")
def generate_lesson():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("student/generate_lesson.html")

# ==============================
# Generate Lesson (POST API)
# ==============================
@student_bp.route("/generate_lesson", methods=["POST"])
def generate_lesson_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # ✅ Check if lesson already exists
    existing = query_one(LESSONS_DB, "SELECT id, content FROM lessons WHERE topic=?", (topic,))
    if existing:
        return jsonify({"lesson": existing["content"], "lesson_id": existing["id"], "from_db": True})

    # ✅ Generate via API if not in DB
    try:
        lesson_text = call_gemini_with_backoff(
            f"Create a detailed lesson on: {topic}. Include explanation, key points, and examples."
        ) or "Fallback lesson content."
        lesson_id = execute(
            LESSONS_DB,
            "INSERT INTO lessons (topic, content) VALUES (?, ?)",
            (topic, lesson_text)
        )
        return jsonify({"lesson": lesson_text, "lesson_id": lesson_id, "from_db": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
# -----------------------------
# Quiz page
# -----------------------------

@student_bp.route("/quiz")
def quiz_page():
    if not session.get("user"):
        return "You must login first", 401
    return render_template("student/generate_quiz.html")

# -----------------------------
# Generate quiz API
# -----------------------------
@student_bp.route("/generate_quiz", methods=["POST"])
def generate_quiz_api():
    if not session.get("user"):
        return jsonify({"error": "You must login first"}), 401

    try:
        data = request.get_json(force=True)
        topic = data.get("topic", "").strip() or "General Knowledge"
        num_questions = int(data.get("num_questions", 5))

        # ✅ Updated strict prompt to force explanation field
        prompt = f"""
You are a quiz generator.
Generate {num_questions} multiple-choice questions on "{topic}".

⚠️ Rules:
- Return ONLY a JSON ARRAY. No extra text. No markdown. No code fences.
- Each object MUST contain exactly:
    "question": string,
    "options": array of 4 strings,
    "correct_answer": string (must match one option),
    "explanation": string (2–3 lines explaining WHY this option is correct)

Make explanations clear, educational, and concise.

STRICT JSON OUTPUT FORMAT EXAMPLE:
[
  {{
    "question": "Which data type is mutable in Python?",
    "options": ["Tuple","List","String","Int"],
    "correct_answer": "List",
    "explanation": "List is mutable, meaning it can be modified after creation using methods like append(). Tuple and String are immutable, and Int is a numeric type."
  }}
]
"""

        # ✅ Call Gemini API (now expecting explanation but not forcing JSON mode)
        result = call_gemini_with_backoff(prompt, is_json=False)
        print("🔍 Raw Gemini Response:", result)

        if not result:
            return jsonify({"error": "Empty response from Gemini"}), 500

        # ✅ Extract JSON if Gemini returns as string
        if isinstance(result, str):
            json_text = result.strip()
            if json_text.startswith("```"):  # remove code fences
                json_text = "\n".join(json_text.split("\n")[1:-1])

            try:
                quiz = json.loads(json_text)
            except Exception as je:
                print("🔥 JSON Parsing Failed:", je)
                return jsonify({"error": "Gemini did not return valid JSON"}), 500
        else:
            quiz = result

        # ✅ Validate must be list
        if not isinstance(quiz, list):
            return jsonify({"error": "Invalid quiz format from Gemini (not a JSON list)"}), 500

        # ✅ Validate required fields including explanation
        clean_quiz = []
        for q in quiz:
            if all(k in q for k in ["question","options","correct_answer","explanation"]):
                if isinstance(q["options"], list) and len(q["options"]) == 4:
                    if q["correct_answer"] in q["options"]:
                        clean_quiz.append(q)

        if not clean_quiz:
            return jsonify({"error": "Gemini JSON missing required fields"}), 500

        return jsonify({"quiz": clean_quiz})

    except Exception as e:
        print("❌ Quiz API Error:", e)
        print(traceback.format_exc())
        return jsonify({"error": "Server crash"}), 500
    
# ==============================
# Notes
# ==============================
@student_bp.route("/notes")
def notes_page():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("student/notes.html")

@student_bp.route("/ai_notes", endpoint="ai_notes_page")
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

    # ✅ Load existing notes index
    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))

    # ✅ Check if note already exists
    existing = next((n for n in notes_list if n["topic"].lower() == topic.lower()), None)
    if existing:
        return jsonify({
            "notes": existing.get("content"),
            "download_url": url_for("student.download_note", filename=existing["filename"]),
            "from_db": True
        })

    # ✅ Generate note via AI if not found
    try:
        notes = call_gemini_with_backoff(f"Create detailed notes on: {topic}") or "Fallback notes content."

        safe = "".join(c if c.isalnum() else "_" for c in topic)
        filename = f"{safe}_{int(time.time())}.txt"
        full_path = os.path.join(NOTES_DIR, filename)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(notes)

        # ✅ Append new note to index with content
        notes_list.append({
            "topic": topic,
            "filename": filename,
            "content": notes,
            "date": datetime.datetime.utcnow().isoformat()
        })

        with open(NOTES_INDEX, "w", encoding="utf-8") as f:
            json.dump(notes_list, f, indent=2)

        return jsonify({
            "notes": notes,
            "download_url": url_for("student.download_note", filename=filename),
            "from_db": False
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@student_bp.route("/notes/list")
def list_notes_api():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    for n in notes_list:
        n["url"] = url_for("student.download_note", filename=n["filename"])
        # Optionally remove content in list view if too large
        n.pop("content", None)

    return jsonify({"notes": notes_list})

@student_bp.route("/notes/download/<filename>")
def download_note(filename):
    if not session.get("user"):
        return redirect(url_for("login"))

    full_path = os.path.join(NOTES_DIR, filename)
    if not os.path.exists(full_path):
        flash("File not found", "danger")
        return redirect(url_for("student.notes_page"))
    
    return send_file(full_path, as_attachment=True)


# ==============================
# Student course
# ==============================

@student_bp.route("/student/student_course_list", endpoint="student_course_list")
def student_course_list():
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    rows = query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel")

    courses = []

    for r in rows:
        courses.append({
            "course_title": r.get("course_title", "Untitled Course"),
            "instructor_name": r.get("instructor_name", "Not specified"),
            "skills": r.get("skills", ""),
            "ratings": r.get("ratings", 0),
            "thumbnail": r.get("thumbnail") or url_for(
                "static", filename="images/default_thumbnail.png"
            ),
            "link": r.get("link")
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
# Avatar
# ==============================
@student_bp.route("/avatar")
def avatar_page():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return render_template("student/avatar.html")

from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import pandas as pd
import sqlite3
import google.generativeai as genai
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------------------
# Load Environment & API Key
# ------------------------------
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY or GOOGLE_API_KEY not found in .env")

genai.configure(api_key=API_KEY)

# ------------------------------
# Flask App Setup
# ------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# ------------------------------
# Database Paths & Utils
# ------------------------------
USERS_DB = "users.db"

def query_one(db_path, query, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

def execute(db_path, query, params=()):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()

# ------------------------------
# Import & Register Blueprints
# ------------------------------
from student import student_bp
from admin import admin_bp

app.register_blueprint(student_bp, url_prefix="/student")
app.register_blueprint(admin_bp, url_prefix="/admin")

# ------------------------------
# Home & About
# ------------------------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/about")
def about():
    return render_template("about.html")

# ------------------------------
# CSV Loader
# ------------------------------
COURSE_FILE = "ai_learning_courses.csv"

def safe_load_csv(file_path, required_columns):
    if not os.path.exists(file_path):
        print(f"⚠️ CSV file not found: {file_path}")
        return pd.DataFrame(columns=required_columns)
    try:
        df = pd.read_csv(file_path)
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""
        return df[required_columns]
    except Exception as e:
        print("Error loading CSV:", e)
        return pd.DataFrame(columns=required_columns)

@app.route("/videos")
def videos():
    columns = ["CourseID", "CourseName", "Category", "LessonCount", "Difficulty",
               "DurationHours", "Description", "Instructor", "CourseLink"]
    courses_df = safe_load_csv(COURSE_FILE, columns)
    
    if courses_df.empty:
        flash("No courses available right now. Please add courses to the CSV.", "info")
    
    courses = courses_df.to_dict(orient="records")
    return render_template("videos.html", courses=courses)

@app.route("/video_player/<int:course_id>")
def video_player(course_id):
    columns = ["CourseID", "CourseName", "Category", "LessonCount", "Difficulty",
               "DurationHours", "Description", "Instructor", "CourseLink"]
    courses_df = safe_load_csv(COURSE_FILE, columns)
    
    course = courses_df[courses_df["CourseID"] == course_id]
    if course.empty:
        return "Course not found", 404
    
    course_data = course.iloc[0].to_dict()
    return render_template("video_player.html", course=course_data)

# ------------------------------
# Student Authentication Routes
# ------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = query_one(USERS_DB, "SELECT * FROM users WHERE username=?", (username,))
        print("DEBUG: Login attempt for:", username)
        print("DEBUG: User from DB:", user)

        if user:
            user_dict = dict(user)
            if check_password_hash(user_dict["password"], password):
                session["user"] = {"id": user_dict["id"], "username": user_dict["username"]}
                flash(f"Welcome, {username}!", "success")
                return redirect(url_for("student.dashboard"))
            else:
                print("DEBUG: Password mismatch")

        flash("Invalid username or password", "danger")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        existing_user = query_one(USERS_DB, "SELECT id FROM users WHERE username=?", (username,))
        if existing_user:
            flash("Username already taken!", "warning")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)
        execute(USERS_DB, "INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        flash("Signup successful! Please login.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# ------------------------------
# Protect Student Routes
# ------------------------------
@app.before_request
def require_login():
    public_endpoints = {"home", "about", "videos", "video_player", "login", "signup", "static"}
    if request.endpoint not in public_endpoints:
        if request.path.startswith("/student") and "user" not in session:
            flash("Please login to access student pages.", "warning")
            return redirect(url_for("login"))

# ------------------------------
# Run App
# ------------------------------
if __name__ == "__main__":
    app.run(debug=True)



import os
import json
import time
import datetime
from flask import Blueprint, render_template, redirect, url_for, session, request, jsonify, send_file, flash
from dotenv import load_dotenv
import traceback
from utils import COURSES_DB, LESSONS_DB, COURSES_EXCEL_DB, query_all, query_one, execute, call_gemini_with_backoff

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
        rows = query_all(COURSES_DB, "SELECT id, title, description FROM courses ORDER BY id DESC")
        courses = [dict(r) for r in rows]
        return render_template("student/courses.html", courses=courses)
    except Exception as e:
        flash(str(e), "danger")
        return render_template("student/courses.html", courses=[])

@student_bp.route("/generate_courses", methods=["POST"])
def generate_courses_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    # Check if course already exists
    existing = query_one(COURSES_DB, "SELECT id, description FROM courses WHERE title=?", (topic,))
    if existing:
        return jsonify({
            "course": existing["description"],
            "course_id": existing["id"],
            "from_db": True
        })

    # Generate via AI
    try:
        course_text = call_gemini_with_backoff(f"Create a structured course outline on: {topic}") or "Fallback content."
        course_id = execute(
            COURSES_DB,
            "INSERT INTO courses (title, description) VALUES (?, ?)",
            (topic, course_text)
        )
        return jsonify({"course": course_text, "course_id": course_id, "from_db": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================
# Lesson Generator
# ==============================
@student_bp.route("/generate_lesson")
def generate_lesson():
    if not session.get("user"):
        return redirect(url_for("auth.login"))
    return render_template("student/generate_lesson.html")

@student_bp.route("/generate_lesson", methods=["POST"])
def generate_lesson_post():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400

    existing = query_one(LESSONS_DB, "SELECT id, content FROM lessons WHERE title=?", (topic,))
    if existing:
        return jsonify({"lesson": existing["content"], "lesson_id": existing["id"], "from_db": True})

    try:
        lesson_text = call_gemini_with_backoff(
            f"Create a detailed lesson on: {topic}. Include explanation, key points, and examples."
        ) or "Fallback lesson content."
        lesson_id = execute(
            LESSONS_DB,
            "INSERT INTO lessons (title, content) VALUES (?, ?)",
            (topic, lesson_text)
        )
        return jsonify({"lesson": lesson_text, "lesson_id": lesson_id, "from_db": False})
    except Exception as e:
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
        except Exception as je:
            return jsonify({"error": "Invalid JSON from AI"}), 500

        clean_quiz = [q for q in quiz if all(k in q for k in ["question", "options", "correct_answer", "explanation"])]
        return jsonify({"quiz": clean_quiz})
    except Exception as e:
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

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    existing = next((n for n in notes_list if n["topic"].lower() == topic.lower()), None)
    if existing:
        return jsonify({
            "notes": existing["content"],
            "download_url": url_for("student.download_note", filename=existing["filename"]),
            "from_db": True
        })

    try:
        notes = call_gemini_with_backoff(f"Create detailed notes on: {topic}") or "Fallback notes content."
        safe = "".join(c if c.isalnum() else "_" for c in topic)
        filename = f"{safe}_{int(time.time())}.txt"
        full_path = os.path.join(NOTES_DIR, filename)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(notes)

        notes_list.append({
            "topic": topic,
            "filename": filename,
            "content": notes,
            "date": datetime.datetime.utcnow().isoformat()
        })
        with open(NOTES_INDEX, "w", encoding="utf-8") as f:
            json.dump(notes_list, f, indent=2)

        return jsonify({
            "notes": notes,
            "download_url": url_for("student.download_note", filename=filename),
            "from_db": False
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@student_bp.route("/notes/list")
def list_notes_api():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    for n in notes_list:
        n["url"] = url_for("student.download_note", filename=n["filename"])
        n.pop("content", None)
    return jsonify({"notes": notes_list})

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
        except Exception as je:
            return jsonify({"error": "Invalid JSON from AI"}), 500

        clean_quiz = [q for q in quiz if all(k in q for k in ["question", "options", "correct_answer", "explanation"])]
        return jsonify({"quiz": clean_quiz})
    except Exception as e:
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

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    existing = next((n for n in notes_list if n["topic"].lower() == topic.lower()), None)
    if existing:
        return jsonify({
            "notes": existing["content"],
            "download_url": url_for("student.download_note", filename=existing["filename"]),
            "from_db": True
        })

    try:
        notes = call_gemini_with_backoff(f"Create detailed notes on: {topic}") or "Fallback notes content."
        safe = "".join(c if c.isalnum() else "_" for c in topic)
        filename = f"{safe}_{int(time.time())}.txt"
        full_path = os.path.join(NOTES_DIR, filename)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(notes)

        notes_list.append({
            "topic": topic,
            "filename": filename,
            "content": notes,
            "date": datetime.datetime.utcnow().isoformat()
        })
        with open(NOTES_INDEX, "w", encoding="utf-8") as f:
            json.dump(notes_list, f, indent=2)

        return jsonify({
            "notes": notes,
            "download_url": url_for("student.download_note", filename=filename),
            "from_db": False
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@student_bp.route("/notes/list")
def list_notes_api():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401

    notes_list = json.load(open(NOTES_INDEX, "r", encoding="utf-8"))
    for n in notes_list:
        n["url"] = url_for("student.download_note", filename=n["filename"])
        n.pop("content", None)
    return jsonify({"notes": notes_list})

@student_bp.route("/notes/download/<filename>")
def download_note(filename):
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    full_path = os.path.join(NOTES_DIR, filename)
    if not os.path.exists(full_path):
        flash("File not found", "danger")
        return redirect(url_for("student.notes_page"))

    return send_file(full_path, as_attachment=True)
