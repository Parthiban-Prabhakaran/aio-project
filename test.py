
from src.sql_engine import run_sql

try:
    run_sql("DELETE FROM employees WHERE name = 'Jane Smith';")
    print("UNEXPECTED: delete ran")
except Exception as e:
    print("OK blocked:", e)

cols, rows = run_sql("SELECT 1 AS x;")
print("OK select:", cols, rows)

