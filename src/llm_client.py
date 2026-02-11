import os
import time
from typing import Dict, List, Optional, Tuple, Union

from openai import OpenAI

# ---------------------------------------------------------------------
# Defaults (your existing values preserved)
# ---------------------------------------------------------------------
BASE_URL = os.getenv("AIO_BASE_URL", "https://router.huggingface.co/v1")
MODEL_BASE = os.getenv("AIO_MODEL", "openai/gpt-oss-20b")  # we will append ":provider" for failover

# Backward-compat: export a MODEL with :groq by default (so old imports don't break)
MODEL = f"{MODEL_BASE}:groq"

# Primary provider priority (free-first, fastest-first)
PROVIDERS: List[str] = [
    "groq",
    "fireworks",
    "novita",
    "together",
]

# Backoff between provider attempts (seconds)
RETRY_PAUSE = float(os.getenv("AIO_PROVIDER_RETRY_PAUSE", "0.4"))

# ---------------------------------------------------------------------
# Public helpers used by the app
# ---------------------------------------------------------------------
def get_client() -> OpenAI:
    """
    Returns an OpenAI-compatible client pointed at HF Router using HF_TOKEN.
    """
    return OpenAI(base_url=BASE_URL, api_key=os.getenv("HF_TOKEN"))

def get_model_id(provider: Optional[str] = None) -> str:
    """
    Compose the full model id with optional provider suffix, e.g.
    "openai/gpt-oss-20b:groq"
    """
    base = MODEL_BASE.split(":", 1)[0]  # strip any suffix accidentally in env
    return f"{base}:{provider}" if provider else base

# ---------------------------------------------------------------------
# Core failover call
# ---------------------------------------------------------------------
def _call_chat_once(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, str]],
    **kwargs,
):
    """
    Make a single chat.completions call.
    Raises exceptions on non-OK responses.
    """
    return client.chat.completions.create(
        model=model,
        messages=messages,
        **({"temperature": 0.7} | kwargs),
    )

def _is_credit_depleted(exc: Exception) -> bool:
    msg = str(exc)
    return (
        "402" in msg
        or "Credit balance is depleted" in msg
        or "Payment Required" in msg
        or "You have exceeded your monthly included credits" in msg
    )

def chat_with_failover(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.7,
    max_retries_per_provider: int = 1,  # reserved for future use
    include_headers: bool = True,
) -> Tuple[Union[object, str], str]:
    """
    Try HF Router providers (Groq → Fireworks → Novita → Together) for the same model.
    On success, returns (response, provider_used).
    If all fail, attempts OpenRouter (free) then Gemini (free).
    Raises RuntimeError if nothing works.

    NOTE: The first return item is the provider's response object:
          - For HF/OpenRouter: OpenAI SDK response (has .choices[0].message.content)
          - For Gemini fallback: returns the text (string) directly
    """
    # --- 1) HF Router providers ---
    hf_client = get_client()
    last_err = None

    for provider in PROVIDERS:
        model_id = get_model_id(provider)
        try:
            resp = _call_chat_once(
                hf_client, model_id, messages, temperature=temperature
            )
            provider_used = provider
            try:
                if include_headers and hasattr(resp, "response") and hasattr(resp.response, "headers"):
                    header_provider = resp.response.headers.get("x-inference-provider")
                    if header_provider:
                        provider_used = header_provider
            except Exception:
                pass
            return resp, provider_used
        except Exception as e:
            last_err = e
            # Exhausted credits or transient error → try next provider
            time.sleep(RETRY_PAUSE)
            if _is_credit_depleted(e):
                continue
            else:
                continue

    # --- 2) OpenRouter fallback (free models) ---
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        try:
            or_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=or_key,
            )
            # Choose a small/free model for fallback
            or_model = os.getenv("OPENROUTER_MODEL", "mistral/mistral-small")
            resp = or_client.chat.completions.create(
                model=or_model,
                messages=messages,
                temperature=temperature,
            )
            return resp, "openrouter"
        except Exception as e:
            last_err = e
            time.sleep(RETRY_PAUSE)

    # --- 3) Google Gemini fallback (free tier) ---
    gem_key = os.getenv("GEMINI_API_KEY")
    if gem_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gem_key)
            gem_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            gem = genai.GenerativeModel(gem_model)
            # Send only the user's last message content for a simple fallback
            user_text = None
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_text = m.get("content", "")
                    break
            if not user_text:
                user_text = messages[-1]["content"]
            out = gem.generate_content(user_text)
            return (out.text if hasattr(out, "text") else str(out)), "gemini"
        except Exception as e:
            last_err = e

    raise RuntimeError(f"All providers failed. Last error: {last_err}")

# ---------------------------------------------------------------------
# Convenience wrapper your app can call
# ---------------------------------------------------------------------
def chat(messages: List[Dict[str, str]], **kwargs) -> Tuple[str, str]:
    """
    High-level helper that returns (answer_text, provider_used).
    This keeps your app code simple and unified across providers.
    """
    resp, provider_used = chat_with_failover(messages, **kwargs)

    # HF/OpenRouter (OpenAI SDK) → extract content
    if hasattr(resp, "choices"):
        content = resp.choices[0].message.content
        return content, provider_used

    # Gemini fallback returned plain text
    if isinstance(resp, str):
        return resp, provider_used

    # Fallback catch-all
    try:
        return str(resp), provider_used
    except Exception:
        return "[No content returned]", provider_used