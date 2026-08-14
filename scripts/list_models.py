import os
import json
import urllib.request
import sys

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Please set GEMINI_API_KEY environment variable.")
    sys.exit(1)

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
try:
    req = urllib.request.urlopen(url)
    data = json.loads(req.read().decode('utf-8'))
    models = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
    print("AVAILABLE MODELS FOR GENERATION:")
    for m in models:
        print(f" - {m}")
except Exception as e:
    print(f"Failed to fetch models: {e}")
