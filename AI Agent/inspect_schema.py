import sqlite3
from pathlib import Path

_path = Path(__file__).parent / "olist.sqlite"
conn = sqlite3.connect(f"file:{_path}?mode=ro", uri=True)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cur.fetchall()]

print("=== TABLES ===")
for t in tables:
    print(t)
print()

for t in tables:
    cur.execute(f'PRAGMA table_info("{t}")')
    cols = cur.fetchall()
    print(f"--- {t} ---")
    for c in cols:
        print(f"  {c[1]:30} {c[2]}")
    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
    n = cur.fetchone()[0]
    print(f"  (rows: {n})")
    print()

conn.close()
