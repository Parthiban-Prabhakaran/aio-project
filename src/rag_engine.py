import os, uuid, re
from typing import List, Dict, Any
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import CrossEncoder
from PyPDF2 import PdfReader
import docx
from chromadb.config import Settings

from .llm_client import get_client, MODEL
from .prompts import RAG_SYNTH_SYSTEM, COT_NUDGE, NEGATIVE_CONSTRAINTS

# Normalize the env var before importing chromadb so Pydantic can parse it
raw = os.getenv("ANONYMIZED_TELEMETRY", "False")
val = str(raw).strip().lower()
os.environ["ANONYMIZED_TELEMETRY"] = "FALSE" if val in ("0","false","no","n","off","") else "TRUE"

CHROMA_PATH = os.getenv("AIO_CHROMA_PATH","./data/vectorstore")
EMBED_MODEL = os.getenv("AIO_EMBED_MODEL","sentence-transformers/all-MiniLM-L6-v2")


client_chroma = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=Settings(anonymized_telemetry=False)   # <-- boolean, not string
)

embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
docs_col = client_chroma.get_or_create_collection(name="kb_docs", embedding_function=embed_fn)
golden_col = client_chroma.get_or_create_collection(name="golden_queries", embedding_function=embed_fn)

# Light-weight chunker
def _chunks(text: str, max_chars: int = 1200, overlap: int = 150) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    i = 0
    while i < len(text):
        j = min(i + max_chars, len(text))
        # try to cut at sentence end
        dot = text.rfind(". ", i, j)
        if dot == -1 or dot < i + 200:
            dot = j
        else:
            dot += 1
        chunks.append(text[i:dot].strip())
        i = max(dot - overlap, dot)
    return [c for c in chunks if c]

def ingest_path(path: str, source_prefix: str = "S") -> int:
    """Load PDF/DOCX/TXT into Chroma."""
    docs, ids, metadatas = [], [], []
    if path.lower().endswith(".pdf"):
        txt = ""
        reader = PdfReader(path)
        for p in reader.pages:
            txt += p.extract_text() or ""
    elif path.lower().endswith(".docx"):
        d = docx.Document(path)
        txt = "\n".join(p.text for p in d.paragraphs)
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()

    for idx, ch in enumerate(_chunks(txt)):
        sid = f"{source_prefix}{uuid.uuid4().hex[:8]}"
        docs.append(ch); ids.append(sid); metadatas.append({"source": sid, "file": os.path.basename(path)})
    if docs:
        docs_col.add(documents=docs, ids=ids, metadatas=metadatas)
    return len(docs)

# Reranker
_reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")  # scalar relevance score
# The model is commonly used to rerank top-k retrieved passages. [6](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)

def retrieve(query: str, k: int = 8, k_rerank: int = 3) -> List[Dict[str,Any]]:
    # vector search
    res = docs_col.query(query_texts=[query], n_results=k)
    candidates = [{"id": i, "text": d, "source": m["source"], "file": m.get("file","")}
                  for i, d, m in zip(res["ids"][0], res["documents"][0], res["metadatas"][0])]
    if not candidates: return []

    # rerank
    pairs = [(query, c["text"]) for c in candidates]
    scores = _reranker.predict(pairs)  # higher is better
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return [dict(r[0], score=float(r[1])) for r in ranked[:k_rerank]]

def synthesize_answer(query: str, passages: List[Dict[str,Any]]) -> str:
    if not passages:
        return "I don’t know based on current documents."
    ctx = "\n\n".join([f"[{p['source']}] {p['text']}" for p in passages])
    client = get_client()
    messages = [
        {"role":"system","content":RAG_SYNTH_SYSTEM},
        {"role":"user","content":f"Question: {query}\n\nRelevant passages:\n{ctx}\n{COT_NUDGE}\n{NEGATIVE_CONSTRAINTS}"}
    ]
    resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0)
    return resp.choices[0].message.content.strip()

# Golden queries store
def add_golden_example(question: str, sql: str, summary: str = ""):
    gid = uuid.uuid4().hex
    docs = [f"Q: {question}\nSQL: {sql}\nSummary: {summary}"]
    golden_col.add(ids=[gid], documents=docs, metadatas=[{"question":question, "sql": sql}])

def fetch_few_shots(question: str, k: int = 3):
    res = golden_col.query(query_texts=[question], n_results=k)
    few = []
    for doc, meta in zip(res.get("documents",[[]])[0], res.get("metadatas",[[]])[0]):
        few.append({"question": meta.get("question",""), "sql": meta.get("sql",""), "doc": doc})
    return few

def add_bad_response(question: str, reason: str):
    gid = uuid.uuid4().hex
    golden_col.add(ids=[f"bad-{gid}"], documents=[f"BAD: {question}\n{reason}"], metadatas=[{"bad": True}])
