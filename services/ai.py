import os
from pathlib import Path

import requests
from dotenv import load_dotenv


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def load_environment(project_root=None):
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    load_dotenv(root / ".env", override=False)
    return root


load_environment()


def ask_ai(messages):

    api_key = os.getenv("GROQ_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages
    }

    response = requests.post(
        GROQ_URL,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()

    return response.json()