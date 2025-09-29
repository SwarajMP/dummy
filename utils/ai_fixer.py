# utils/ai_fixer.py
import google.generativeai as genai
import config

# --- Configure the Gemini API at the top of the file ---
try:
    genai.configure(api_key=config.GEMINI_API_KEY)
except Exception as e:
    print(f"FATAL ERROR: Could not configure Gemini API in ai_fixer.py: {e}")

def verify_fix_with_gemini(error_log, original_code, new_code):
    """
    Asks Gemini to verify if the new code is a valid fix for the original error.
    Returns True if Gemini confirms the fix, False otherwise.
    """
    model = genai.GenerativeModel('gemini-1.5-flash-latest')

    # This prompt is specifically designed to get a simple "yes" or "no" answer.
    prompt = f"""
You are a senior code reviewer. Your task is to determine if a code change is a valid fix for a given error.
Respond with only a single word: 'yes' or 'no'.

Here is the original error message:
--- ERROR LOG ---
{error_log}
--- END ERROR LOG ---

Here is the original, broken code:
--- ORIGINAL CODE ---
{original_code}
--- END ORIGINAL CODE ---

Here is the new, proposed code fix:
--- NEW CODE ---
{new_code}
--- END NEW CODE ---

Based on all the information, is the 'NEW CODE' a valid and logical fix for the 'ERROR LOG'?
Answer with only 'yes' or 'no'.
"""

    try:
        print("🤖 Asking Gemini to verify the proposed fix...")
        response = model.generate_content(prompt)
        
        decision = response.text.strip().lower()
        print(f"🧠 Gemini's verdict: '{decision}'")
        
        return "yes" in decision

    except Exception as e:
        print(f"❌ An error occurred while communicating with the Gemini API: {e}")
        return False # Default to false if the API fails