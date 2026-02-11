import os, sqlite3, json, textwrap
from typing import List, Dict, Any, Tuple
from .llm_client import get_client, MODEL
from .prompts import SQL_GEN_SYSTEM, COT_NUDGE, NEGATIVE_CONSTRAINTS

DB_PATH = os.getenv("AIO_DB_PATH", "./db/company.sqlite")

SCHEMA_INTROSPECT_SQL = """
SELECT name, sql FROM sqlite_master
WHERE type IN ('table','index','view') ORDER BY 1;
"""

def get_schema() -> str:
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(SCHEMA_INTROSPECT_SQL).fetchall()
    return "\n".join([r[1] for r in rows if r[1]])

def generate_sql(question: str, few_shots: List[Dict[str,str]] = []) -> Dict[str,Any]:
    client = get_client()
    schema = get_schema()
    # Few-shot examples from Golden Queries
    examples = "\n".join(
        [f"Q: {fs['question']}\nGoodSQL: {fs['sql']}" for fs in few_shots[:3]]
    )
    user = f"""\
Question: {question}

Schema (SQLite DDL):
{schema}

Golden examples:
{examples or 'None'}

{NEGATIVE_CONSTRAINTS}
Return ONLY JSON.
"""
    messages = [
        {"role":"system","content":SQL_GEN_SYSTEM},
        {"role":"user","content":user}
    ]
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0
    )
    return json.loads(resp.choices[0].message.content)

def run_sql(sql: str) -> Tuple[List[str], List[Tuple]]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return cols, rows

def narrate_results(question: str, cols: List[str], rows: List[Tuple]) -> str:
    if not cols:
        return "Query executed."
    # Convert to simple markdown table, then ask the model to narrate briefly
    head = " | ".join(cols)
    sep  = " | ".join(["---"] * len(cols))
    body = "\n".join(" | ".join(map(lambda x: str(x), r)) for r in rows[:20])
    table_md = f"{head}\n{sep}\n{body}"
    client = get_client()
    prompt = f"""\
User question: {question}
Given the following SQL result table, provide a concise, useful answer.
If the result is too long, summarize key facts.

Table:
{table_md}
{COT_NUDGE}
"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )
    return resp.choices[0].message.content.strip()

