# utils/evaluation_logic.py
import subprocess
import tempfile
import os
import json
import re
import logging
from pymongo import MongoClient
from . import ai_grader
import config
import sys

# --- Database Setup ---
client = MongoClient(config.MONGO_URI)
db = client[config.DB_NAME]
submissions_col = db["submissions"]  # New collection for evaluations


def _extract_functions_and_imports(source_code: str) -> str:
    """Return a sanitized module string containing only imports, classes, and functions.

    This prevents execution of arbitrary top-level code (e.g., input()).
    If parsing fails, returns the original source_code as a fallback.
    """
    try:
        import ast
        tree = ast.parse(source_code)
        allowed_nodes = (ast.Import, ast.ImportFrom, ast.FunctionDef,
                         ast.AsyncFunctionDef, ast.ClassDef)
        new_body = []
        for node in tree.body:
            if isinstance(node, allowed_nodes):
                new_body.append(node)
        new_module = ast.Module(body=new_body, type_ignores=[])
        return ast.unparse(new_module)
    except Exception as e:
        logging.warning(f"Failed to sanitize student code, using original: {e}")
        return source_code


def run_tests_on_code(student_code, test_cases_code):
    """Run pytest on student's code + test cases and return structured results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Write sanitized student's code
        submission_path = os.path.join(temp_dir, "submission.py")
        with open(submission_path, "w", encoding="utf-8") as f:
            f.write(_extract_functions_and_imports(student_code))

        # Write test code
        test_path = os.path.join(temp_dir, "test_submission.py")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_cases_code)

        # Run pytest
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=5", test_path],
            cwd=temp_dir,
            capture_output=True,
            text=True
        )

        output = result.stdout + result.stderr

        # Extract summary (last line usually has results)
        summary_line = ""
        for line in output.splitlines():
            if re.search(r"(passed|failed|error)", line):
                summary_line = line.strip()

        passed = int(re.search(r"(\d+)\s+passed", summary_line).group(1)) if "passed" in summary_line else 0
        failed = int(re.search(r"(\d+)\s+failed", summary_line).group(1)) if "failed" in summary_line else 0
        errors = int(re.search(r"(\d+)\s+error", summary_line).group(1)) if "error" in summary_line else 0
        total = passed + failed + errors

        # Collect test details (basic for now)
        tests = []
        for line in output.splitlines():
            if line.strip().startswith("FAILED"):
                tests.append({"name": line.strip(), "status": "failed", "error": line.strip()})
            elif line.strip().startswith("PASSED"):
                tests.append({"name": line.strip(), "status": "passed"})

        score = (passed / total * 100) if total > 0 else 0

        # Handle case: no tests collected
        if total == 0:
            return {
                "score": 0,
                "total_tests": 0,
                "passed_tests": 0,
                "failed_tests_count": 0,
                "raw_output": output,
                "tests": [{"name": "collection", "status": "failed", "error": "No tests collected"}],
            }

        return {
            "score": round(score, 2),
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests_count": failed + errors,
            "raw_output": output,
            "tests": tests
        }


def evaluate_submission_logic(problem_description, student_code, test_cases_code):
    """
    Orchestrates the entire evaluation process:
    1. Run tests on student submission.
    2. Generate AI feedback.
    3. Save results to DB and return them.
    """
    # 1. Run the tests
    test_results_data = run_tests_on_code(student_code, test_cases_code)

    # 2. Get AI feedback
    ai_feedback = ai_grader.get_llm_feedback(
        problem_description,
        student_code,
        test_results_data["tests"]
    )

    # 3. Assemble the final result
    final_result = {
        "problem": problem_description,
        "student_code": student_code,
        "score": test_results_data["score"],
        "test_summary": {
            "total": test_results_data["total_tests"],
            "passed": test_results_data["passed_tests"],
            "failed": test_results_data["failed_tests_count"]
        },
        "test_details": test_results_data["tests"],
        "ai_feedback": ai_feedback,
        "status": "completed"
    }

    # 4. Save to the database
    result = submissions_col.insert_one(final_result)
    final_result["submission_id"] = str(result.inserted_id)

    return final_result