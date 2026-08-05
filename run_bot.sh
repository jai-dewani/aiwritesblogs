#!/bin/bash

# Navigate to the project directory
cd "/Users/jaikumardewani/Projects/AI blogs"

# Configure standard path to include user-installed tools and python3
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH"

echo "=========================================="
echo "Starting Blog Automation: $(date)"
echo "=========================================="

# Run the python generation script
python3 scripts/generate_blog.py

# Capture exit code of the python script
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "Generation script finished successfully. Checking git status..."
  
  # Find any new files under content/blog/
  # E.g. ?? content/blog/my-slug/index.md
  NEW_FILE=$(git status --porcelain content/blog/ | grep -E '^\?\?|^ A' | head -n 1 | awk '{print $2}')
  
  if [ -n "$NEW_FILE" ]; then
    # Extract slug (parent directory of the file)
    SLUG=$(basename "$(dirname "$NEW_FILE")")
    
    echo "New blog post detected for slug: '$SLUG'"
    echo "Staging files..."
    git add content/blog/
    
    # Use caveman-commit style message
    COMMIT_MSG="add blog post about $SLUG"
    echo "Committing with message: '$COMMIT_MSG'"
    git commit -m "$COMMIT_MSG"
    
    echo "Pushing to GitHub..."
    git push origin main
    
    echo "Done! Post pushed and deployment triggered on GitHub Pages."
  else
    echo "No new blog posts were found in git status."
  fi
else
  echo "ERROR: Generation script failed with exit code $EXIT_CODE."
fi

echo "=========================================="
echo "Blog Automation Completed: $(date)"
echo "=========================================="
