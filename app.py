import os
import time
import streamlit as st
from dotenv import load_dotenv

# --- Load .env early ---
load_dotenv()

# --- Normalize Chroma telemetry flag BEFORE any chromadb import happens via submodules ---
raw = os.getenv("ANONYMIZED_TELEMETRY", "False")
os.environ["ANONYMIZED_TELEMETRY"] = (
    "FALSE" if str(raw).strip().lower() in ("0", "false", "no", "n", "off", "") else "TRUE"
)

# --- Local modules ---
from src.llm_client import get_client, MODEL  # If you adopt failover, import: from src.llm_client import chat
from src.router import classify_intent
from src.sql_engine import generate_sql, run_sql, narrate_results
from src.rag_engine import ingest_path, retrieve, synthesize_answer, fetch_few_shots
from src.memory import ConversationMemory
from src.feedback import (
    record_good_sql,
    record_bad_answer,
    record_rag_feedback,  # single import line
)

# ----------------------------
# Page / Session bootstrapping
# ----------------------------
st.set_page_config(
    page_title="Hari's – Autonomous Intelligence Orchestrator",
    page_icon="🤖",
    layout="wide",
)

# Remember up to 5 turns (per your preference)
if "mem" not in st.session_state:
    st.session_state.mem = ConversationMemory(max_turns=5)
if "history" not in st.session_state:
    st.session_state.history = []
if "last_turn" not in st.session_state:
    st.session_state.last_turn = None  # will store the most recent {intent, prompt, answer, extra}

st.title("🤖 Hari's – Autonomous Intelligence Orchestrator 🤖")

# -------------
# Sidebar (KB)
# -------------
with st.sidebar:
    st.header("Knowledge Base")
    doc = st.file_uploader("Add PDF/DOCX/TXT", type=["pdf", "docx", "txt"])
    if doc is not None:
        tmp = f"./data/docs/{doc.name}"
        os.makedirs("./data/docs", exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(doc.read())
        n = ingest_path(tmp)
        st.success(f"Ingested {n} chunks from {doc.name}")

    st.header("Settings")
    st.caption("Using HF Router (OpenAI-compatible)")
    st.text_input(
        "Model (repo:provider)",
        value=os.getenv("AIO_MODEL", "openai/gpt-oss-20b:groq"),
        key="model_show",
        disabled=True,
    )
    st.text_input(
        "Base URL",
        value=os.getenv("AIO_BASE_URL", "https://router.huggingface.co/v1"),
        disabled=True,
    )

# ---------------
# Chat container
# ---------------
chat = st.container()


def render_message(role: str, content: str):
    with chat:
        (st.chat_message("user") if role == "user" else st.chat_message("assistant")).write(content)


# Replay history
for role, content in st.session_state.history:
    render_message(role, content)


def set_last_turn(intent: str, prompt_txt: str, answer_txt: str, extra: dict):
    """Persist the most recent exchange so feedback buttons work after Streamlit re-run."""
    st.session_state.last_turn = {
        "intent": intent,
        "prompt": prompt_txt,
        "answer": answer_txt,
        "extra": extra or {},
    }


# -----------------
# Handle new input
# -----------------
prompt = st.chat_input("Ask something…")

if prompt:
    # 1) Record user message
    st.session_state.mem.add_user(prompt)
    st.session_state.history.append(("user", prompt))
    render_message("user", prompt)

    # 2) Classify intent (with robust SQL heuristic)
    try:
        intent = classify_intent(prompt)
    except Exception:
        intent = "GENERAL_CHAT"  # safe fallback

    lower = prompt.strip().lower()
    starts_like_sql = lower.startswith(
        ("select", "with", "insert", "update", "delete", "create", "drop", "alter")
    )
    if starts_like_sql:
        # Force SQL route if user pasted raw SQL (ensures it hits the read-only guard)
        intent = "QUERY_SQL"

    answer = ""
    extra = {}

    # 3) Route by intent
    if intent == "QUERY_SQL":
        # If user pasted SQL, use it directly; otherwise, generate via LLM
        if starts_like_sql:
            sql = prompt
        else:
            few = fetch_few_shots(prompt)
            gen = generate_sql(prompt, few_shots=few)
            sql = gen.get("sql")

        # DEBUG note (optional): uncomment if you want to see SQL in the UI
        # st.code(sql or "No SQL produced", language="sql")

        if sql:
            try:
                # run_sql blocks mutations and enforces read-only connection
                cols, rows = run_sql(sql)
                answer = narrate_results(prompt, cols, rows)
                extra = {"sql": sql, "rows": len(rows)}
            except Exception as e:
                # Force the exact wording on screen, regardless of exception text
                guard_text = "Only read-only SELECT queries are allowed."
                msg = str(e)
                if guard_text not in msg:
                    msg = guard_text
                answer = f"Blocked: {msg}"
                extra = {"sql": sql}
                # Also show a toast so it's obvious to users
                st.warning("Write operation blocked. Read-only mode is enforced.")
        else:
            notes = gen.get("notes", "No details.") if not starts_like_sql else "No SQL text provided."
            answer = f"Could not generate SQL: {notes}"
            extra = {"sql": None}

    elif intent == "QUERY_DOC":
        # Pure RAG path
        t0 = time.time()
        passages = retrieve(prompt, k=8, k_rerank=3)
        answer_core = synthesize_answer(prompt, passages)

        # Tag as RAG and show sources for provenance
        sources = ""
        if passages:
            src_lines = [f"- {p.get('source', 'S?')} ({p.get('file', 'unknown')})" for p in passages]
            sources = "\n\n**Sources used:**\n" + "\n".join(src_lines)

        answer = f"📘 **[RAG]**\n\n{answer_core}{sources}"

        # Persist context for feedback
        st.session_state.last_passages = passages
        st.session_state.last_rag_latency_ms = int((time.time() - t0) * 1000)
        st.session_state.last_rag_model = MODEL  # set to whatever synthesize_answer uses

    else:
        # GENERAL_CHAT – do NOT use RAG here
        from datetime import datetime

        lower_q = prompt.lower().strip()
        if "what day is today" in lower_q or ("what" in lower_q and "day" in lower_q):
            answer = datetime.now().strftime("Today is %A, %B %d, %Y.")
        else:
            # --- Using your current MODEL and raw OpenAI-compatible client ---
            client = get_client()
            system_msg = "You are a helpful, friendly assistant. Keep answers short and clear."

            # Build memory-aware messages (robust to missing methods/attrs)
            mem = st.session_state.get("mem")
            messages = []

            # Always include a system prompt
            messages.append({"role": "system", "content": system_msg})

            # Try preferred build_messages; if not, use known attributes
            if mem and hasattr(mem, "build_messages"):
                prior = mem.build_messages(system_msg=None)  # we already added system
                # Filter to only user/assistant roles to avoid duplicate system entries
                prior = [m for m in prior if m.get("role") in ("user", "assistant")]
                messages.extend(prior)
            else:
                # Fallback: common internal buffers
                prior = []
                if mem and hasattr(mem, "get_messages"):
                    prior = mem.get_messages()
                elif mem and hasattr(mem, "buffer"):
                    prior = list(mem.buffer)
                elif mem and hasattr(mem, "history"):
                    prior = list(mem.history)
                # Cap to max_turns if available; else last 10
                cap = getattr(mem, "max_turns", 10) if mem else 10
                messages.extend(prior[-cap:])

            # Append the new user prompt
            messages.append({"role": "user", "content": prompt})

            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.7,
            )
            answer = resp.choices[0].message.content.strip()

            # --- If you want automatic provider failover instead, comment the 4 lines above and use: ---
            # messages = [
            #     {"role": "system", "content": "You are a helpful, friendly assistant. Keep answers short and clear."},
            #     {"role": "user", "content": prompt},
            # ]
            # answer, provider_used = chat(messages, temperature=0.7)   # from src.llm_client import chat
            # st.caption(f"Served by: **{provider_used}**")

    # 4) Record assistant response & render
    st.session_state.mem.add_assistant(answer)
    st.session_state.history.append(("assistant", answer))
    render_message("assistant", answer)

    # 5) Persist last turn so feedback works after re-run
    set_last_turn(intent, prompt, answer, extra)

    # 6) (Optional) Block Inspector – helpful while you verify behavior
    with st.expander("🔒 Block Inspector (debug)"):
        st.write("Intent:", intent)
        st.write("Last SQL:", (extra or {}).get("sql"))
        st.write("DB PATH:", os.getenv("AIO_DB_PATH", "./db/company.sqlite"))

# ------------------------------
# Feedback UI (always rendered)
# ------------------------------
with chat:
    cols = st.columns([1, 1, 2])
    last_turn = st.session_state.get("last_turn", None)

    # Optional comment box for RAG feedback (render BEFORE buttons to capture value)
    rag_comment_key = f"rag_comment_{len(st.session_state.history)}"
    if last_turn and last_turn["intent"] == "QUERY_DOC":
        with cols[2]:
            st.caption("Optional comment for RAG feedback")
            st.text_input("Notes (optional):", key=rag_comment_key)

    # Optional comment for non-RAG bad feedback
    bad_reason_key = f"bad_reason_{len(st.session_state.history)}"
    if last_turn and last_turn["intent"] != "QUERY_DOC":
        with cols[1]:
            st.text_input("What went wrong? (optional)", key=bad_reason_key)

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
                else:
                    st.success("Thanks for the feedback!")

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
                else:
                    reason = st.session_state.get(bad_reason_key) or "unspecified"
                    record_bad_answer(last_turn["prompt"], reason)
                    st.warning("Logged. I’ll avoid repeating this mistake.")

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