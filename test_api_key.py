import os
import json
from dotenv import load_dotenv
import google.generativeai as genai

# Load .env
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("❌ GEMINI_API_KEY not found in .env")

genai.configure(api_key=API_KEY)

try:
    model = genai.GenerativeModel("gemini-2.5-flash")
    print("✅ Gemini model initialized successfully!")
except Exception as e:
    print("❌ Failed to initialize model:", e)
    exit(1)

# Test a quiz generation
prompt = "Generate 3 multiple-choice questions on Python programming in JSON format with fields: question, options, correct_answer"

try:
    response = model.generate_content(prompt)
    text = getattr(response, "text", "")
    print("Raw response:\n", text)

    # Try to parse JSON
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text else text

    quiz = json.loads(text)
    print("✅ Parsed quiz JSON successfully:")
    print(json.dumps(quiz, indent=2))

except Exception as e:
    print("❌ Failed to generate quiz or parse JSON:", e)


