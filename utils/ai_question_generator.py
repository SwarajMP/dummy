# utils/ai_question_generator.py

import logging
import json
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from .ai_client import GEMINI_MODEL # Import the shared model

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def _strip_markdown_fences(text: str) -> str:
    """Helper to remove ```json ... ``` fences from the AI response."""
    if not text: return ""
    s = text.strip()
    if s.startswith("```"):
        parts = s.splitlines()
        if len(parts) >= 2:
            parts = parts[1:]
            if parts and parts[-1].strip() == "```":
                parts = parts[:-1]
            return "\n".join(parts).strip()
    return s

def generate_question_variations(user_prompt: str, count=5) -> list[str]:
    """
    Generates multiple question variations based on a user's keyword or prompt using Gemini.
    """
    if not GEMINI_MODEL:
        logging.warning("Gemini model not configured. Returning fallback questions.")
        return [f"Create a function that uses '{user_prompt}'." for _ in range(count)]

    raw_text = ""
    try:
        prompt = f"""
        You are an expert in creating educational programming challenges for beginners.
        Based on the following keyword or topic, generate {count} distinct real-world programming problem descriptions.
        Each problem should be a concise, one-sentence description.

        Topic: "{user_prompt}"

        Your response MUST be a valid JSON object with a single key "questions" which contains a list of the generated strings.
        Example response format:
        {{
          "questions": [
            "Write a function to calculate the total cost of items in a shopping cart.",
            "Build a program that finds the most frequent word in a text file."
          ]
        }}
        """
        logging.info(f"--- Generating question variations for: '{user_prompt}' ---")
        response = GEMINI_MODEL.generate_content(prompt)
        raw_text = _strip_markdown_fences(response.text or "")
        
        data = json.loads(raw_text)
        questions = data.get("questions", [])
        
        if not questions or not isinstance(questions, list):
            logging.warning("Gemini response did not contain a valid list of questions. Using fallback.")
            raise ValueError("Invalid response format")

        logging.info(f"✅ Successfully generated {len(questions)} question variations.")
        return questions

    except (json.JSONDecodeError, ValueError):
        logging.error(f"❌ Failed to decode JSON from Gemini response for question variations. RAW AI RESPONSE was: >>>\n{raw_text}\n<<<")
        return [f"Error generating question for {user_prompt}" for _ in range(count)]
    except Exception as e:
        logging.error(f"❌ Gemini AI call for question variations failed: {e}")
        return [f"Error generating question for {user_prompt}" for _ in range(count)]

def select_best_question(user_prompt: str, generated_questions: list[str]) -> str:
    """
    Selects the best question from a list by comparing semantic similarity to the user's original prompt.
    (This function remains unchanged).
    """
    if not generated_questions or all("Error generating question" in q for q in generated_questions):
        logging.warning("No valid questions were generated, falling back to the original user prompt.")
        return user_prompt 

    all_phrases = [user_prompt] + generated_questions
    vectorizer = TfidfVectorizer().fit_transform(all_phrases)
    vectors = vectorizer.toarray()
    cosine_similarities = cosine_similarity(vectors[0:1], vectors[1:]).flatten()
    best_question_index = cosine_similarities.argmax()
    return generated_questions[best_question_index]