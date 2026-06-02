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
