import os
import json
import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, session, request, flash
from werkzeug.security import generate_password_hash, check_password_hash

from admin_utils import (
    ADMIN_DB,
    COURSES_DB,
    LESSONS_DB,
    COURSES_EXCEL_DB,
    NOTES_DB,
    QUIZ_DB,
    query_all,
    query_one,
    execute
)

# ==============================
# Blueprint
# ==============================
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# ==============================
# Auth Decorator
# ==============================
def admin_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "admin" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("admin.admin_login"))
        return func(*args, **kwargs)
    return wrapper

# ==============================
# Admin Login
# ==============================
@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        admin = query_one(
            ADMIN_DB,
            "SELECT * FROM admins WHERE username=?",
            (username,)
        )

        if admin and check_password_hash(admin["password"], password):
            session["admin"] = {
                "id": admin["id"],
                "username": admin["username"]
            }
            flash("Logged in successfully!", "success")
            return redirect(url_for("admin.dashboard"))

        flash("Invalid username or password", "danger")

    return render_template("admin/admin_login.html")

# ==============================
# Admin Signup
# ==============================
@admin_bp.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("All fields are required", "warning")
            return redirect(url_for("admin.admin_signup"))

        if query_one(
            ADMIN_DB,
            "SELECT id FROM admins WHERE username=?",
            (username,)
        ):
            flash("Username already exists", "warning")
            return redirect(url_for("admin.admin_signup"))

        hashed_password = generate_password_hash(password)
        execute(
            ADMIN_DB,
            "INSERT INTO admins (username, password) VALUES (?, ?)",
            (username, hashed_password)
        )

        flash("Admin account created successfully!", "success")
        return redirect(url_for("admin.admin_login"))

    return render_template("admin/admin_signup.html")

# ==============================
# Admin Logout
# ==============================
@admin_bp.route("/logout")
@admin_login_required
def admin_logout():
    session.pop("admin", None)
    flash("Logged out successfully", "info")
    return redirect(url_for("admin.admin_login"))

# ==============================
# Root & Dashboard
# ==============================
@admin_bp.route("/")
@admin_login_required
def admin_root():
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/dashboard")
@admin_login_required
def dashboard():
    stats = {
        "courses": len(query_all(COURSES_DB, "SELECT id FROM courses")),
        "lessons": len(query_all(LESSONS_DB, "SELECT id FROM lessons")),
        "notes": len(query_all(NOTES_DB, "SELECT id FROM notes")),
        "quizzes": query_one(QUIZ_DB, "SELECT COUNT(DISTINCT topic) FROM quiz_questions")[0],
        "external_courses": len(query_all(COURSES_EXCEL_DB, "SELECT rowid FROM courses_excel")),
        "last_updated": datetime.datetime.utcnow().strftime("%d %b %Y %H:%M")
    }

    return render_template("admin/admin_dashboard.html", stats=stats)


# ==============================
# Course Management
# ==============================
@admin_bp.route("/courses")
@admin_login_required
def manage_courses():
    courses = query_all(COURSES_DB, "SELECT * FROM courses ORDER BY id DESC")
    return render_template("admin/manage_courses.html", courses=courses)

@admin_bp.route("/courses/delete/<int:course_id>", methods=["POST"])
@admin_login_required
def delete_course(course_id):
    execute(COURSES_DB, "DELETE FROM courses WHERE id=?", (course_id,))
    flash("Course deleted successfully", "success")
    return redirect(url_for("admin.manage_courses"))

# ==============================
# Lesson Management
# ==============================
@admin_bp.route("/lessons")
@admin_login_required
def manage_lessons():
    lessons = query_all(LESSONS_DB, "SELECT * FROM lessons ORDER BY id DESC")
    return render_template("admin/manage_lessons.html", lessons=lessons)

@admin_bp.route("/lessons/delete/<int:lesson_id>", methods=["POST"])
@admin_login_required
def delete_lesson(lesson_id):
    execute(LESSONS_DB, "DELETE FROM lessons WHERE id=?", (lesson_id,))
    flash("Lesson deleted successfully", "success")
    return redirect(url_for("admin.manage_lessons"))

# ==============================
# Quizz Management
# ==============================

@admin_bp.route("/quizzes")
@admin_login_required
def manage_quizzes():
    quizzes = query_all(QUIZ_DB, """
        SELECT 
            topic,
            COUNT(*) AS total_questions,
            MAX(created_at) AS created_at
        FROM quiz_questions
        GROUP BY topic
        ORDER BY created_at DESC
    """)
    return render_template("admin/manage_quizzes.html", quizzes=quizzes)

@admin_bp.route("/quizzes/delete/<topic>", methods=["POST"])
@admin_login_required
def delete_quiz(topic):
    execute(QUIZ_DB, "DELETE FROM quiz_questions WHERE topic=?", (topic,))
    flash("Quiz deleted successfully", "success")
    return redirect(url_for("admin.manage_quizzes"))

# ==============================
# Notes Management
# ==============================
@admin_bp.route("/notes")
@admin_login_required
def manage_notes():
    # Fetch all notes
    notes = query_all(NOTES_DB, """
        SELECT id, topic, title, filename, content, created_at
        FROM notes
        ORDER BY id DESC
    """)
    return render_template("admin/manage_notes.html", notes=notes)


@admin_bp.route("/notes/delete/<int:note_id>", methods=["POST"])
@admin_login_required
def delete_note(note_id):
    execute(NOTES_DB, "DELETE FROM notes WHERE id=?", (note_id,))
    flash("Note deleted successfully", "success")
    return redirect(url_for("admin.manage_notes"))


# ==============================
# External Courses
# ==============================
@admin_bp.route("/external-courses")
@admin_login_required
def external_courses():
    courses = query_all(COURSES_EXCEL_DB, "SELECT * FROM courses_excel ORDER BY rowid DESC")
    return render_template("admin/external_courses.html", courses=courses)

