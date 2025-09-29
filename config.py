# config.py
import os
from dotenv import load_dotenv

# Load the variables from .env into the environment
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "autologfixer"
COLLECTION_NAME = "logs"

# --- NEW ---
# Paste your Google Gemini API Key here
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Flask
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Collections
SUBMISSIONS_COLLECTION_NAME = "submissions"
TESTS_COLLECTION_NAME = "tests"

