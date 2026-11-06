#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db-schema-forge - local-first database schema design reviewer.

Flask API + static UI. No API keys, no network calls, no LLM.
"""

import os
import json
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, Response

import engine
import db
import samples

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"),
            static_url_path="/static")

db.init_db()


@app.after_request
def add_headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "db-schema-forge",
                    "time": datetime.now().isoformat(timespec="seconds")})


@app.route("/api/profiles")
def list_profiles():
    out = []
    for pid, p in engine.PROFILES.items():
        out.append({"id": pid, "label": p["label"], "blurb": p["blurb"],
                    "weights": p["weights"], "soften": p["soften"]})
    return jsonify(out)


@app.route("/api/samples")
def list_samples():
    return jsonify(samples.list_samples())


@app.route("/api/sample")
def get_sample():
    sid = request.args.get("id", "")
    sample = samples.get_sample(sid)
    if not sample:
        return jsonify({"error": "sample not found"}), 404
    return jsonify(sample)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    payload = request.get_json(silent=True) or {}
    schema_text = (payload.get("schema") or payload.get("ddl") or "").strip()
    profile = payload.get("profile") or engine.DEFAULT_PROFILE
    if profile not in engine.PROFILES:
        profile = engine.DEFAULT_PROFILE
    if not schema_text:
        return jsonify({"error": "paste a schema or pick a sample first"}), 400

    result = engine.analyze(schema_text, profile)

    saved_id = None
    if payload.get("save"):
        label = (payload.get("label") or "").strip()
        saved_id = db.create_session(profile, result["score"],
                                     result["verdict"]["band"], label,
                                     schema_text, result)
    result["session_id"] = saved_id
    return jsonify(result)


@app.route("/api/sessions")
def list_sessions():
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    return jsonify(db.list_sessions(min(max(limit, 1), 200)))


@app.route("/api/sessions/<int:sid>")
def get_session(sid):
    s = db.get_session(sid)
    if not s:
        return jsonify({"error": "session not found"}), 404
    return jsonify(s)


@app.route("/api/sessions/<int:sid>", methods=["PATCH"])
def patch_session(sid):
    payload = request.get_json(silent=True) or {}
    rating = payload.get("self_rating")
    if rating is not None:
        try:
            rating = max(1, min(5, int(rating)))
        except (TypeError, ValueError):
            rating = None
    notes = payload.get("notes")
    s = db.update_session(sid, self_rating=rating, notes=notes)
    if not s:
        return jsonify({"error": "session not found"}), 404
    return jsonify(s)


@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
def remove_session(sid):
    db.delete_session(sid)
    return jsonify({"status": "deleted", "id": sid})


@app.route("/api/export/<int:sid>")
def export_session(sid):
    fmt = (request.args.get("fmt") or "md").lower()
    s = db.get_session(sid)
    if not s:
        return jsonify({"error": "session not found"}), 404
    result = s.get("result") or {}
    if fmt == "json":
        body = json.dumps(result, indent=2)
        mime = "application/json"
        ext = "json"
    elif fmt == "ddl":
        body = (result.get("scaffold") or {}).get("ddl", "")
        mime = "text/plain"
        ext = "sql"
    else:
        body = engine.to_markdown(result)
        mime = "text/markdown"
        ext = "md"
    return Response(body, mimetype=mime, headers={
        "Content-Disposition": 'attachment; filename="schema-review-%s.%s"'
                               % (sid, ext)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="127.0.0.1", port=port, debug=False)
