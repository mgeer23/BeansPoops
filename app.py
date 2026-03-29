import csv
import io
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "data/beans.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL CHECK(event_type IN ('pee', 'poo')),
            timestamp  TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_timestamp
        ON events(timestamp DESC)
    """)
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/events", methods=["POST"])
def create_event():
    data = request.get_json()
    if not data or data.get("event_type") not in ("pee", "poo"):
        return jsonify({"error": "event_type must be 'pee' or 'poo'"}), 400

    timestamp = data.get("timestamp")
    if not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    db = get_db()
    cursor = db.execute(
        "INSERT INTO events (event_type, timestamp) VALUES (?, ?)",
        (data["event_type"], timestamp),
    )
    db.commit()

    row = db.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/events", methods=["GET"])
def list_events():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    db = get_db()
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    return "", 204


@app.route("/api/events/export")
def export_events():
    db = get_db()
    rows = db.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "event_type", "timestamp", "created_at"])
    for row in rows:
        writer.writerow([row["id"], row["event_type"], row["timestamp"], row["created_at"]])

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return (
        output.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": f"attachment; filename=beans_events_{today}.csv",
        },
    )


@app.route("/api/events/export.json")
def export_events_json():
    db = get_db()
    rows = db.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events/import", methods=["POST"])
def import_events():
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array of events"}), 400

    db = get_db()
    count = 0
    for event in data:
        if event.get("event_type") not in ("pee", "poo") or not event.get("timestamp"):
            continue
        db.execute(
            "INSERT INTO events (event_type, timestamp, created_at) VALUES (?, ?, ?)",
            (event["event_type"], event["timestamp"], event.get("created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))),
        )
        count += 1
    db.commit()
    return jsonify({"imported": count}), 201


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
