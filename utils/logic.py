# utils/logic.py

import subprocess
import tempfile
import os
from pymongo import MongoClient
from bson.objectid import ObjectId
from . import ai_fixer
import config
from bson import ObjectId

# --- Database Setup ---
client = MongoClient(config.MONGO_URI)
db = client[config.DB_NAME]
logs_col = db[config.COLLECTION_NAME]

def run_script(script_path):
    """Run a Python script and return any errors."""
    result = subprocess.run(["python", script_path], capture_output=True, text=True)
    # Only treat as error if returncode != 0 (true error)
    return result.stderr.strip() if result.returncode != 0 else None

# --- Monitor script content ---
def monitor_file_content_logic(file_content):
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as temp_file:
        temp_file.write(file_content)
        temp_file_path = temp_file.name

    error_log = run_script(temp_file_path)

    if not error_log:
        # ✅ Success → cleanup file
        os.remove(temp_file_path)
        return {
            "status": "ok",
            "message": "Script ran successfully. No errors to log."
        }

    # ❌ Error → store log
    log_entry = {
        "file_path": temp_file_path,
        "error_message": error_log,
        "original_code": file_content,
        "status": "unresolved"
    }
    result = logs_col.insert_one(log_entry)

    return {
        "status": "error_logged",
        "message": "Error detected and logged from uploaded script.",
        "log_id": str(result.inserted_id)
    }

# --- Check fix ---
def check_fix_logic(log_id_str):
    try:
        log_id = ObjectId(log_id_str)
    except Exception:
        return {"status": "error", "message": "Invalid log ID format."}

    log = logs_col.find_one({"_id": log_id})
    if not log:
        return {"status": "not_found", "message": "Log not found."}

    # Use new_code if available, else fallback to file_path original code
    code_to_test = log.get("new_code")

    if code_to_test:
        # Write new_code to a temp file and run that
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as temp_file:
            temp_file.write(code_to_test)
            temp_file_path = temp_file.name

        current_error = run_script(temp_file_path)

        # Cleanup temp file
        os.remove(temp_file_path)
    else:
        # No new_code provided, fallback to original script file path
        file_path = log["file_path"]
        current_error = run_script(file_path)

    if current_error:
        return {
            "status": "still_broken",
            "message": "Script still has errors.",
            "new_error": current_error
        }

    # If script runs fine, double-check with AI
    current_code = code_to_test if code_to_test else log.get("original_code")

    is_valid_fix = ai_fixer.verify_fix_with_gemini(
        error_log=log["error_message"],
        original_code=log["original_code"],
        new_code=current_code
    )

    if is_valid_fix:
        logs_col.update_one({"_id": log_id}, {"$set": {"status": "fix_verified_pending_confirmation"}})
        return {
            "status": "fix_verified_pending_confirmation",
            "message": "AI verified fix. Call /confirm-delete with this log_id to delete.",
            "log_id": log_id_str
        }
    else:
        logs_col.update_one({"_id": log_id}, {"$set": {"status": "verification_failed"}})
        return {
            "status": "verification_failed",
            "message": "AI did not accept the fix. Log remains."
        }

# --- Confirm delete ---
def confirm_delete_logic(log_id_str, confirmation):
    if confirmation.lower() not in ['yes', 'no']:
        return {"status": "error", "message": "Confirmation must be 'yes' or 'no'."}

    try:
        log_id = ObjectId(log_id_str)
    except Exception:
        return {"status": "error", "message": "Invalid log ID format."}

    # Fetch the log
    log = logs_col.find_one({"_id": log_id})
    if not log:
        return {"status": "error", "message": "Log not found."}

    # 🚨 Only allow deletion if AI verified the fix
    if log["status"] != "fix_verified_pending_confirmation":
        return {
            "status": "blocked",
            "message": f"Cannot delete log. Current status is '{log['status']}', but it must be 'fix_verified_pending_confirmation' before deletion."
        }

    if confirmation.lower() == 'no':
        logs_col.update_one({"_id": log_id}, {"$set": {"status": "unresolved"}})
        return {"status": "deletion_cancelled", "message": "Log was not deleted."}

    # Proceed with deletion
    result = logs_col.delete_one({"_id": log_id})
    if result.deleted_count > 0:
        return {"status": "deleted", "message": "Log deleted successfully."}
    else:
        return {"status": "error", "message": "Log could not be deleted. It may have already been removed."}


def update_log_logic(log_id, new_code):
    try:
        oid = ObjectId(log_id)
    except Exception:
        return {"error": "Invalid log_id"}

    result = logs_col.update_one(
        {"_id": oid},
        {
            "$set": {
                "new_code": new_code,
                "status": "fix_attempted"
            }
        }
    )
    if result.matched_count == 0:
        return {"error": "Log not found"}
    
    return {"status": "fix_attempted", "log_id": log_id}