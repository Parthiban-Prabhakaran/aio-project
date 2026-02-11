# tools/init_db.py (run once)
import sqlite3, os
os.makedirs("./db", exist_ok=True)
con = sqlite3.connect("./db/company.sqlite")
cur = con.cursor()

cur.executescript("""
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS contracts;
DROP TABLE IF EXISTS salaries;                  

CREATE TABLE employees(
  id INTEGER PRIMARY KEY,
  name TEXT,
  department TEXT,
  role TEXT,
  start_date TEXT
);

CREATE TABLE salaries(
  id INTEGER PRIMARY KEY,
  employee_id INTEGER,
  salary INTEGER
);
                  
CREATE TABLE projects(
  id INTEGER PRIMARY KEY,
  name TEXT,
  client TEXT,
  start_date TEXT,
  end_date TEXT
);

CREATE TABLE contracts(
  id INTEGER PRIMARY KEY,
  employee_id INTEGER,
  project_id INTEGER,
  contract_type TEXT,
  duration_months INTEGER,
  FOREIGN KEY(employee_id) REFERENCES employees(id),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);

INSERT INTO employees VALUES
 (1,'John Doe','HR','Manager','2021-02-01'),
 (2,'Jane Smith','Engineering','SDE','2022-06-12'),
 (3,'A. Patel','HR','Analyst','2023-01-10');

INSERT INTO salaries VALUES
 (1,1,75000),
 (2,2,85000),
 (3,3,65000);

INSERT INTO projects VALUES
 (10,'Aquila','Contoso','2023-02-01','2023-12-31'),
 (11,'Zephyr','Fabrikam','2024-01-15',NULL);

INSERT INTO contracts VALUES
 (100,1,10,'Full-time',24),
 (101,2,11,'Contract',12),
 (102,3,10,'Contract',6);
""")
con.commit(); con.close()
print("DB ready at ./db/company.sqlite")
