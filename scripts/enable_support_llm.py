"""Enable the authorized Ollama model on the isolated local support application."""

from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
local = dotenv_values(root / ".env.support.local")
cloud = dotenv_values(root / ".env.support-eval.local")
key = cloud.get("OLLAMA_EVAL_API_KEY")
base_url = cloud.get("OLLAMA_EVAL_BASE_URL")
model = cloud.get("OLLAMA_EVAL_MODEL") or "qwen3.8:27b"
if not key or not base_url:
    raise SystemExit("Local Ollama credential or base URL is missing")

# The destination is intentionally fixed to the isolated local API. Never print
# the login response, settings payload, or request headers containing secrets.
with httpx.Client(base_url="http://127.0.0.1:8099", timeout=30) as client:
    auth = client.post("/api/auth/login", json={
        "username": "support_admin", "password": local["ADMIN_PASSWORD"],
    })
    auth.raise_for_status()
    headers = {"Authorization": f"Bearer {auth.json()['token']}"}
    response = client.put("/api/admin/settings", headers=headers, json={"updates": {
        "llm.base_url": base_url,
        "llm.model": model,
        "llm.api_key": key,
        "copilot.enabled": True,
        "copilot.auto_draft": True,
        "agent.escalation_message": "Передаю ваше обращение специалистам направления «{line_label}». "
        "Специалист подключится к диалогу в ближайшее время.",
        "routing.transfer_notice": "Направление обращения: {line_label} ({topic}).",
    }})
    response.raise_for_status()
print(f"Local support API configured: Ollama {model}; automatic copilot enabled.")
