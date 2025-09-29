import threading
import logging
import json
import traceback
from datetime import datetime, UTC
from functools import wraps
import os
from dotenv import load_dotenv

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from pymongo import MongoClient
from bson import ObjectId

# ===== LOAD .env FILE =====
load_dotenv()

# ===== CONFIG =====
import config
from utils import evaluation_logic, ai_grader

# Gemini client
import google.generativeai as genai

# --- Gemini AI Model Setup ---
# Try both common env var names to reduce configuration issues
_GEMINI_KEY = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
_MODEL = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    if _GEMINI_KEY and _GEMINI_KEY != "YOUR_API_KEY_HERE":
        genai.configure(api_key=_GEMINI_KEY)
        # Try multiple candidates to avoid 404 per API version
        for _mid in ['gemini-1.5-flash', 'gemini-1.5-flash-001', 'gemini-1.5-pro', 'gemini-pro']:
            try:
                _MODEL = genai.GenerativeModel(_mid)
                logger.info(f"✅ Configured Gemini model: {_mid}")
                break
            except Exception as me:
                logger.warning(f"Model candidate failed: {_mid} → {me}")
        if _MODEL is None:
            logger.warning("⚠️ No Gemini model could be initialized; falling back.")
        logger.info("✅ Successfully configured Gemini AI model from .env file.")
    else:
        logger.warning("⚠️ Gemini API key not found in .env file (set GOOGLE_API_KEY or GEMINI_API_KEY) or is a placeholder. Using fallback logic.")
except Exception as e:
    _MODEL = None
    logger.error(f"❌ Failed to configure Gemini AI model. Error: {e}")
    logger.warning("Application will use fallback logic.")


# ============================================================== #
# Helpers for LLM and fallback logic
# ============================================================== #

def _strip_markdown_fences(text: str) -> str:
    if not text: return ""
    s = text.strip()
    # Handles both ```json and ``` cases
    if s.startswith("```"):
        parts = s.splitlines()
        if len(parts) >= 2:
            # Remove the first line (e.g., ```json)
            parts = parts[1:]
            # Remove the last line if it's ```
            if parts and parts[-1].strip() == "```":
                parts = parts[:-1]
            return "\n".join(parts).strip()
    return s

# ⭐ NEW CONSOLIDATED FUNCTION
def generate_auto_materials(problem_description: str) -> dict:
    """
    Generates both pytest test cases and a scenario question in a single AI call.
    Returns a dictionary with 'scenario' and 'tests' keys.
    """
    prompt = f"""
    You are an expert educational content generator for a programming course.
    Based on the problem description below, generate two things:
    1. A simple, real-world scenario-based question that a beginner can understand.
    2. A complete Python script with pytest tests. The function to test will be in `submission.py` and named `solution`. Include at least 3 test cases: a simple case, an edge case, and another common case.

    Problem: "{problem_description}"

    Your response MUST be a valid JSON object with two keys: "scenario_question" and "tests".
    The value for "tests" should be the Python code as a string.

    Example Response Format:
    {{
      "scenario_question": "You are building a POS system for a bookstore. Create a function that calculates the total price for a customer's books, applying a 10% discount if they buy more than three books.",
      "tests": "from submission import solution\\n\\ndef test_simple():\\n    assert solution([10, 20]) == 30\\n\\ndef test_edge_case():\\n    assert solution([]) == 0"
    }}
    """
    fallback_materials = {
        "scenario": f"**Scenario:** A real-world application for: '{problem_description}'.\n\n*(This is a fallback. AI generation failed.)*",
        "tests": "from submission import solution\n\ndef test_placeholder():\n    assert True, \"This is a fallback test. AI generation failed.\"\n"
    }
    
    if not _MODEL:
        logger.warning("Model not configured, using fallback materials.")
        return fallback_materials

    try:
        logger.info(f"--- Starting combined material generation for: '{problem_description}' ---")
        resp = _MODEL.generate_content(prompt)
        raw_text = _strip_markdown_fences(resp.text or "")
        
        materials = json.loads(raw_text)
        
        scenario = materials.get("scenario_question")
        tests = materials.get("tests")
        
        if scenario and tests and "def test_" in tests:
            logger.info("✅ Successfully generated both scenario and pytest cases.")
            return {"scenario": scenario, "tests": tests}
        else:
            logger.warning("AI response was missing required keys or valid tests. Using fallback.")
            return fallback_materials
            
    except json.JSONDecodeError:
        logger.error(f"❌ Failed to decode JSON from AI response. RAW AI RESPONSE was: >>>\n{raw_text}\n<<<")
        return fallback_materials


def get_llm_feedback(
    problem_description: str,
    student_code: str,
    tests: list,
    passed: bool | None = None,
    score: int | float | None = None,
    total_tests: int | None = None
):
    """
    Generate AI feedback for a student's submission using Gemini when available.

    Parameters align with calls from routes so this function can be safely used
    in background threads. Falls back to a deterministic message if AI is not
    configured or fails.
    """
    # Basic, always-available fallback
    fallback = (
        "AI feedback is currently unavailable. "
        "Ensure your API key is configured (GOOGLE_API_KEY or GEMINI_API_KEY).\n\n"
        f"Summary: score={score}, total_tests={total_tests}, passed={bool(passed)}.\n"
        "Hint: Review failed test errors above and handle edge cases."
    )

    if _MODEL is None:
        logger.warning("Gemini model is not configured. Returning fallback feedback.")
        return fallback

    try:
        # Normalize test summaries into brief bullet points for the model
        test_points = []
        try:
            for t in tests or []:
                name = t.get('name') if isinstance(t, dict) else str(t)
                status = t.get('status') if isinstance(t, dict) else ''
                err = t.get('error') if isinstance(t, dict) else ''
                if status == 'failed' and err:
                    test_points.append(f"- {name}: failed — {err}")
                elif status:
                    test_points.append(f"- {name}: {status}")
        except Exception:
            # If tests are not in expected format, skip details gracefully
            pass

        tests_summary_block = "\n".join(test_points[:10])  # cap length

        prompt = (
            "You are an expert programming tutor. Provide concise, actionable feedback "
            "for a beginner student based on their code, the problem, and test results.\n\n"
            f"Problem:\n{problem_description}\n\n"
            f"Student Code:\n```python\n{student_code}\n```\n\n"
            f"Score: {score} / {total_tests if total_tests is not None else 'N/A'} | "
            f"Passed: {bool(passed)}\n"
            "Test Summary (truncated):\n" + (tests_summary_block or "- No detailed test results available") + "\n\n"
            "Return helpful feedback in plain text with:\n"
            "1) What works, 2) What fails and why, 3) Concrete next steps, 4) One edge case."
        )

        resp = _MODEL.generate_content(prompt)
        text = resp.text or ""
        cleaned = _strip_markdown_fences(text)
        return cleaned.strip() or fallback
    except Exception as e:
        logger.error(f"❌ Failed to get LLM feedback: {e}")
        return fallback


def estimate_time_space_complexity(problem_description, student_code):
    prompt = f'Analyze the student\'s code for complexity.\n\nProblem: {problem_description}\nCode:\n{student_code}\n\nOutput JSON like: {{"time": "O(n)", "space": "O(1)", "rationale": "Why"}}'
    if _MODEL is None: return {"time": "O(n)", "space": "O(1)", "rationale": "Default fallback"}
    try:
        resp = _MODEL.generate_content(prompt)
        return json.loads(resp.text)
    except Exception: return {"time": "O(n)", "space": "O(1)", "rationale": "Estimation failed"}


def require_role(role):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get("role") != role: return redirect(url_for(f"{role}_login"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
client = MongoClient(config.MONGO_URI)
db = client[config.DB_NAME]
submissions_collection = db[config.SUBMISSIONS_COLLECTION_NAME]
tests_collection = db[config.TESTS_COLLECTION_NAME]

@app.route('/')
def dashboard():
    return render_template("dashboard.html")

# ... (Keep all your login, logout, and student routes exactly the same) ...
@app.route('/login/student', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        session['role'] = 'student'
        session['name'] = request.form.get('name')
        session['email'] = request.form.get('email')
        return redirect(url_for('student_dashboard'))
    return render_template('student_login.html')

@app.route('/login/educator', methods=['GET', 'POST'])
def educator_login():
    if request.method == 'POST':
        name = request.form.get('name')
        code = request.form.get('access_code')
        if code != "TEACHER1223": return render_template('educator_login.html', error="Invalid access code")
        session['role'] = 'educator'
        session['name'] = name
        return redirect(url_for('educator_dashboard'))
    return render_template('educator_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('dashboard'))

@app.route('/student')
@require_role('student')
def student_dashboard():
    name = session.get('name')
    subs = list(submissions_collection.find({'student_name': name}).sort('created_at', -1))
    for s in subs: s['id'] = str(s['_id'])
    return render_template("student_dashboard.html", submissions=subs)

@app.route('/student/test/<tid>/submit', methods=['POST'])
@require_role('student')
def submit_code(tid):
    student_code = request.form.get('student_code')
    test = tests_collection.find_one({'_id': ObjectId(tid)})
    test_results = evaluation_logic.run_tests_on_code(student_code, test.get('test_cases_code', ''))
    passed = test_results.get('failed_tests_count', 0) == 0
    score = test_results.get('score', 0)
    doc = {
        'student_name': session.get('name'), 'student_email': session.get('email'), 'test_id': ObjectId(tid),
        'student_code': student_code, 'created_at': datetime.now(UTC),
        'evaluation': {'score': score, 'tests': test_results.get('tests', []), 'pass': passed}
    }
    sid = submissions_collection.insert_one(doc).inserted_id
    def _async_ai():
        fb = ai_grader.get_llm_feedback(test['problem_description'], student_code, test_results.get('tests', []), passed=passed, score=score, total_tests=test_results.get('total_tests', 0))
        comp = estimate_time_space_complexity(test['problem_description'], student_code)
        submissions_collection.update_one({'_id': sid}, {'$set': {'evaluation.ai_feedback': fb, 'evaluation.complexity': comp}})
    threading.Thread(target=_async_ai, daemon=True).start()
    return redirect(url_for('student_dashboard'))

@app.route('/educator')
@require_role('educator')
def educator_dashboard():
    tests = list(tests_collection.find({'educator_name': session.get('name')}).sort('created_at', -1))
    for t in tests: t['id'] = str(t['_id'])
    return render_template('educator_dashboard.html', tests=tests)


# ⭐ UPDATED EDUCATOR ROUTE
@app.route('/educator/tests/new', methods=['GET', 'POST'])
@require_role('educator')
def educator_create_test():
    if request.method == 'POST':
        title = request.form.get('title')
        desc = request.form.get('problem_description')
        topic = request.form.get('topic') or "General"
        code = request.form.get('access_code')
        if tests_collection.find_one({'access_code': code}):
            return render_template('educator_create_test.html', error="Access code exists")
        
        auto_materials = generate_auto_materials(desc)
        generated_tests = auto_materials.get('tests')
        scenario = auto_materials.get('scenario')

        # ✅ ADD THIS LINE FOR DEBUGGING
        logger.info(f"Generated Scenario to be saved: '{scenario}'")

        doc = {
            'title': title, 
            'problem_description': desc, 
            'test_cases_code': generated_tests,
            'scenario': scenario, 
            'topic': topic, 
            'access_code': code,
            'educator_name': session.get('name'), 
            'created_at': datetime.now(UTC)
        }
        tests_collection.insert_one(doc)
        return redirect(url_for('educator_dashboard'))
    return render_template('educator_create_test.html')


if __name__ == "__main__":
    app.run(debug=True, port=5001)