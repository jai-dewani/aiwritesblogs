"""
generate_blog_cloud.py — AI blog generator using the Gemini REST API directly.

Designed to run in GitHub Actions (no local tooling required).
Reads topics.md and existing post titles, calls the Gemini API with a model
fallback chain, retries on failure, and writes the new post via blog_core.
"""

import os
import sys
import json
import re
import time
import urllib.request
import urllib.error

# Resolve the scripts/ directory so blog_core can always be imported,
# regardless of the working directory the script is called from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blog_core import (
    get_system_instruction,
    build_user_prompt,
    get_existing_posts,
    write_blog_post,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
]
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15


def get_config() -> dict:
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"ntfy_topic": ""}


def get_topics_content() -> str:
    try:
        with open("topics.md", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No specific topics found."


# ---------------------------------------------------------------------------
# Gemini API call with model fallback
# ---------------------------------------------------------------------------
def call_gemini_api(api_key: str, system_instruction: str, user_prompt: str) -> str | None:
    """
    Tries each model in FALLBACK_MODELS in order.
    Returns the raw text content on the first success, or None if all fail.
    """
    for model in FALLBACK_MODELS:
        print(f"Attempting Gemini API with model: {model}...")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": user_prompt}]}],
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"Successfully generated content using model: {model}")
                return text
        except urllib.error.HTTPError as e:
            print(
                f"Warning: HTTP {e.code} for model {model}: {e.read().decode('utf-8')}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Warning: API call failed for model {model}: {e}", file=sys.stderr)

    return None


def parse_blog_json(text: str) -> dict:
    """Extracts and parses the first JSON object from the API response."""
    json_match = re.search(r'(\{.*\})', text, re.DOTALL)
    json_str = json_match.group(1) if json_match else text
    return json.loads(json_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Starting AI Blog Generator (Cloud API Edition)...")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    topics_content = get_topics_content()
    existing_posts = get_existing_posts()
    print(f"Found {len(existing_posts)} existing blog posts.")

    system_instruction = get_system_instruction()
    user_prompt = build_user_prompt(topics_content, existing_posts)

    # Retry loop: attempt generation up to MAX_RETRIES times.
    blog_data = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\nGeneration attempt {attempt}/{MAX_RETRIES}...")
        try:
            text_content = call_gemini_api(api_key, system_instruction, user_prompt)
            if not text_content:
                raise RuntimeError("All Gemini API models returned no content.")
            blog_data = parse_blog_json(text_content.strip())
            print(f"Successfully generated post: '{blog_data.get('selected_topic')}'!")
            break
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                print(
                    f"Waiting {RETRY_DELAY_SECONDS}s before next attempt...",
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY_SECONDS)

    if blog_data is None:
        print(
            f"ERROR: All {MAX_RETRIES} generation attempts failed. Exiting.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        filepath, slug = write_blog_post(blog_data)
        print(f"Saved new blog post to: {filepath}")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
