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
 (3,'A. Patel','HR','Analyst','2023-01-10'),
 (4,'Michael Brown','Finance','Accountant','2020-09-18'),
 (5,'Emily Davis','Marketing','Coordinator','2022-03-22'),
 (6,'Carlos Rodriguez','Engineering','Senior SDE','2019-11-05'),
 (7,'Sophia Lee','Product','Product Manager','2021-07-14'),
 (8,'David Kim','IT','Systems Administrator','2020-02-28'),
 (9,'Priya Kumar','Finance','Financial Analyst','2023-04-17'),
 (10,'Daniel White','Sales','Sales Executive','2021-10-09'),
 (11,'Olivia Green','Customer Support','Support Engineer','2022-01-26'),
 (12,'Liam Johnson','Engineering','DevOps Engineer','2020-06-30'),
 (13,'Emma Wilson','HR','Recruiter','2023-08-11'),
 (14,'Noah Williams','Legal','Compliance Officer','2019-12-03'),
 (15,'Ava Thompson','Design','UX Designer','2021-05-19');

INSERT INTO salaries VALUES
 (1, 1, 75000),
 (2, 2, 85000),
 (3, 3, 65000),
 (4, 4, 72000),
 (5, 5, 68000),
 (6, 6, 115000),
 (7, 7, 105000),
 (8, 8, 90000),
 (9, 9, 78000),
 (10, 10, 82000),
 (11, 11, 70000),
 (12, 12, 98000),
 (13, 13, 66000),
 (14, 14, 95000),
 (15, 15, 88000);

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
