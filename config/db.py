# db.py
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("arbi_data.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    buy_ex TEXT,
    sell_ex TEXT,
    spread REAL,
    volume REAL,
    timestamp TEXT
)
""")
conn.commit()

def save_signal(symbol, buy_ex, sell_ex, spread, volume):
    cur.execute("""
    INSERT INTO signals (symbol, buy_ex, sell_ex, spread, volume, timestamp)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (symbol, buy_ex, sell_ex, spread, volume, datetime.now(timezone.utc).isoformat()))
    conn.commit()
