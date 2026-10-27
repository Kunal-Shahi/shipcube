"""Import time and compile cost of a Python service that loads sqlglot.

Each measurement is a fresh interpreter, so it includes interpreter start-up,
which is what a Cloud Run cold start pays. Run from this directory:

    .venv/bin/python bench_import.py
"""
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

PY = sys.executable
RUNS = int(os.environ.get("RUNS", "20"))

# A query of the shape the L1 compiler will emit: CTE + window function + date_trunc.
SQL = """
WITH m AS (
  SELECT region, date_trunc('month', created_at) AS month, sum(amount) AS revenue
  FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
  WHERE c.segment IN ('smb') AND created_at >= current_date - interval '6 months'
  GROUP BY 1, 2
)
SELECT region, month, revenue,
       revenue / sum(revenue) OVER (PARTITION BY month) AS share,
       revenue / sum(revenue) OVER (PARTITION BY month)
         - lag(revenue / sum(revenue) OVER (PARTITION BY month)) OVER (PARTITION BY region ORDER BY month) AS share_mom
FROM m ORDER BY month, revenue DESC LIMIT 100
"""

SNIPPETS = {
    "bare interpreter": "pass",
    "import sqlglot": "import sqlglot",
    "import sqlglot + optimizer": "import sqlglot; import sqlglot.optimizer",
    "import fastapi": "import fastapi",
    "import fastapi + sqlglot + optimizer (service)": "import fastapi, sqlglot, sqlglot.optimizer",
}

def wall(code):
    import time
    t0 = time.perf_counter()
    subprocess.run([PY, "-c", code], check=True, capture_output=True)
    return (time.perf_counter() - t0) * 1000


def main():
    out = {"python": sys.version.split()[0]}
    import importlib.metadata as md
    out["sqlglot"] = md.version("sqlglot")
    for label, code in SNIPPETS.items():
        xs = [wall(code) for _ in range(RUNS)]
        out[f"process wall ms p50: {label}"] = round(statistics.median(xs), 1)

    # In-process compile cost after warm import, for the per-request budget.
    body = f"""
import sqlglot
from sqlglot.optimizer import optimize
sql = {SQL!r}
first = time.perf_counter(); e = sqlglot.parse_one(sql, read="duckdb"); sqlglot.transpile(sql, read="duckdb", write="bigquery"); first = (time.perf_counter() - first) * 1000
N = 200
t = time.perf_counter()
for _ in range(N):
    e = sqlglot.parse_one(sql, read="duckdb")
parse = (time.perf_counter() - t) * 1000 / N
t = time.perf_counter()
for _ in range(N):
    e.sql(dialect="bigquery")
gen = (time.perf_counter() - t) * 1000 / N
t = time.perf_counter()
for _ in range(20):
    optimize(sqlglot.parse_one(sql, read="duckdb"), schema={{"orders": {{"region": "TEXT", "created_at": "DATE", "amount": "DOUBLE", "customer_id": "INT"}}, "customers": {{"id": "INT", "segment": "TEXT"}}}}, dialect="duckdb")
opt = (time.perf_counter() - t) * 1000 / 20
import json; print(json.dumps({{"first_parse+transpile_ms": round(first, 2), "parse_ms": round(parse, 3), "generate_bigquery_ms": round(gen, 3), "optimize_ms": round(opt, 2)}}))
"""
    code = "import time\n" + body
    r = subprocess.run([PY, "-c", code], check=True, capture_output=True, text=True)
    out["per-query cost (warm)"] = json.loads(r.stdout)

    # -X importtime: top self+cumulative entries for sqlglot
    r = subprocess.run([PY, "-X", "importtime", "-c", "import sqlglot, sqlglot.optimizer"],
                       capture_output=True, text=True)
    rows = []
    for line in r.stderr.splitlines():
        if line.startswith("import time:") and "|" in line and "self" not in line:
            _, rest = line.split(":", 1)
            selfus, cum, mod = (s.strip() for s in rest.split("|"))
            rows.append((int(cum), mod))
    top = [m for m in rows if m[1].strip() in ("sqlglot", "sqlglot.optimizer")]
    out["importtime cumulative us"] = {m: c for c, m in top}
    print(json.dumps(out, indent=2))
    Path(__file__).resolve().parent.parent.joinpath("results").mkdir(exist_ok=True)
    Path(__file__).resolve().parent.parent.joinpath("results", "sqlglot.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
