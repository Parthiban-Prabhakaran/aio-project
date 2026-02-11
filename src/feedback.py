from .rag_engine import add_golden_example, add_bad_response

def record_good_sql(question: str, sql: str, summary: str = ""):
    add_golden_example(question, sql, summary)

def record_bad_answer(question: str, reason: str):
    add_bad_response(question, reason)

# src/feedback.py
from .rag_engine import add_golden_example, add_bad_response  # (if these are really here)
# If record_good_sql / record_bad_answer are in this file too, make sure imports are consistent.

import os
import json
import time
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

# Ensure feedback directory exists - use project relative path
FEEDBACK_DIR = "./data/feedback"
RAG_FEEDBACK_PATH = os.path.join(FEEDBACK_DIR, "rag_feedback.jsonl")
os.makedirs(FEEDBACK_DIR, exist_ok=True)

def _write_jsonl(path: str, record: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def record_rag_feedback(
    query: str,
    answer: str,
    passages: Optional[List[dict]],
    rating: str,                       # 'up' | 'down'
    comment: Optional[str] = None,
    *,
    model: Optional[str] = None,
    latency_ms: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Persist feedback for RAG answers to a local JSONL file.
    `passages` can be what your retriever returns (expects keys like: source, file, page, score, id/snippet/text).
    Returns the feedback id.
    """

    def to_citation(p: dict) -> dict:
        if not isinstance(p, dict):
            return {}
        return {
            "source": p.get("source"),
            "file": p.get("file"),
            "page": p.get("page"),
            "score": p.get("score"),
            "chunk_id": p.get("id") or p.get("chunk_id"),
            "snippet": (p.get("snippet") or p.get("text") or "")[:512],
        }

    # Unique & timestamps
    fid = str(uuid.uuid4())
    ts_ms = int(time.time() * 1000)

    citations = [to_citation(p) for p in (passages or [])]
    doc_ids = list({
        (p.get("file") or p.get("source"))
        for p in (passages or [])
        if isinstance(p, dict) and (p.get("file") or p.get("source"))
    })
    retriever_scores = [
        {"chunkId": (p.get("id") or p.get("chunk_id")), "score": p.get("score")}
        for p in (passages or [])
        if isinstance(p, dict) and p.get("score") is not None
    ]

    record = {
        "id": fid,
        "ts": ts_ms,
        "ts_iso": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        "response": answer,
        "rating": rating,      # 'up' | 'down'
        "comment": comment,
        "doc_ids": doc_ids,
        "citations": citations,
        "retriever_scores": retriever_scores,
        "model": model,
        "latency_ms": latency_ms,
        "meta": meta or {},
    }

    _write_jsonl(RAG_FEEDBACK_PATH, record)
    return fid
