"""
blog_core.py — Shared logic for AI blog generation scripts.

Provides:
  - get_system_instruction()  : The single source of truth for the LLM system prompt.
  - build_user_prompt()       : Builds the user-facing prompt with topics + existing posts.
  - get_existing_posts()      : Scans content/blog/* and returns {slug: title} pairs.
  - sanitize_slug()           : Cleans a slug to be URL-safe.
  - write_blog_post()         : Validates data, writes the markdown file, returns (filepath, slug).
"""

import os
import re
import datetime

# ---------------------------------------------------------------------------
# JSON schema returned by the LLM — kept here so both scripts stay in sync.
# ---------------------------------------------------------------------------
_BLOG_JSON_SCHEMA = """{
  "selected_topic": "The topic name you decided to write about",
  "title": "A compelling, technical title for the blog post",
  "slug": "url-friendly-slug-in-lowercase-using-hyphens",
  "description": "One sentence technical summary of the post",
  "content": "The full body of the blog post in Markdown format, following all writer guidelines."
}"""


def get_system_instruction() -> str:
    """Returns the full LLM system instruction, including anti-AI-tell rules and JSON schema."""
    return (
        "You are an expert systems engineer and technical writer. "
        "Your task is to write a deep technical blog post.\n"
        "The blog post must analyze the internals, design, or architecture of a system, "
        "database, protocol, runtime, or framework.\n"
        "Write as deeply or as concisely as the topic demands. "
        "Do not pad, do not cut corners. The goal is a highly focused technical read.\n"
        "CRITICAL VISUALIZATION RULE: Whenever explaining complex concepts, architectures, "
        "or workflows, you MUST include visual diagrams. Use standard Markdown Mermaid blocks "
        "(```mermaid) or highly detailed ASCII art so the reader can visually understand new topics.\n\n"
        "Writer guidelines to ensure a natural, human-like voice (Anti-AI Tells):\n"
        "- NO Em-dashes (—). Use commas or periods.\n"
        "- NO Hyphens (-) as list markers. Convert all lists/bullets into flowing conversational paragraphs.\n"
        "- NO AI Crutch Phrases: 'Let's talk about it', 'Let's be fair', "
        "'Here's where it gets spicy', 'Here's the thing that gets me', "
        "'This is where things get interesting'.\n"
        "- NO Analyst Voice: 'What we're seeing is...', 'This wasn't a sudden revelation'.\n"
        "- NO Hedging: Do not say 'Whether you think X depends on Y' or 'Essentially states'. "
        "Take a firm stance.\n"
        "- NO Signposting: 'In conclusion', 'Key Takeaways', 'It's worth noting that'.\n"
        "- NO AI Vocabulary: 'Delve', 'Navigate', 'Landscape', 'Nuanced', "
        "'Furthermore', 'Moreover', 'Additionally'.\n"
        "- NO Structural Tells: No comparison tables, no bold-colon headers, "
        "no perfect grammatical parallelism.\n"
        "- Human Stylings: Use contractions inconsistently (don't vs do not). "
        "Sound opinionated and slightly informal.\n\n"
        "Your responses must ALWAYS be valid JSON without any markdown wrapping.\n"
        f"Output MUST be a JSON object matching this schema exactly:\n{_BLOG_JSON_SCHEMA}"
    )


def get_existing_posts(content_dir: str = "content/blog") -> dict:
    """
    Scans content_dir for existing blog posts and reads the title from each
    post's frontmatter. Returns a dict of {slug: title}.

    Falls back to using the slug itself as the title if the file can't be read.
    """
    posts = {}
    if not os.path.exists(content_dir):
        return posts

    for slug in sorted(os.listdir(content_dir)):
        if slug.startswith('.'):
            continue
        post_path = os.path.join(content_dir, slug)
        if not os.path.isdir(post_path):
            continue

        index_path = os.path.join(post_path, "index.md")
        title = slug  # fallback

        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith("title:"):
                            title = stripped[6:].strip().strip('"')
                            break
            except Exception:
                pass

        posts[slug] = title

    return posts


def build_user_prompt(topics_content: str, existing_posts: dict) -> str:
    """
    Builds the user-facing prompt.

    Args:
        topics_content: Full text of topics.md.
        existing_posts: {slug: title} dict from get_existing_posts().
    """
    if existing_posts:
        posts_list = "\n".join(
            f"  - [{slug}] {title}" for slug, title in existing_posts.items()
        )
    else:
        posts_list = "  None yet."

    return (
        f"Here is the user's Interest DNA and candidate topic list from topics.md:\n"
        f"{topics_content}\n\n"
        f"Here are the existing blog posts already written (slug and human-readable title):\n"
        f"{posts_list}\n\n"
        "Your task is to:\n"
        "1. Analyze the user's interest profile and the full list of existing blog posts "
        "(paying attention to both slugs AND titles to detect semantic overlap).\n"
        "2. Select a new, unique systems-engineering or backend architecture topic that fits "
        "their interests but has NOT been written about yet. Avoid semantic overlap with "
        "existing post titles, not just exact slug matches.\n"
        "3. Write a deep technical blog post on that selected topic.\n"
        "4. Output the result in valid JSON matching the schema precisely. "
        "Do not wrap the JSON in markdown code blocks. Return ONLY the raw JSON."
    )


def sanitize_slug(slug: str) -> str:
    """Returns a lowercase, URL-safe slug (alphanumeric + hyphens only)."""
    slug = slug.strip().lower()
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'\-+', '-', slug)
    return slug.strip('-')


def write_blog_post(blog_data: dict, content_dir: str = "content/blog") -> tuple:
    """
    Validates, sanitizes, and writes the blog post to disk.

    Args:
        blog_data: Parsed JSON dict from the LLM.
        content_dir: Root directory for blog posts.

    Returns:
        (filepath, slug) on success.

    Raises:
        ValueError: If any required field (title, slug, content) is empty.
    """
    title = blog_data.get("title", "").strip().strip('"')
    description = blog_data.get("description", "").strip().strip('"')
    slug = sanitize_slug(blog_data.get("slug", ""))
    content = blog_data.get("content", "").strip()

    if not title or not slug or not content:
        raise ValueError(
            f"Generated data is missing required fields — "
            f"title={bool(title)}, slug={bool(slug)}, content={bool(content)}"
        )

    escaped_title = title.replace('"', '\\"')
    escaped_description = description.replace('"', '\\"')

    post_dir = os.path.join(content_dir, slug)
    os.makedirs(post_dir, exist_ok=True)

    filepath = os.path.join(post_dir, "index.md")
    current_date_str = (
        datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )

    markdown = (
        "---\n"
        f'title: "{escaped_title}"\n'
        f'date: "{current_date_str}"\n'
        f'description: "{escaped_description}"\n'
        "---\n\n"
        f"{content}\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)

    return filepath, slug
