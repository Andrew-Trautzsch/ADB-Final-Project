"""List available Gemini models, then test the one you pick."""

import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("ERROR: GEMINI_API_KEY not found in .env")
    exit(1)

print(f"Key loaded: {api_key[:8]}...{api_key[-4:]}\n")

from google import genai

client = genai.Client(api_key=api_key)

print("Available models that support generateContent:\n")
models = [
    m for m in client.models.list()
    if "generateContent" in (m.supported_actions or [])
]

for i, m in enumerate(models, 1):
    print(f"  {i:>2}. {m.name}")

print()
choice = input("Enter model number to test (or press Enter for #1): ").strip()
index = (int(choice) - 1) if choice.isdigit() else 0
selected = models[index].name

print(f"\nTesting: {selected} ...")
response = client.models.generate_content(
    model=selected,
    contents="Reply with just: Gemini API key works!",
)
print("Response:", response.text.strip())
print("Done.")
