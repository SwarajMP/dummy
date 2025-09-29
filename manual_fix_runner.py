# manual_fix_runner.py
import subprocess
import os
import sys
from pymongo import MongoClient
from bson.objectid import ObjectId  # --- NEW: Added import for handling MongoDB ObjectIDs
import utils.ai_fixer as ai_fixer  # We will use this for AI verification
import config

# MongoDB connection
client = MongoClient(config.MONGO_URI)
db = client[config.DB_NAME]
logs_col = db[config.COLLECTION_NAME]

def run_script(script_path):
    """Runs a script and captures its output. Returns error message or None."""
    if not os.path.exists(script_path):
        return f"Error: The file '{script_path}' does not exist."
    result = subprocess.run(["python", script_path], capture_output=True, text=True)
    return result.stderr.strip() if result.returncode != 0 else None

def log_error(script_path):
    """Runs a script, and if it fails, logs the error and original code."""
    print(f"▶️  Running '{script_path}' to check for errors...")
    error_log = run_script(script_path)

    if not error_log:
        print("✅ Success! The script ran without any errors. Nothing to log.")
        return

    print(f"❌ Failure. An error was detected:\n---\n{error_log}\n---")
    
    with open(script_path, 'r') as f:
        original_code = f.read()

    existing_log = logs_col.find_one({"file_path": script_path, "status": "unresolved"})
    if existing_log:
        print(f"ℹ️  This file already has an unresolved error logged. Use 'check' command after fixing.")
        return

    log_entry = {
        "file_path": script_path,
        "error_message": error_log,
        "original_code": original_code,
        "status": "unresolved"
    }
    insert_result = logs_col.insert_one(log_entry)
    print(f"📝 Logged error to database with ID: {insert_result.inserted_id}")

def check_fixes():
    """Checks all unresolved logs to see if they have been fixed, using AI verification."""
    print("--- Checking for Manual Fixes with AI Verification ---")
    # Also check for logs that previously failed verification
    unresolved_logs = list(logs_col.find({"status": {"$in": ["unresolved", "verification_failed"]}}))

    if not unresolved_logs:
        print("✅ No unresolved logs found.")
        return

    for log in unresolved_logs:
        log_id = log["_id"]
        script_path = log["file_path"]
        print(f"\nVerifying fix for: {script_path} (Log ID: {log_id})")

        if not os.path.exists(script_path):
            print(f"⚠️  File not found. Skipping.")
            continue
            
        current_error = run_script(script_path)
        
        if current_error:
            print(f"❌ Fix not successful. The script still fails.")
            continue

        print("✅ Script ran successfully. Preparing for AI verification...")
        with open(script_path, 'r') as f:
            current_code = f.read()
        
        original_code = log["original_code"]
        original_error = log["error_message"]

        is_valid_fix = ai_fixer.verify_fix_with_gemini(original_error, original_code, current_code)
        
        if is_valid_fix:
            print("✨ AI verification successful! Gemini confirms this is a valid fix.")
            print(f"🗑️  Automatically deleting log {log_id} from the database.")
            logs_col.delete_one({"_id": log_id})
        else:
            print("⚠️  AI verification failed. Gemini does not consider this a valid fix.")
            print("The log will be retained for further review.")
            logs_col.update_one({"_id": log_id}, {"$set": {"status": "verification_failed"}})

# --- NEW FUNCTION TO HANDLE THE 'resolve' COMMAND ---
def resolve_log(log_id_str):
    """
    Manually deletes a log entry from the database by its ID.
    This is useful for closing logs that AI verification might have missed or for cleaning up.
    """
    print(f"Attempting to manually resolve and delete log: {log_id_str}")
    try:
        log_id = ObjectId(log_id_str)
    except Exception:
        print(f"❌ Error: '{log_id_str}' is not a valid log ID format.")
        return

    # Use the existing global collection object 'logs_col'
    result = logs_col.delete_one({"_id": log_id})

    if result.deleted_count > 0:
        print(f"✅ Success! Log ID '{log_id_str}' has been deleted from the database.")
    else:
        print(f"⚠️ Warning: No log found with ID '{log_id_str}'. It may have already been resolved or the ID is incorrect.")
# --- END OF NEW FUNCTION ---

def main():
    """Main CLI handler."""
    if len(sys.argv) < 2:
        # --- UPDATED USAGE INSTRUCTIONS ---
        print("Usage: python manual_fix_runner.py [log|check|resolve]")
        print("  log <path_to_script.py> : Runs a script and logs an error if it fails.")
        print("  check                     : Checks all unresolved logs for manual fixes.")
        print("  resolve <log_id>          : Manually deletes a log entry by its ID.")
        return

    command = sys.argv[1]

    if command == "log":
        if len(sys.argv) < 3:
            print("Usage: python manual_fix_runner.py log <path_to_script.py>")
            return
        script_to_log = sys.argv[2]
        log_error(script_to_log)
    elif command == "check":
        check_fixes()
    # --- NEW COMMAND HANDLING LOGIC ---
    elif command == "resolve":
        if len(sys.argv) < 3:
            print("Usage: python manual_fix_runner.py resolve <log_id>")
            return
        log_id_to_resolve = sys.argv[2]
        resolve_log(log_id_to_resolve)
    # --- END OF NEW LOGIC ---
    else:
        print(f"Unknown command: '{command}'")
# In manual_fix_runner.py

# --- BEFORE (Your old code) ---
# def run_script(script_path):
#     """Runs a script and captures its output. Returns error message or None."""
#     if not os.path.exists(script_path):
#         return f"Error: The file '{script_path}' does not exist."
#     result = subprocess.run(["python", script_path], capture_output=True, text=True)
#     return result.stderr.strip() if result.returncode != 0 else None

# --- AFTER (The new, robust version) ---
def run_script(script_path):
    """
    Runs a script with a timeout and captures its output.
    Returns error message if it crashes or hangs, otherwise returns None.
    """
    if not os.path.exists(script_path):
        return f"Error: The file '{script_path}' does not exist."
    try:
        # THE CORE FIX: Run with a timeout!
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,
            text=True,
            timeout=10  # Stop after 10 seconds
        )
        # Return error if it crashed, otherwise None
        return result.stderr.strip() if result.returncode != 0 else None
        
    except subprocess.TimeoutExpired as e:
        # Return a specific error message for timeouts
        error_output = (
            "TimeoutError: The script ran for more than 10 seconds and was terminated.\n"
            "This likely indicates an infinite loop or a very slow algorithm.\n\n"
            f"Output captured before timeout:\n{e.stdout.strip()}"
        )
        return error_output
if __name__ == "__main__":
    main()
