import os
import time
import json
import hmac
import hashlib
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import streamlit as st
from dotenv import load_dotenv

# --- Load .env early ---
load_dotenv()

# --- Normalize Chroma telemetry flag BEFORE any chromadb import happens via submodules ---
raw = os.getenv("ANONYMIZED_TELEMETRY", "False")
os.environ["ANONYMIZED_TELEMETRY"] = (
    "FALSE" if str(raw).strip().lower() in ("0", "false", "no", "n", "off", "") else "TRUE"
)

# --- Local modules (existing) ---
from src.llm_client import get_client, MODEL  # If you adopt failover: from src.llm_client import chat
from src.router import classify_intent
from src.sql_engine import generate_sql, run_sql, narrate_results
from src.rag_engine import ingest_path, retrieve, synthesize_answer, fetch_few_shots
from src.memory import ConversationMemory
from src.feedback import record_good_sql, record_bad_answer, record_rag_feedback


# =========================================================
# Authentication helpers (no external packages required)
# =========================================================

def _hash_password(username: str, password: str, salt: str) -> str:
    """sha256(salt:username:password) hex digest."""
    return hashlib.sha256(f"{salt}:{username}:{password}".encode("utf-8")).hexdigest()

def _load_user_db() -> tuple[Dict[str, str], str]:
    """
    Loads users from env.
    AIO_USERS_JSON should be a JSON object: {"user1":"<hash>","user2":"<hash>"}
    AIO_AUTH_SALT is the salt used to generate those hashes.
    If none provided, creates a default admin/changeme (with default salt) and warns.
    """
    salt = os.getenv("AIO_AUTH_SALT", "change-me")
    raw = os.getenv("AIO_USERS_JSON", "").strip()

    users: Dict[str, str] = {}
    if raw:
        try:
            users = json.loads(raw)
            if not isinstance(users, dict):
                users = {}
        except Exception:
            users = {}

    if not users:
        # Fallback default (dev only) -> admin/changeme
        default_hash = _hash_password("admin", "changeme", salt)
        users = {"admin": default_hash}
        st.warning(
            "Using default credentials `admin / changeme`. "
            "Set AIO_USERS_JSON and AIO_AUTH_SALT in your .env for production.",
            icon="⚠️",
        )
    return users, salt

def authenticate(username: str, password: str) -> bool:
    users, salt = _load_user_db()
    if not username:
        return False
    stored = users.get(username)
    if not stored:
        return False
    computed = _hash_password(username, password, salt)
    return hmac.compare_digest(stored, computed)

def render_login():
    # Minimal header
    st.markdown("### 🔒 Sign in to AIO")
    st.caption("Access is restricted. Enter your credentials to continue.")

    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Username", autocomplete="username")
        p = st.text_input("Password", type="password", autocomplete="current-password")
        left, right = st.columns([1, 1])
        remember = left.checkbox("Remember me", value=False)
        submit = right.form_submit_button("Sign in", use_container_width=True)

    if submit:
        if authenticate(u, p):
            st.session_state.authenticated = True
            st.session_state.user = u
            st.session_state.remember = remember
            st.success("Signed in.")
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.markdown(
        "<div class='small-muted'>Tip: Manage users via <code>AIO_USERS_JSON</code> and "
        "<code>AIO_AUTH_SALT</code> in your <code>.env</code>.</div>",
        unsafe_allow_html=True,
    )


# =========================================================
# Appearance (Light / Dark / Auto) — no JS required
# =========================================================

# Session state defaults
if "appearance" not in st.session_state:
    st.session_state.appearance = "Auto"  # Auto | Light | Dark
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user" not in st.session_state:
    st.session_state.user = None

# Page config
st.set_page_config(
    page_title="AIO – Autonomous Intelligence Orchestrator",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Shared CSS (structure/comps, without tokens)
BASE_CSS = """
.main > div { padding-top: 0.5rem; }
.block-container { max-width: 1120px; }
.stHeading { margin-bottom: 0.25rem; }
.small-muted { font-size: 0.9rem; }

/* Chat bubbles */
.chat-bubble {
  padding: 0.75rem 1rem; border-radius: 10px; margin-bottom: 0.5rem;
  line-height: 1.45;
}

/* Badge chips */
.badge {
  display: inline-flex; align-items: center; gap: 0.35rem;
  padding: 0.15rem 0.5rem; border-radius: 999px;
  font-size: 0.8rem;
}

/* Sidebar */
[data-testid="stSidebar"] .block-container { padding-top: 1rem; }

/* Hook tokens to actual elements */
html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg);
  color: var(--text);
}
.small-muted { color: var(--text-2); }
.chat-user   { background: var(--bubble-user-bg); border: 1px solid var(--bubble-user-bd); }
.chat-assist { background: var(--bubble-assist-bg); border: 1px solid var(--bubble-assist-bd); }
.badge { border: 1px solid var(--chip-bd); background: var(--chip-bg); }
pre, code { font-size: 0.88rem; color: var(--code-fg); }
"""
st.markdown(f"<style>{BASE_CSS}</style>", unsafe_allow_html=True)

# Color token sets
LIGHT_TOKENS = """
:root {
  --bg: #FFFFFF;
  --bg-2: #F7F7FB;
  --text: #0F172A;
  --text-2: #475569;
  --accent: #4F46E5;
  --chip-bg: #F9FAFB;
  --chip-bd: #E5E7EB;
  --bubble-user-bg: #F5F7FB;
  --bubble-user-bd: #E6EAF2;
  --bubble-assist-bg: #FAFAFF;
  --bubble-assist-bd: #E9E9FD;
  --code-fg: #0B1220;
}
"""

DARK_TOKENS = """
:root {
  --bg: #0B1020;
  --bg-2: #121832;
  --text: #E6EAF2;
  --text-2: #A8B0C4;
  --accent: #8B92F9;
  --chip-bg: #161C34;
  --chip-bd: #273152;
  --bubble-user-bg: #121A33;
  --bubble-user-bd: #273152;
  --bubble-assist-bg: #141E3A;
  --bubble-assist-bd: #2B3863;
  --code-fg: #E6EAF2;
}
"""

AUTO_TOKENS = """
/* Default to light tokens */
:root {
  --bg: #FFFFFF;
  --bg-2: #F7F7FB;
  --text: #0F172A;
  --text-2: #475569;
  --accent: #4F46E5;
  --chip-bg: #F9FAFB;
  --chip-bd: #E5E7EB;
  --bubble-user-bg: #F5F7FB;
  --bubble-user-bd: #E6EAF2;
  --bubble-assist-bg: #FAFAFF;
  --bubble-assist-bd: #E9E9FD;
  --code-fg: #0B1220;
}

/* Override when OS prefers dark */
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0B1020;
    --bg-2: #121832;
    --text: #E6EAF2;
    --text-2: #A8B0C4;
    --accent: #8B92F9;
    --chip-bg: #161C34;
    --chip-bd: #273152;
    --bubble-user-bg: #121A33;
    --bubble-user-bd: #273152;
    --bubble-assist-bg: #141E3A;
    --bubble-assist-bd: #2B3863;
    --code-fg: #E6EAF2;
  }
}
"""

def apply_tokens(mode: str):
    """Inject the correct token block without JS."""
    if mode == "Dark":
        st.markdown(f"<style>{DARK_TOKENS}</style>", unsafe_allow_html=True)
    elif mode == "Light":
        st.markdown(f"<style>{LIGHT_TOKENS}</style>", unsafe_allow_html=True)
    else:  # Auto
        st.markdown(f"<style>{AUTO_TOKENS}</style>", unsafe_allow_html=True)

# Apply selected tokens early
apply_tokens(st.session_state.appearance)


# =========================================================
# Minimal sidebar (always visible) — shows theme & auth state
# =========================================================
with st.sidebar:
    st.markdown("#### Appearance")
    choice = st.radio(
        "Theme",
        ["Auto", "Light", "Dark"],
        index=["Auto", "Light", "Dark"].index(st.session_state.appearance),
        horizontal=True,
        key="appearance_radio"
    )
    if choice != st.session_state.appearance:
        st.session_state.appearance = choice
        apply_tokens(choice)  # re-inject tokens on change
        st.toast(f"Theme set to {choice}.", icon="🌓")

    st.markdown("---")
    if st.session_state.authenticated:
        st.caption(f"Signed in as **{st.session_state.user}**")
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.user = None
            st.toast("Signed out.", icon="🚪")
            st.rerun()
    else:
        st.caption("Not signed in")


# =========================================================
# If not authenticated, render login screen and exit early
# =========================================================
if not st.session_state.authenticated:
    render_login()
    st.stop()


# =========================================================
# From here on: the original AIO app (protected by login)
# =========================================================

# ----------------------------
# Session state bootstrapping
# ----------------------------
if "mem" not in st.session_state:
    st.session_state.mem = ConversationMemory(max_turns=5)  # your preference
if "history" not in st.session_state:
    st.session_state.history: List[Tuple[str, str]] = []
if "last_turn" not in st.session_state:
    st.session_state.last_turn: Optional[Dict] = None
if "feedback_log" not in st.session_state:
    st.session_state.feedback_log: List[str] = []

# ----------------------------
# Header / Hero
# ----------------------------
col_l, col_r = st.columns([1, 2], vertical_alignment="center")
with col_l:
    st.markdown("### 🤖 AIO – Autonomous Intelligence Orchestrator")
    st.caption("Smart routing across SQL · RAG · General Chat")
with col_r:
    st.markdown(
        f"""
        <div style="display:flex; gap:.5rem; justify-content:flex-end;">
          <div class="badge">🧠 Model: <b>{MODEL}</b></div>
          <div class="badge">🕒 {datetime.now().strftime("%b %d, %Y %I:%M %p")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
st.divider()

# ----------------------------
# Sidebar (KB, Settings, Controls) — visible after login
# ----------------------------
with st.sidebar:
    st.markdown("#### Knowledge Base")
    # Unique key to avoid duplicate element IDs
    doc = st.file_uploader(
        "Add PDF / DOCX / TXT",
        type=["pdf", "docx", "txt"],
        key="kb_uploader_v1"
    )
    if doc is not None:
        with st.spinner("Ingesting document…"):
            tmp = f"./data/docs/{doc.name}"
            os.makedirs("./data/docs", exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(doc.read())
            try:
                n = ingest_path(tmp)
                st.success(f"Added **{doc.name}** · {n} chunks")
            except Exception as e:
                st.warning(f"Could not ingest **{doc.name}**: {e}")

    st.markdown("---")
    st.markdown("#### Model & Router")
    st.text_input(
        "Model (repo:provider)",
        value=os.getenv("AIO_MODEL", "openai/gpt-oss-20b:groq"),
        key="model_show",
        disabled=True
    )
    st.text_input(
        "Base URL",
        value=os.getenv("AIO_BASE_URL", "https://router.huggingface.co/v1"),
        key="base_url_show",
        disabled=True
    )
    st.caption("Configured via environment variables.")

    st.markdown("---")
    st.markdown("#### App Controls")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🧹 Clear Chat"):
            st.session_state.history.clear()
            st.session_state.last_turn = None
            st.toast("Chat cleared.", icon="🧹")
    with c2:
        if st.button("♻️ Reset Memory"):
            st.session_state.mem = ConversationMemory(max_turns=5)
            st.toast("Short‑term memory reset.", icon="♻️")

# ----------------------------
# Utility renderers
# ----------------------------
def render_message(role: str, content: str):
    css_class = "chat-user" if role == "user" else "chat-assist"
    st.markdown(f'<div class="chat-bubble {css_class}">{content}</div>', unsafe_allow_html=True)

def set_last_turn(intent: str, prompt_txt: str, answer_txt: str, extra: Dict):
    st.session_state.last_turn = {
        "intent": intent,
        "prompt": prompt_txt,
        "answer": answer_txt,
        "extra": extra or {},
    }

# ----------------------------
# Main workspace with tabs
# ----------------------------
tab_chat, tab_activity = st.tabs(["💬 Chat", "📊 Activity"])

with tab_chat:
    # Replay history
    for role, content in st.session_state.history:
        render_message(role, content)

    # Chat input row
    prompt = st.chat_input("Ask anything…")
    if prompt:
        # 1) Record user message
        st.session_state.mem.add_user(prompt)
        st.session_state.history.append(("user", prompt))
        render_message("user", prompt)

        # --- UNIVERSAL PROMPT GUARDRAIL ---
        forbidden_pattern = re.compile(r'\b(delete|drop|insert|update|truncate|alter|remove)\b', re.IGNORECASE)
        
        if forbidden_pattern.search(prompt):
            # Block it immediately before routing
            answer = "Blocked: Write operations (DELETE, DROP, etc.) are not allowed for security reasons."
            intent = "BLOCKED"
            extra = {}
            st.warning("Write operation blocked. Read-only mode is enforced.", icon="🛑")
        else:
            # 2) Classify intent (Only runs if the prompt is safe)    
            try:
                intent = classify_intent(prompt)
            except Exception:
                intent = "GENERAL_CHAT"  # safe fallback

            answer = ""
            extra: Dict = {}

            # 3) Route by intent
            if intent == "QUERY_SQL":
                with st.spinner("Generating SQL and fetching results…"):
                    few = fetch_few_shots(prompt)
                    gen = generate_sql(prompt, few_shots=few)
                    sql = gen.get("sql")
                    if sql:
                        # --- NEW SAFETY GUARDRAIL ---
                        sql_lower = sql.lower().strip()
                        if forbidden_pattern.search(sql_lower):
                            answer = "Blocked: Write operations (DELETE, DROP, etc.) are not allowed. Only read-only SELECT queries are permitted."
                            extra = {"sql": sql}
                            st.warning("Write operation blocked. Read-only mode is enforced.", icon="🛑")
                        # ----------------------------
                        else:
                            try:
                                cols, rows = run_sql(sql)
                                answer = narrate_results(prompt, cols, rows)
                                extra = {"sql": sql, "rows": len(rows), "cols": cols}
                            except Exception as e:
                                answer = (
                                    f"SQL failed: {e}\n"
                                    "You may add/adjust filters and I can refine the query."
                                )
                                extra = {"sql": sql}
                    else:
                        notes = gen.get("notes", "No details.")
                        answer = f"Could not generate SQL: {notes}"
                        extra = {"sql": None}

            elif intent == "QUERY_DOC":
                with st.spinner("Retrieving knowledge and composing an answer…"):
                    t0 = time.time()
                    passages = retrieve(prompt, k=8, k_rerank=3)
                    answer_core = synthesize_answer(prompt, passages)
                    # Source list
                    sources = ""
                    if passages:
                        src_lines = [
                            f"- `{p.get('source','S?')}` · **{p.get('file','unknown')}**"
                            for p in passages
                        ]
                        sources = "\n\n**Sources**\n" + "\n".join(src_lines)
                    answer = f"**[RAG]**\n\n{answer_core}{sources}"

                    # Persist context for feedback / metrics
                    st.session_state.last_passages = passages
                    st.session_state.last_rag_latency_ms = int((time.time() - t0) * 1000)
                    st.session_state.last_rag_model = MODEL
                    extra = {
                        "latency_ms": st.session_state.last_rag_latency_ms,
                        "model": st.session_state.last_rag_model,
                    }

            else:
                # GENERAL_CHAT — avoid RAG
                lower_q = prompt.lower().strip()
                if "what day is today" in lower_q or ("what" in lower_q and "day" in lower_q):
                    answer = datetime.now().strftime("Today is %A, %B %d, %Y.")
                else:
                    with st.spinner("Thinking…"):
                        client = get_client()
                        system_msg = "You are a helpful, friendly assistant. Keep answers short and clear."
                        mem = st.session_state.get("mem")
                        messages = [{"role": "system", "content": system_msg}]

                        # Memory-aware prefill
                        if mem and hasattr(mem, "build_messages"):
                            prior = mem.build_messages(system_msg=None)
                            prior = [m for m in prior if m.get("role") in ("user", "assistant")]
                            messages.extend(prior)
                        else:
                            prior = []
                            if mem and hasattr(mem, "get_messages"):
                                prior = mem.get_messages()
                            elif mem and hasattr(mem, "buffer"):
                                prior = list(mem.buffer)
                            elif mem and hasattr(mem, "history"):
                                prior = list(mem.history)
                            cap = getattr(mem, "max_turns", 10) if mem else 10
                            messages.extend(prior[-cap:])

                        messages.append({"role": "user", "content": prompt})
                        resp = client.chat.completions.create(
                            model=MODEL,
                            messages=messages,
                            temperature=0.7,
                        )
                        answer = resp.choices[0].message.content.strip()

        # 4) Record assistant response & render (Moved outside the else block to catch BLOCKED intent)
        st.session_state.mem.add_assistant(answer)
        st.session_state.history.append(("assistant", answer))
        render_message("assistant", answer)

        # 5) Persist last turn so feedback works after re-run
        set_last_turn(intent, prompt, answer, extra)

    # ------------------------------
    # Feedback UI (always rendered)
    # ------------------------------
    st.markdown("##### Feedback")
    cols = st.columns([1, 1, 2])
    last_turn = st.session_state.get("last_turn")

    rag_comment_key = f"rag_comment_{len(st.session_state.history)}"
    bad_reason_key = f"bad_reason_{len(st.session_state.history)}"

    if last_turn and last_turn["intent"] == "QUERY_DOC":
        with cols[2]:
            st.text_input(
                "Notes (optional):",
                key=rag_comment_key,
                placeholder="e.g., relevant, missing doc, latency, etc."
            )
    elif last_turn:
        with cols[1]:
            st.text_input(
                "What went wrong? (optional)",
                key=bad_reason_key,
                placeholder="e.g., incorrect fact, unclear, off-topic"
            )

    with cols[0]:
        if st.button("👍 Good", disabled=(last_turn is None)):
            if not last_turn:
                st.info("Ask something first.")
            else:
                lt = last_turn
                if lt["intent"] == "QUERY_SQL" and lt["extra"].get("sql"):
                    record_good_sql(
                        lt["prompt"],
                        lt["extra"]["sql"],
                        summary=f"{lt['extra'].get('rows', 0)} rows",
                    )
                    st.success("Saved as a Golden Query for future few-shot prompting.")
                    st.session_state.feedback_log.append("👍 Good (SQL)")
                elif lt["intent"] == "QUERY_DOC":
                    fid = record_rag_feedback(
                        query=lt["prompt"],
                        answer=lt["answer"],
                        passages=st.session_state.get("last_passages", []),
                        rating="up",
                        comment=st.session_state.get(rag_comment_key),
                        model=st.session_state.get("last_rag_model"),
                        latency_ms=st.session_state.get("last_rag_latency_ms"),
                        meta={"route": "QUERY_DOC"},
                    )
                    st.success(f"RAG feedback saved (id: {fid[:8]}…).")
                    st.session_state.feedback_log.append("👍 Good (RAG)")
                else:
                    st.success("Thanks for the feedback!")
                    st.session_state.feedback_log.append("👍 Good")

    with cols[1]:
        if st.button("👎 Bad", disabled=(last_turn is None)):
            if last_turn:
                lt = last_turn
                if lt["intent"] == "QUERY_DOC":
                    fid = record_rag_feedback(
                        query=lt["prompt"],
                        answer=lt["answer"],
                        passages=st.session_state.get("last_passages", []),
                        rating="down",
                        comment=st.session_state.get(rag_comment_key) or "unspecified",
                        model=st.session_state.get("last_rag_model"),
                        latency_ms=st.session_state.get("last_rag_latency_ms"),
                        meta={"route": "QUERY_DOC"},
                    )
                    st.warning(f"RAG feedback logged (id: {fid[:8]}…).")
                    st.session_state.feedback_log.append("👎 Bad (RAG)")
                else:
                    reason = st.session_state.get(bad_reason_key) or "unspecified"
                    record_bad_answer(last_turn["prompt"], reason)
                    st.warning("Logged. I’ll avoid repeating this mistake.")
                    st.session_state.feedback_log.append(f"👎 Bad · {reason}")

    with cols[2]:
        if last_turn and last_turn["intent"] == "QUERY_SQL" and last_turn["extra"].get("sql"):
            corrected = st.text_input(
                "Submit corrected SQL (optional)",
                value=last_turn["extra"]["sql"],
                key=f"fix_{len(st.session_state.history)}",
            )
            if st.button("Save corrected SQL"):
                record_good_sql(last_turn["prompt"], corrected, summary="Manual correction")
                st.success("Corrected SQL saved to Golden Queries.")
                st.session_state.feedback_log.append("✔ Manual SQL correction saved")

    st.markdown("---")
    # Quick actions row
    a1, a2, a3 = st.columns([1, 1, 2])
    with a1:
        if st.session_state.get("last_turn") and st.button("📋 Show last SQL", use_container_width=True):
            sql = st.session_state["last_turn"]["extra"].get("sql")
            if sql:
                st.code(sql, language="sql")
                st.toast("SQL shown below. Copy from the code block.", icon="📋")
            else:
                st.info("No SQL available from the last turn.")
    with a2:
        if st.session_state.get("last_turn") and st.button("⬇️ Download last answer", use_container_width=True):
            ans = st.session_state["last_turn"]["answer"]
            st.download_button(
                "Download answer.txt",
                data=ans or "",
                file_name="answer.txt",
                mime="text/plain",
            )

with tab_activity:
    st.subheader("Recent Activity")
    last_turn = st.session_state.get("last_turn")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Route**")
        st.write(last_turn["intent"] if last_turn else "–")
    with c2:
        st.markdown("**Latency**")
        latency = st.session_state.get("last_rag_latency_ms")
        st.write(f"{latency} ms" if latency else "–")

    st.markdown("**Last SQL**")
    sql = last_turn["extra"].get("sql") if last_turn else None
    if sql:
        st.code(sql, language="sql")
    else:
        st.caption("No SQL generated in the most recent turn.")

    st.markdown("**Sources (RAG)**")
    passages = st.session_state.get("last_passages", [])
    if passages:
        for p in passages:
            st.markdown(
                f"- `{p.get('source','S?')}` · **{p.get('file','unknown')}** · score: {p.get('score','?')}"
            )
    else:
        st.caption("No sources yet. Ask a question that targets your uploaded documents.")

    st.markdown("**Feedback Log**")
    if st.session_state.feedback_log:
        for item in st.session_state.feedback_log[-8:][::-1]:
            st.write(f"- {item}")
    else:
        st.caption("No feedback yet.")

# Footer
st.write("")
st.caption("AIO · Autonomous Intelligence Orchestrator — Professional Edition UI")