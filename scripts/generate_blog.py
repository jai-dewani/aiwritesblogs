"""
generate_blog.py — AI blog generator using the local Antigravity CLI (agy).

Designed for local use via run_bot.sh.
Reads topics.md and existing post titles, calls `agy --print` with the
combined prompt, and writes the new post via blog_core.
"""

import os
import sys
import json
import subprocess
import re
import urllib.request

# Resolve the scripts/ directory so blog_core can always be imported,
# regardless of the working directory the script is called from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blog_core import (
    get_system_instruction,
    build_user_prompt,
    get_existing_posts,
    write_blog_post,
)


def main():
    # 1. Load configuration from config.json
    config_path = "config.json"
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            print(f"Loaded configuration from {config_path}")
        except Exception as e:
            print(f"Warning: Failed to load config.json: {e}", file=sys.stderr)

    model = os.environ.get("BLOG_MODEL") or config.get("model", "gemini-3.5-flash-high")
    ntfy_topic = os.environ.get("NTFY_TOPIC") or config.get("ntfy_topic", "")
    print(f"Using model: {model}")

    # 2. Read topics.md
    topics_content = ""
    if os.path.exists("topics.md"):
        try:
            with open("topics.md", "r", encoding="utf-8") as f:
                topics_content = f.read()
            print("Loaded interests and topics from topics.md")
        except Exception as e:
            print(f"Warning: Failed to read topics.md: {e}", file=sys.stderr)
    else:
        print("Warning: topics.md not found.", file=sys.stderr)

    # 3. Get existing posts with human-readable titles (via blog_core)
    existing_posts = get_existing_posts()
    print(f"Found {len(existing_posts)} existing blog posts.")

    # 4. Build combined prompt for agy CLI (system instruction + user prompt)
    combined_prompt = get_system_instruction() + "\n\n" + build_user_prompt(topics_content, existing_posts)

    # 5. Call Antigravity CLI
    cmd = ["agy", "--model", model, "--print", combined_prompt]
    print(f"Running Antigravity CLI with model: {model}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("ERROR: agy CLI call timed out (5 minute limit).", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(
            "ERROR: 'agy' command not found. "
            "Please ensure the Antigravity CLI is installed and on your PATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    if result.returncode != 0:
        print(f"ERROR: agy command exited with code {result.returncode}.", file=sys.stderr)
        print(f"Stderr: {result.stderr}", file=sys.stderr)
        print(f"Stdout: {result.stdout}", file=sys.stderr)
        sys.exit(1)

    # 6. Parse JSON from agy output
    text_content = result.stdout.strip()
    json_match = re.search(r'(\{.*\})', text_content, re.DOTALL)
    json_str = json_match.group(1) if json_match else text_content

    try:
        blog_data = json.loads(json_str)
    except Exception:
        print("ERROR: Failed to parse JSON from agy output.", file=sys.stderr)
        print(f"Full stdout was: {text_content}", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully generated post on topic: '{blog_data.get('selected_topic')}'!")

    # 7. Write blog post to disk (via blog_core)
    try:
        filepath, slug = write_blog_post(blog_data)
        print(f"Successfully wrote new blog post to: {filepath}")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Notification helpers — kept for local testing, not called from main().
# ---------------------------------------------------------------------------
def get_site_url() -> str:
    try:
        with open("gatsby-config.js", "r", encoding="utf-8") as f:
            content = f.read()
        for pattern in [
            r'siteUrl:\s*`([^`]+)`',
            r'siteUrl:\s*"([^"]+)"',
            r"siteUrl:\s*'([^']+)'",
        ]:
            match = re.search(pattern, content)
            if match:
                return match.group(1).rstrip('/')
    except Exception as e:
        print(f"Warning: Failed to parse gatsby-config.js: {e}", file=sys.stderr)
    return "https://jai-dewani.github.io/aiwritesblogs"


def send_notifications(title: str, slug: str, ntfy_topic: str):
    site_url = get_site_url()
    blog_url = f"{site_url}/{slug}/"

    if ntfy_topic:
        print(f"Sending notification via ntfy.sh for topic: {ntfy_topic}...")
        try:
            url = f"https://ntfy.sh/{ntfy_topic}"
            headers = {
                "Title": "A new blog published",
                "Click": blog_url,
                "X-Click": blog_url,
                "Priority": "4",
                "Tags": "memo,rocket",
            }
            req = urllib.request.Request(
                url,
                data=title.encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as _:
                print("ntfy.sh notification sent successfully.")
        except Exception as e:
            print(f"Failed to send ntfy.sh notification: {e}")


if __name__ == "__main__":
    main()
