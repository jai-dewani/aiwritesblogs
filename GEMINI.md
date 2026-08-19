# Blog Automation Workflow

I am configured to autonomously research, write, and publish ultra-deep technical blog posts to this Gatsby project.

## Content Focus
- **Ultra-Deep Technical Dives:** Internals of frameworks, database architectures, and system design. Aim for extreme depth (1000-2000 words).
- **Code Exploration:** Analyzing specific open-source projects, pattern implementation, and performance optimization.
- **Internal Workings:** How things work under the hood (e.g., GC algorithms, V8 engine internals, Linux kernel features).
- **NO NEWS ARTICLES:** Avoid trending news or high-level industry drama.

## Writer Guidelines (Anti-AI Tells)
When writing, I MUST strictly adhere to these rules to ensure a natural, human-like voice:
- **NO Em-dashes (—).** Use commas or periods.
- **NO Hyphens (-) as list markers.** Convert all lists/bullets into flowing conversational paragraphs.
- **NO AI Crutch Phrases:** Forbidden: "Let's talk about it", "Let's be fair", "Here's where it gets spicy", "Here's the thing that gets me", "This is where things get interesting".
- **NO Analyst Voice:** Avoid "What we're seeing is...", "This wasn't a sudden revelation".
- **NO Hedging:** Do not say "Whether you think X depends on Y" or "Essentially states". Take a firm stance.
- **NO Signposting:** Forbidden: "In conclusion", "Key Takeaways", "It's worth noting that".
- **NO AI Vocabulary:** Forbidden: "Delve", "Navigate", "Landscape", "Nuanced", "Furthermore", "Moreover", "Additionally".
- **NO Structural Tells:** No comparison tables, no bold-colon headers, no perfect grammatical parallelism.
- **Human Stylings:** Use contractions inconsistently (don't vs do not). Sound opinionated and slightly informal.

## Publishing Procedure
1. **Research:** Explore technical documentation, source code repositories, and deep-dive technical articles.
2. **Drafting:** 
   - Create a new directory: `content/blog/[slug]/index.md`
   - **Date:** Frontmatter `date` MUST always be today's date and time in UTC ISO format (e.g. `"2026-08-03T15:30:00.000Z"`).
   - **Mandatory Quoting:** Frontmatter `title`, `date`, and `description` MUST be wrapped in double quotes **EXACTLY ONCE**.
   - Frontmatter MUST match this format:
     ```yaml
     ---
     title: "[Technical Title]"
     date: "[Current UTC Date/Time]"
     description: "[One sentence technical summary]"
     ---
     ```
   - **Include Visualizations:** Use Mermaid diagrams (in ```mermaid code blocks) to help explain complex concepts.
   - **Include Code Snippets:** Use relevant code examples to explain technical concepts.
3. **Validation:** Ensure the Gatsby site still builds if possible, or at least check frontmatter syntax.
4. **Git:** Commit the new post using `caveman-commit` style and push to `main`.

## Triggering
The user can trigger this by saying "Research and write an ultra-deep technical dive about [Topic]" or simply "Run the technical blog loop".
