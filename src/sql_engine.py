import os
import sqlite3
import json
import re
from typing import List, Dict, Any, Tuple

from .llm_client import get_client, MODEL
from .prompts import SQL_GEN_SYSTEM, COT_NUDGE, NEGATIVE_CONSTRAINTS
print("[sql_engine] run_sql guard ACTIVE -> using read-only URI + validator") 
# ---------------------------------------------------------------------
# DB Path
# ---------------------------------------------------------------------
DB_PATH = os.getenv("AIO_DB_PATH", "./db/company.sqlite")

# ---------------------------------------------------------------------
# Optional AST Parser (sqlglot)
# ---------------------------------------------------------------------
try:
    import sqlglot
    from sqlglot import expressions as E
    HAVE_SQLGLOT = True
except Exception:
    HAVE_SQLGLOT = False

# ---------------------------------------------------------------------
# Schema Introspection SQL
# ---------------------------------------------------------------------
SCHEMA_INTROSPECT_SQL = """
SELECT name, sql FROM sqlite_master
WHERE type IN ('table','index','view') ORDER BY 1;
"""

# ---------------------------------------------------------------------
# Get DB Schema
# ---------------------------------------------------------------------
def get_schema() -> str:
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(SCHEMA_INTROSPECT_SQL).fetchall()
        return "\n".join([r[1] for r in rows if r[1]])


# ---------------------------------------------------------------------
# READ‑ONLY SQL VALIDATOR (AST + Regex Fallback)
# ---------------------------------------------------------------------
def _is_read_only_sql(sql: str) -> Tuple[bool, str]:
    """
    Return (True, "") if SQL is read‑only.
    Otherwise return (False, reason).
    """

    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return False, "Empty SQL."

    # Block multiple statements
    if re.search(r";\s*\S", s):
        return False, "Multiple SQL statements are not allowed."

    # Quick mutation blacklist
    if re.search(
        r"\b(INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT|ALTER|DROP|TRUNCATE"
        r"|CREATE|ATTACH|DETACH|VACUUM|PRAGMA)\b",
        s,
        re.IGNORECASE,
    ):
        return False, "Only read-only SELECT queries are allowed."

    # If sqlglot not installed: fallback to SELECT/WITH only
    if not HAVE_SQLGLOT:
        if re.match(r"^\s*(SELECT|WITH)\b", s, re.IGNORECASE):
            return True, ""
        return False, "Only read-only SELECT queries are allowed."

    # AST path
    try:
        ast = sqlglot.parse_one(s, read="sqlite")
    except Exception as e:
        return False, f"SQL parse error: {e}"

    # WITH should wrap a SELECT or safe set op
    def _root_ok(node):
        if isinstance(node, E.With):
            node = node.this
        return isinstance(node, (E.Select, E.Union, E.Except, E.Intersect))

    if not _root_ok(ast):
        return False, f"Statement type not allowed: {type(ast).__name__}"

    # Traverse AST for mutation nodes
    for n in ast.walk():
        if isinstance(n, (E.Insert, E.Update, E.Delete, E.Command)):
            return False, "Only read-only SELECT queries are allowed."

    return True, ""


# ---------------------------------------------------------------------
# ENFORCED READ‑ONLY EXECUTION
# ---------------------------------------------------------------------
def run_sql(sql: str) -> Tuple[List[str], List[Tuple]]:
    """
    Executes SQL ONLY if it is read‑only.
    Blocks DELETE/UPDATE/INSERT/DDL etc.
    Opens SQLite in read‑only mode + PRAGMA guard.
    """

    ok, reason = _is_read_only_sql(sql)
    if not ok:
        # Keep error EXACT for UI
        if "Only read-only" in reason:
            raise ValueError("Only read-only SELECT queries are allowed.")
        raise ValueError(f"Blocked non-read-only SQL: {reason}")

    # Open SQLite strictly read‑only
    ro_uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
    with sqlite3.connect(ro_uri, uri=True) as con:
        # final guard: SQLite cannot modify anything
        con.execute("PRAGMA query_only = ON;")

        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        return cols, rows


# ---------------------------------------------------------------------
# SQL GENERATION (unchanged)
# ---------------------------------------------------------------------
def generate_sql(question: str, few_shots: List[Dict[str, str]] = []) -> Dict[str, Any]:
    client = get_client()
    schema = get_schema()

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

Return ONLY JSON: {{"sql": "...", "notes": "..."}}.
"""

    messages = [
        {"role": "system", "content": SQL_GEN_SYSTEM},
        {"role": "user", "content": user},
    ]

    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0,
    )

    return json.loads(resp.choices[0].message.content)


# ---------------------------------------------------------------------
# NARRATION (unchanged)
# ---------------------------------------------------------------------
def narrate_results(question: str, cols: List[str], rows: List[Tuple]) -> str:
    if not cols:
        return "Query executed."

    head = " | ".join(cols)
    sep = " | ".join(["---"] * len(cols))
    body = "\n".join(" | ".join(map(str, r)) for r in rows[:20])
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
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )

    return resp.choices[0].message.content.strip()