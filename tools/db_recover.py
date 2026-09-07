#!/usr/bin/env python3
"""db_recover.py <file.db.corrupt> [out.db]: pull what can still be read out of a
WiCAN logger SQLite file the firmware set aside as *.corrupt.

The firmware already salvaged what it could into a fresh file with the original
name when the corruption was found; this is the second, thorough pass on a PC.
Two strategies, best first:
  1. the sqlite3 command-line shell's `.recover` (needs sqlite3 >= 3.29 on PATH)
  2. a plain Python walk: params, then records/frames in rowid order, stopping at
     the first read error — everything before it is kept.
The output is a normal logger .db (params + records + frames) that the Logger
page, wdl_dump-style tooling and any SQLite client can open. A file the firmware
salvaged on the device lacks the ts index (no temp files there for the sort);
run this script on it, or `sqlite3 file.db "CREATE INDEX records_ts ON records(ts)"`.
"""
import os
import shutil
import sqlite3
import subprocess
import sys

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS params (id INTEGER PRIMARY KEY, source TEXT, name TEXT, UNIQUE(source, name));",
    "CREATE TABLE IF NOT EXISTS records (ts INTEGER, param_id INTEGER, value REAL);",
    "CREATE INDEX IF NOT EXISTS records_ts ON records(ts);",
    "CREATE TABLE IF NOT EXISTS frames (ts INTEGER, id INTEGER, ext INTEGER, rtr INTEGER, dlc INTEGER, data BLOB);",
    "CREATE INDEX IF NOT EXISTS frames_ts ON frames(ts);",
]


def via_shell(src, dst):
    exe = shutil.which("sqlite3")
    if not exe:
        return None
    try:
        dump = subprocess.run([exe, src, ".recover"], capture_output=True, text=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"sqlite3 .recover failed: {e}")
        return None
    if dump.returncode != 0 or not dump.stdout.strip():
        return None
    if os.path.exists(dst):
        os.remove(dst)
    out = sqlite3.connect(dst)
    try:
        out.executescript(dump.stdout)
        out.commit()
    except sqlite3.Error as e:
        print(f".recover script did not load cleanly ({e}); falling back")
        out.close()
        os.remove(dst)
        return None
    n = out.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    m = out.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    out.close()
    return n, m


def copy_rows(src_cur, out, select, insert, label):
    n = 0
    try:
        rows = src_cur.execute(select)
        batch = []
        for row in rows:
            batch.append(row)
            if len(batch) >= 1000:
                out.executemany(insert, batch)
                n += len(batch)
                batch = []
        out.executemany(insert, batch)
        n += len(batch)
    except sqlite3.DatabaseError as e:
        print(f"{label}: stopped at row {n} ({e})")
    out.commit()
    return n


def via_python(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    out = sqlite3.connect(dst)
    for stmt in SCHEMA:
        out.execute(stmt)
    src_db = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    cur = src_db.cursor()
    copy_rows(cur, out, "SELECT id, source, name FROM params ORDER BY id",
              "INSERT OR IGNORE INTO params (id, source, name) VALUES (?, ?, ?)", "params")
    n = copy_rows(cur, out, "SELECT ts, param_id, value FROM records ORDER BY rowid",
                  "INSERT INTO records (ts, param_id, value) VALUES (?, ?, ?)", "records")
    m = copy_rows(cur, out, "SELECT ts, id, ext, rtr, dlc, data FROM frames ORDER BY rowid",
                  "INSERT INTO frames (ts, id, ext, rtr, dlc, data) VALUES (?, ?, ?, ?, ?, ?)", "frames")
    src_db.close()
    out.close()
    return n, m


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else (src[:-len(".corrupt")] if src.endswith(".corrupt") else src + ".recovered.db")
    if os.path.abspath(dst) == os.path.abspath(src):
        raise SystemExit("output must differ from the input")
    got = via_shell(src, dst)
    how = "sqlite3 .recover"
    if got is None:
        got = via_python(src, dst)
        how = "python row walk"
    print(f"{dst}: {got[0]} records + {got[1]} frames recovered ({how})")


if __name__ == "__main__":
    main()
