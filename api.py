# api.py

import threading
import logging
import json
from datetime import datetime, timezone
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
from utils import evaluation_logic, ai_grader, ai_question_generator
from utils.ai_client import GEMINI_MODEL

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ------------------ AI Helper ------------------ #
def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences."""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        parts = s.splitlines()
        if len(parts) >= 2:
            parts = parts[1:]
            if parts and parts[-1].strip() == "```":
                parts = parts[:-1]
            return "\n".join(parts).strip()
    return s

def generate_auto_materials(problem_description: str) -> dict:
    """Generate scenario and pytest tests using Gemini."""
    prompt = f"""
    You are an expert educational content generator. Based on the problem below, generate two things:
    1. A simple, real-world scenario-based question a beginner can understand.
    2. A complete Python script with pytest tests. The function to test will be in a file named `submission.py` and will be called `solution`.

    **IMPORTANT**: Your response for the tests must ONLY include the `import` statements and the `def test_...` functions.
    **DO NOT** include the `def solution(...)` function itself in the test code block.

    Problem: "{problem_description}"

    Your response MUST be a valid JSON object with two keys: "scenario_question" and "tests".
    """

    fallback_materials = {
        "scenario": f"**Scenario:** A real-world application for: '{problem_description}'.\n\n*(Fallback: AI generation failed.)*",
        "tests": "from submission import solution\n\ndef test_placeholder():\n    assert True, \"This is a fallback test. AI generation failed.\""
    }

    if not GEMINI_MODEL:
        logger.warning("Model not configured, using fallback materials.")
        return fallback_materials

    try:
        logger.info(f"--- Generating materials for: '{problem_description}' ---")
        resp = GEMINI_MODEL.generate_content(prompt)
        raw_text = _strip_markdown_fences(resp.text or "")
        materials = json.loads(raw_text)
        scenario = materials.get("scenario_question")
        tests = materials.get("tests")

        if scenario and tests and "def test_" in tests:
            if "from submission import solution" not in tests:
                tests = "from submission import solution\n\n" + tests
            return {"scenario": scenario, "tests": tests}
        else:
            logger.warning("AI response missing keys or valid tests. Using fallback.")
            return fallback_materials
    except Exception as e:
        logger.error(f"❌ AI call failed: {e}")
        return fallback_materials

def require_role(role):
    """Decorator to enforce user role for routes."""
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for(f"{role}_login"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator

# ------------------ Flask & DB ------------------ #
app = Flask(__name__)
app.secret_key = config.SECRET_KEY

client = MongoClient(config.MONGO_URI)
db = client[config.DB_NAME]
submissions_collection = db[config.SUBMISSIONS_COLLECTION_NAME]
tests_collection = db[config.TESTS_COLLECTION_NAME]
logs_collection = db.get_collection("logs")

# ------------------ Public & Auth Routes ------------------ #
@app.route('/')
def dashboard():
    return render_template("dashboard.html")

@app.route('/login/student', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        session['role'] = 'student'
        session['name'] = request.form.get('name', 'Anonymous')
        session['email'] = request.form.get('email')
        return redirect(url_for('student_dashboard'))
    return render_template('student_login.html')

@app.route('/login/educator', methods=['GET', 'POST'])
def educator_login():
    if request.method == 'POST':
        name = request.form.get('name')
        code = request.form.get('access_code')
        if code != "TEACHER123":
            return render_template('educator_login.html', error="Invalid access code")
        session['role'] = 'educator'
        session['name'] = name
        return redirect(url_for('educator_dashboard'))
    return render_template('educator_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('dashboard'))

# ------------------ Student Routes ------------------ #
@app.route('/student')
@require_role('student')
def student_dashboard():
    name = session.get('name')
    subs = list(submissions_collection.find({'student_name': name}).sort('created_at', -1))
    for s in subs:
        s['id'] = str(s['_id'])
    return render_template("student_dashboard.html", submissions=subs, name=name)

@app.route('/student/access', methods=['GET', 'POST'])
@require_role('student')
def student_access_test():
    if request.method == 'POST':
        code = request.form.get('access_code', '').strip()
        test = tests_collection.find_one({'access_code': code})
        if not test:
            return render_template('student_access.html', error='Invalid access code')
        return redirect(url_for('student_test', tid=str(test['_id'])))
    return render_template('student_access.html')

@app.route('/student/test/<tid>', methods=['GET'])
@require_role('student')
def student_test(tid):
    test = tests_collection.find_one({'_id': ObjectId(tid)})
    if not test:
        return redirect(url_for('student_dashboard'))
    return render_template('student_test.html', test=test)

@app.route('/student/test/<tid>/submit', methods=['POST'])
@require_role('student')
def submit_code(tid):
    student_code = request.form.get('student_code', '').strip()
    test = tests_collection.find_one({'_id': ObjectId(tid)})
    if not test or not student_code:
        return redirect(url_for('student_dashboard'))

    test_results = evaluation_logic.run_tests_on_code(student_code, test.get('test_cases_code', ''))
    passed = test_results.get('failed_tests_count', 0) == 0 and test_results.get('total_tests', 0) > 0
    score = test_results.get('score', 0)
    
    doc = {
        'student_name': session.get('name'), 
        'student_email': session.get('email'), 
        'test_id': ObjectId(tid),
        'problem_description': test.get('problem_description', 'No description available'), 
        'student_code': student_code, 
        'created_at': datetime.now(timezone.utc),
        'evaluation': {
            'score': score, 
            'tests': test_results.get('tests', []), 
            'pass': passed,
            'total_tests': test_results.get('total_tests', 0),
            'ai_feedback': None, 
            'complexity': None
        }
    }
    sid = submissions_collection.insert_one(doc).inserted_id

    # Async AI tasks
    def _async_ai(sub_id, problem_desc, code, tests, passed_flag, score_val, total_tests):
        try:
            feedback = ai_grader.get_llm_feedback(problem_desc, code, tests, passed_flag, score_val, total_tests)
            submissions_collection.update_one(
                {'_id': sub_id}, 
                {'$set': {'evaluation.ai_feedback': feedback}}
            )
            complexity = ai_grader.estimate_time_space_complexity(problem_desc, code)
            submissions_collection.update_one(
                {'_id': sub_id}, 
                {'$set': {'evaluation.complexity': complexity}}
            )
            logger.info(f"✅ AI feedback and complexity saved for submission {sub_id}.")
        except Exception as e:
            logger.error(f"❌ Failed AI processing for {sub_id}: {e}")

    threading.Thread(target=_async_ai, args=(sid, test.get('problem_description',''), student_code, doc['evaluation']['tests'], passed, score, doc['evaluation']['total_tests']), daemon=True).start()
    return redirect(url_for('student_dashboard'))

# ------------------ Educator Routes ------------------ #
@app.route('/educator')
@require_role('educator')
def educator_dashboard():
    tests = list(tests_collection.find({'educator_name': session.get('name')}).sort('created_at', -1))
    for t in tests:
        t['id'] = str(t['_id'])
    return render_template('educator_dashboard.html', tests=tests, name=session.get('name'))

@app.route('/educator/tests/new', methods=['GET', 'POST'])
@require_role('educator')
def educator_create_test():
    if request.method == 'POST':
        title = request.form.get('title')
        user_prompt = request.form.get('problem_description')
        topic = request.form.get('topic') or "General"
        code = request.form.get('access_code')

        if not all([title, user_prompt, code]):
            return render_template('educator_create_test.html', error='All fields are required')
        if tests_collection.find_one({'access_code': code}):
            return render_template('educator_create_test.html', error="Access code exists")

        generated_questions = ai_question_generator.generate_question_variations(user_prompt)
        best_question = ai_question_generator.select_best_question(user_prompt, generated_questions)

        auto_materials = generate_auto_materials(best_question)
        generated_tests = auto_materials.get('tests')
        scenario = auto_materials.get('scenario')

        doc = {
            'title': title, 'user_prompt': user_prompt, 'problem_description': best_question,
            'test_cases_code': generated_tests, 'scenario': scenario, 'topic': topic,
            'access_code': code, 'educator_name': session.get('name'), 'created_at': datetime.now(timezone.utc)
        }
        tests_collection.insert_one(doc)
        return redirect(url_for('educator_dashboard'))
    return render_template('educator_create_test.html')

@app.route('/educator/tests/<tid>')
@require_role('educator')
def educator_view_test(tid):
    test = tests_collection.find_one({'_id': ObjectId(tid)})
    if not test:
        return redirect(url_for('educator_dashboard'))
    subs = list(submissions_collection.find({'test_id': ObjectId(tid)}).sort('created_at', -1))
    for s in subs:
        s['id'] = str(s['_id'])
    test['id'] = str(test['_id'])
    total = len(subs)
    passed_count = sum(1 for s in subs if s.get('evaluation', {}).get('pass'))
    stats = {'total': total, 'passed': passed_count, 'failed': total - passed_count}
    return render_template('educator_test.html', test=test, submissions=subs, stats=stats)

# ------------------ Test Route ------------------ #
@app.route('/test')
def test_route():
    return "🚀 Flask server is running!"

# ------------------ Main Execution ------------------ #
if __name__ == "__main__":
    print("🚀 Starting Flask server at http://127.0.0.1:5001")
    app.run(debug=True, port=5001)
