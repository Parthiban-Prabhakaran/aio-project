import json
from .llm_client import get_client, MODEL
from .prompts import ROUTER_SYSTEM

def classify_intent(latest_user_message: str) -> str:
    client = get_client()
    msg = [{"role":"system","content":ROUTER_SYSTEM},
           {"role":"user","content":latest_user_message}]
    resp = client.chat.completions.create(model=MODEL, messages=msg, temperature=0)
    try:
        data = json.loads(resp.choices[0].message.content.strip())
        intent = data.get("intent","GENERAL_CHAT")
        if intent not in {"QUERY_SQL","QUERY_DOC","GENERAL_CHAT"}:
            return "GENERAL_CHAT"
        return intent
    except Exception:
        return "GENERAL_CHAT"
