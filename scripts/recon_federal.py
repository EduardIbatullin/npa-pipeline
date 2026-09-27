"""Разведка: как отличить федеральные органы."""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect("data/authorities.db")
print("columns:", [r[1] for r in conn.execute("PRAGMA table_info(authorities)")])
print("count", conn.execute("SELECT COUNT(*) FROM authorities").fetchone()[0])
print("sample block/category:", conn.execute("SELECT block, category, name FROM authorities LIMIT 3").fetchall())

for q in ["министерство финансов", "президент"]:
    rows = conn.execute(
        "SELECT name FROM authorities WHERE name_norm LIKE ? ORDER BY length(name_norm) LIMIT 10",
        (f"%{q}%",),
    ).fetchall()
    print("===", q)
    for (name,) in rows:
        print(" ", name[:100])

# PublicBlocks tree — кто федеральный
with urllib.request.urlopen("http://publication.pravo.gov.ru/api/PublicBlocks", timeout=60) as r:
    blocks = json.loads(r.read().decode())
print("\n=== PublicBlocks ===")
for b in blocks:
    print(
        b.get("name"),
        "| agencies=",
        b.get("isAgenciesOfStateAuthorities"),
        "| items=",
        len(b.get("items") or []),
    )

# SignatoryAuthorities weights — retry with httpx if needed
try:
    import httpx

    data = httpx.get(
        "http://publication.pravo.gov.ru/api/SignatoryAuthorities",
        timeout=60.0,
    ).json()
    print("\nauthorities", len(data))
    mins = [a for a in data if "министерство финансов" in a["name"].casefold()]
    print("--- minfin by weight ---")
    for a in sorted(mins, key=lambda x: -x.get("weight", 0)):
        print(a.get("weight"), a["name"][:95])
    print("--- top 10 weight ---")
    for a in sorted(data, key=lambda x: -x.get("weight", 0))[:10]:
        print(a.get("weight"), a["name"][:90])
except Exception as e:
    print("weight probe failed", e)
