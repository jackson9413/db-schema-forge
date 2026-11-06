#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite session log - every review is kept locally, nothing leaves the box."""

import json
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "schema_forge.db")


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                profile TEXT NOT NULL,
                score REAL NOT NULL,
                verdict TEXT NOT NULL,
                label TEXT,
                schema_text TEXT,
                result_json TEXT,
                self_rating INTEGER,
                notes TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_created "
            "ON sessions(created_at DESC)")


def create_session(profile, score, verdict, label, schema_text, result):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (created_at, profile, score, verdict, label,"
            " schema_text, result_json) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), profile, score,
             verdict, label or "", schema_text or "",
             json.dumps(result)))
        return cur.lastrowid


def list_sessions(limit=50):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, profile, score, verdict, label,"
            " self_rating, notes FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_session(sid):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?",
                           (sid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["result"] = json.loads(d.pop("result_json") or "{}")
        except (ValueError, TypeError):
            d["result"] = {}
        return d


def update_session(sid, self_rating=None, notes=None):
    fields = []
    values = []
    if self_rating is not None:
        fields.append("self_rating = ?")
        values.append(int(self_rating))
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    if not fields:
        return get_session(sid)
    values.append(sid)
    with connect() as conn:
        conn.execute("UPDATE sessions SET " + ", ".join(fields) +
                     " WHERE id = ?", values)
    return get_session(sid)


def delete_session(sid):
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
