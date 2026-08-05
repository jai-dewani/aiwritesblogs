import os
import sys
import json
import subprocess
import urllib.request
import urllib.parse
import re
import datetime

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

    model = os.environ.get("BLOG_MODEL") or config.get("model", "gemini-3.1-pro-high")
    ntfy_topic = os.environ.get("NTFY_TOPIC") or config.get("ntfy_topic", "jaid_blogs_by_ai")

    print(f"Using model: {model}")

    # 2. Read topics.md to get the list of candidate topics and interests
    topics_file = "topics.md"
    topics_content = ""
    if os.path.exists(topics_file):
        try:
            with open(topics_file, "r", encoding="utf-8") as f:
                topics_content = f.read()
            print(f"Loaded interests and topics from {topics_file}")
        except Exception as e:
            print(f"Warning: Failed to read {topics_file}: {e}", file=sys.stderr)
    else:
        print(f"Warning: {topics_file} not found.", file=sys.stderr)

    # 3. Get list of existing blog posts (subdirectories under content/blog)
    content_dir = "content/blog"
    existing_blogs = []
    if os.path.exists(content_dir):
        existing_blogs = [
            d for d in os.listdir(content_dir)
            if os.path.isdir(os.path.join(content_dir, d)) and not d.startswith('.')
        ]
    
    print(f"Found {len(existing_blogs)} existing blog post directories.")

    # 4. Formulate prompt matching GEMINI.md rules and new instructions
    system_instruction = (
        "You are an expert systems engineer and technical writer. Your task is to write a deep technical blog post.\n"
        "The blog post must analyze the internals, design, or architecture of a system, database, protocol, runtime, or framework.\n"
        "Aim for a concise, deep dive of around 1000 to 1500 words (a 5 to 10-minute read suitable for on-the-go consumption). You may adjust this length depending on what the topic requires, but keep it highly focused and avoid fluff. Include ASCII diagrams or code snippets where helpful.\n\n"
        "Writer guidelines to ensure a natural, human-like voice (Anti-AI Tells):\n"
        "- NO Em-dashes (—). Use commas or periods.\n"
        "- NO Hyphens (-) as list markers. Convert all lists/bullets into flowing conversational paragraphs.\n"
        "- NO AI Crutch Phrases: 'Let's talk about it', 'Let's be fair', 'Here's where it gets spicy', 'Here's the thing that gets me', 'This is where things get interesting'.\n"
        "- NO Analyst Voice: 'What we're seeing is...', 'This wasn't a sudden revelation'.\n"
        "- NO Hedging: Do not say 'Whether you think X depends on Y' or 'Essentially states'. Take a firm stance.\n"
        "- NO Signposting: 'In conclusion', 'Key Takeaways', 'It's worth noting that'.\n"
        "- NO AI Vocabulary: 'Delve', 'Navigate', 'Landscape', 'Nuanced', 'Furthermore', 'Moreover', 'Additionally'.\n"
        "- NO Structural Tells: No comparison tables, no bold-colon headers, no perfect grammatical parallelism.\n"
        "- Human Stylings: Use contractions inconsistently (don't vs do not). Sound opinionated and slightly informal.\n\n"
        "Output MUST be a JSON object matching this schema:\n"
        "{\n"
        "  \"selected_topic\": \"The topic name you decided to write about\",\n"
        "  \"title\": \"A compelling, technical title for the blog post\",\n"
        "  \"slug\": \"url-friendly-slug-in-lowercase-using-hyphens\",\n"
        "  \"description\": \"One sentence technical summary of the post\",\n"
        "  \"content\": \"The full body of the blog post in Markdown format, following all writer guidelines above. Aim for around 1000 to 1500 words.\"\n"
        "}"
    )

    prompt = (
        f"{system_instruction}\n\n"
        f"Here is the user's Interest DNA and candidate topic list from topics.md:\n{topics_content}\n\n"
        f"Here are the existing blog directories already written: {json.dumps(existing_blogs)}\n\n"
        "Your task is to:\n"
        "1. Analyze the user's interest profile and the list of existing blog posts.\n"
        "2. Brainstorm and select a new, unique systems-engineering or backend architecture topic that fits their interests but has NOT been written about yet. Ensure it does not semantically match any existing blog directories.\n"
        "3. Write a deep technical blog post on that selected topic (aim for a 5 to 10-minute read, around 1000 to 1500 words, depending on the complexity of the topic).\n"
        "4. Output the result in valid JSON matching the schema precisely. Do not wrap the JSON in markdown code blocks. Return ONLY the raw JSON."
    )

    # 5. Call Antigravity CLI (agy)
    cmd = ["agy", "--model", model, "--print", prompt]
    print(f"Running Antigravity CLI: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("ERROR: agy CLI call timed out (5 minutes limit).", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("ERROR: 'agy' command not found. Please ensure the Antigravity CLI is installed and on your PATH.", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"ERROR: agy command exited with code {result.returncode}.", file=sys.stderr)
        print(f"Stderr: {result.stderr}", file=sys.stderr)
        print(f"Stdout: {result.stdout}", file=sys.stderr)
        sys.exit(1)

    text_content = result.stdout.strip()
    
    # 6. Parse output JSON
    json_match = re.search(r'(\{.*\})', text_content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = text_content

    try:
        blog_data = json.loads(json_str)
    except Exception as e:
        print("ERROR: Failed to parse JSON from agy output.", file=sys.stderr)
        print(f"Attempted to parse: {json_str}", file=sys.stderr)
        print(f"Full stdout was: {text_content}", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully generated post on topic: '{blog_data.get('selected_topic')}'!")

    # 7. Extract and sanitize fields
    title = blog_data.get("title", "").strip().strip('"')
    description = blog_data.get("description", "").strip().strip('"')
    slug = blog_data.get("slug", "").strip().lower()
    content = blog_data.get("content", "").strip()

    # Sanitize slug: lowercase, alphanumeric and hyphens only
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'\-+', '-', slug)
    slug = slug.strip('-')

    if not slug or not title or not content:
        print("ERROR: Generated data contains empty required fields.", file=sys.stderr)
        sys.exit(1)

    # 8. Write Markdown file adhering to GEMINI.md rules
    escaped_title = title.replace('"', '\\"')
    escaped_description = description.replace('"', '\\"')

    post_dir = os.path.join(content_dir, slug)
    os.makedirs(post_dir, exist_ok=True)
    
    filepath = os.path.join(post_dir, "index.md")
    
    # Get current UTC date and time in ISO format
    current_date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    frontmatter = (
        "---\n"
        f'title: "{escaped_title}"\n'
        f'date: "{current_date_str}"\n'
        f'description: "{escaped_description}"\n'
        "---\n\n"
        f"{content}\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(frontmatter)

    print(f"Successfully wrote new blog post to: {filepath}")

def get_site_url():
    try:
        with open("gatsby-config.js", "r", encoding="utf-8") as f:
            content = f.read()
            # Extract siteUrl using regex
            match = re.search(r'siteUrl:\s*`([^`]+)`', content)
            if match:
                return match.group(1).rstrip('/')
            match = re.search(r'siteUrl:\s*"([^"]+)"', content)
            if match:
                return match.group(1).rstrip('/')
            match = re.search(r"siteUrl:\s*'([^']+)'", content)
            if match:
                return match.group(1).rstrip('/')
    except Exception as e:
        print(f"Warning: Failed to parse gatsby-config.js: {e}", file=sys.stderr)
    return "https://jai-dewani.github.io/aiwritesblogs"

def send_notifications(title, slug, ntfy_topic):
    site_url = get_site_url()
    blog_url = f"{site_url}/{slug}/"
    
    if ntfy_topic:
        print(f"Sending notification via ntfy.sh for topic: {ntfy_topic}...")
        try:
            url = f"https://ntfy.sh/{ntfy_topic}"
            headers = {
                "Title": f"New Blog: {title}",
                "Click": blog_url,
                "X-Click": blog_url,
                "Priority": "4",
                "Tags": "memo,rocket"
            }
            body = f"An ultra-deep technical blog has been published!\nURL: {blog_url}"
            req = urllib.request.Request(
                url,
                data=body.encode('utf-8'),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                print("ntfy.sh notification sent successfully.")
        except Exception as e:
            print(f"Failed to send ntfy.sh notification: {e}")

if __name__ == "__main__":
    main()
