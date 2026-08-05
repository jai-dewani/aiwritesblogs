import os
import sys
import json
from datetime import datetime, timezone
import re
import urllib.request

# Helper to read topics.md
def get_topics_content():
    try:
        with open("topics.md", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No specific topics found."

def get_existing_slugs():
    blog_dir = "content/blog"
    slugs = []
    if os.path.exists(blog_dir):
        for item in os.listdir(blog_dir):
            item_path = os.path.join(blog_dir, item)
            if os.path.isdir(item_path):
                slugs.append(item)
    return slugs

def get_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"model": "gemini-3.1-pro-high", "ntfy_topic": ""}

def main():
    print("Starting AI Blog Generator (Cloud API Edition)...")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
        
    config = get_config()
    model = config.get("model", "gemini-3.1-pro-high")
    
    topics_content = get_topics_content()
    existing_slugs = get_existing_slugs()

    system_instruction = (
        "You are an ultra-deep technical blog writing agent designed by the Google Deepmind team. "
        "You write highly technical, human-sounding engineering blogs for developers. "
        "Your responses must ALWAYS be valid JSON without markdown wrapping. "
        "Never use AI buzzwords (like 'delve', 'navigate', 'landscape', 'moreover'). "
        "Never use em-dashes or hyphens for list markers (write flowing paragraphs instead). "
        "Never include signposting like 'In conclusion'. "
        "The response JSON must strictly match this schema:\n"
        "{\n"
        "  \"selected_topic\": \"<string, a one-sentence topic idea>\",\n"
        "  \"title\": \"<string, the final article title>\",\n"
        "  \"description\": \"<string, one sentence technical summary>\",\n"
        "  \"slug\": \"<string, url-friendly-slug-with-hyphens>\",\n"
        "  \"content\": \"<string, the raw markdown body of the post, without any frontmatter headers>\"\n"
        "}"
    )

    prompt = (
        "Here is the user's interest profile and topics they are actively working on:\n"
        f"{topics_content}\n\n"
        "Here are the slugs of blog posts already written:\n"
        f"{', '.join(existing_slugs) if existing_slugs else 'None'}\n\n"
        "Your task is to:\n"
        "1. Analyze the user's interest profile and the list of existing blog posts.\n"
        "2. Brainstorm and select a new, unique systems-engineering or backend architecture topic that fits their interests but has NOT been written about yet. Ensure it does not semantically match any existing blog directories.\n"
        "3. Write a deep technical blog post on that selected topic (aim for a 5 to 10-minute read, around 1000 to 1500 words, depending on the complexity of the topic).\n"
        "4. Output the result in valid JSON matching the schema precisely. Do not wrap the JSON in markdown code blocks. Return ONLY the raw JSON."
    )

    # Map your local config model to the free cloud model
    model_mapping = {
        "gemini-3.1-pro-high": "gemini-1.5-pro",
        "gemini-3.1-pro-low": "gemini-1.5-pro",
        "gemini-3.5-flash-high": "gemini-1.5-flash",
        "gemini-3.5-flash-medium": "gemini-1.5-flash",
        "gemini-3.5-flash-low": "gemini-1.5-flash",
        "gemini-3.6-flash-high": "gemini-1.5-flash",
        "gemini-3.6-flash-medium": "gemini-1.5-flash",
        "gemini-3.6-flash-low": "gemini-1.5-flash"
    }
    api_model = model_mapping.get(model, "gemini-1.5-pro")
    print(f"Calling Gemini API endpoint for model: {api_model}...")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            text_content = res_data['candidates'][0]['content']['parts'][0]['text']
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Gemini API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    text_content = text_content.strip()
    
    # Parse output JSON
    json_match = re.search(r'(\{.*\})', text_content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = text_content

    try:
        blog_data = json.loads(json_str)
    except Exception as e:
        print("ERROR: Failed to parse JSON from API output.", file=sys.stderr)
        print(f"Attempted to parse: {json_str}", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully generated post on topic: '{blog_data.get('selected_topic')}'!")

    title = blog_data.get("title", "").strip().strip('"')
    description = blog_data.get("description", "").strip().strip('"')
    slug = blog_data.get("slug", "").strip().lower()
    content = blog_data.get("content", "").strip()

    slug = slug.replace(" ", "-").replace("_", "-")
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'\-+', '-', slug)
    slug = slug.strip('-')

    if not slug or not title or not content:
        print("ERROR: Generated data contains empty required fields.", file=sys.stderr)
        sys.exit(1)

    now_iso = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    now_iso = now_iso.replace("+00:00", "Z")

    frontmatter = (
        "---\n"
        f'title: "{title}"\n'
        f'date: "{now_iso}"\n'
        f'description: "{description}"\n'
        "---\n\n"
    )

    full_markdown = frontmatter + content
    out_dir = os.path.join("content", "blog", slug)
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "index.md")

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(full_markdown)

    print(f"Saved new blog post to: {out_file}")

if __name__ == "__main__":
    main()
