import os
from pathlib import Path

import requests
from dotenv import load_dotenv


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")


def load_environment(project_root=None):
    repo_root = Path(__file__).resolve().parents[1].resolve()
    candidate_root = Path(project_root).resolve() if project_root else repo_root
    env_path = candidate_root / ".env"
    if not env_path.exists():
        env_path = repo_root / ".env"
    load_dotenv(env_path, override=False)
    return repo_root


load_environment()


def ask_ai(messages, model=None, max_tokens=None):
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list of chat message objects")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your environment or project .env file.")

    payload = {
        "model": model or os.getenv("GROQ_MODEL", GROQ_MODEL),
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(5, 60),
    )

    response.raise_for_status()
    return response.json()
