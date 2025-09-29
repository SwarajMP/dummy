# utils/ai_client.py

import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Only use valid v1 Gemini models
MODEL_CANDIDATES = [
    "models/gemini-2.5-flash",          # faster, lighter
    "models/gemini-2.5-pro",            # higher quality
    "models/gemini-pro-latest",         # always points to the latest pro
    "models/gemini-flash-latest"        # always points to the latest flash
]


class GeminiWrapper:
    """
    Wrapper around Gemini models to automatically select a working model.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._active_model = None
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            logger.warning("⚠️ No valid API key provided. AI features disabled.")
            self._disabled = True
        else:
            genai.configure(api_key=api_key)
            self._disabled = False

    def generate_content(self, prompt: str):
        if self._disabled:
            raise RuntimeError("AI model not configured due to missing API key.")

        # Use active model if already selected
        if self._active_model:
            return genai.GenerativeModel(self._active_model).generate_content(prompt)

        last_error = None
        # Try models in order
        for model_id in MODEL_CANDIDATES:
            try:
                resp = genai.GenerativeModel(model_id).generate_content(prompt)
                self._active_model = model_id
                logger.info(f"✅ Gemini model selected: {model_id}")
                return resp
            except Exception as e:
                last_error = e
                logger.warning(f"Model candidate failed: {model_id} → {e}")

        raise last_error or RuntimeError("No Gemini models available.")


# Initialize GEMINI_MODEL once for reuse
GEMINI_MODEL = GeminiWrapper(api_key=os.getenv("GEMINI_API_KEY"))
