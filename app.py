import csv
import io
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", "data/beans.db")

USE_POSTGRES = DATABASE_URL is not None

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


def get_db():
    if "db" not in g:
        if USE_POSTGRES:
            g.db = psycopg2.connect(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL CHECK(event_type IN ('pee', 'poo')),
                accident   BOOLEAN NOT NULL DEFAULT FALSE,
                timestamp  TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp
            ON events(timestamp DESC)
        """)
        conn.commit()
        cur.close()
        conn.close()
    else:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL CHECK(event_type IN ('pee', 'poo')),
                accident   INTEGER NOT NULL DEFAULT 0,
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


def db_execute(query, params=None):
    """Execute a query, handling placeholder differences between SQLite and Postgres."""
    db = get_db()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params or ())
        return cur
    else:
        return db.execute(query, params or ())


def db_fetchall(query, params=None):
    cur = db_execute(query, params)
    rows = cur.fetchall()
    if USE_POSTGRES:
        cur.close()
        return rows
    return [dict(r) for r in rows]


def db_fetchone(query, params=None):
    cur = db_execute(query, params)
    row = cur.fetchone()
    if USE_POSTGRES:
        cur.close()
        return dict(row) if row else None
    return dict(row) if row else None


def db_commit():
    get_db().commit()


def normalize_event(row):
    """Normalize accident field to boolean for JSON responses."""
    d = dict(row)
    d["accident"] = bool(d.get("accident"))
    return d


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

    accident = bool(data.get("accident", False))
    accident_val = accident if USE_POSTGRES else int(accident)

    if USE_POSTGRES:
        row = db_fetchone(
            "INSERT INTO events (event_type, accident, timestamp) VALUES (?, ?, ?) RETURNING *",
            (data["event_type"], accident_val, timestamp),
        )
    else:
        cur = db_execute(
            "INSERT INTO events (event_type, accident, timestamp) VALUES (?, ?, ?)",
            (data["event_type"], accident_val, timestamp),
        )
        row = db_fetchone("SELECT * FROM events WHERE id = ?", (cur.lastrowid,))

    db_commit()
    return jsonify(normalize_event(row)), 201


@app.route("/api/events", methods=["GET"])
def list_events():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    rows = db_fetchall(
        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return jsonify([normalize_event(r) for r in rows])


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    db_execute("DELETE FROM events WHERE id = ?", (event_id,))
    db_commit()
    return "", 204


@app.route("/api/events/export")
def export_events():
    rows = db_fetchall("SELECT * FROM events ORDER BY timestamp ASC")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "event_type", "accident", "timestamp", "created_at"])
    for row in rows:
        writer.writerow([row["id"], row["event_type"], bool(row["accident"]), row["timestamp"], row["created_at"]])

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
    rows = db_fetchall("SELECT * FROM events ORDER BY timestamp ASC")
    return jsonify([normalize_event(r) for r in rows])


@app.route("/api/events/import", methods=["POST"])
def import_events():
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array of events"}), 400

    count = 0
    for event in data:
        if event.get("event_type") not in ("pee", "poo") or not event.get("timestamp"):
            continue
        created = event.get("created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        accident = bool(event.get("accident", False))
        accident_val = accident if USE_POSTGRES else int(accident)
        db_execute(
            "INSERT INTO events (event_type, accident, timestamp, created_at) VALUES (?, ?, ?, ?)",
            (event["event_type"], accident_val, event["timestamp"], created),
        )
        count += 1
    db_commit()
    return jsonify({"imported": count}), 201


@app.route("/api/events/import-csv", methods=["POST"])
def import_csv():
    """Import from Google Sheets CSV format: Datetime,Pee,Accident"""
    if "file" in request.files:
        text = request.files["file"].read().decode("utf-8")
    else:
        text = request.get_data(as_text=True)

    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        # Parse datetime from DD/MM/YYYY HH:MM format
        dt_str = row.get("Datetime", "").strip()
        if not dt_str:
            continue
        try:
            dt = datetime.strptime(dt_str, "%d/%m/%Y %H:%M")
        except ValueError:
            continue

        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        pee = row.get("Pee", "").strip().upper() == "TRUE"
        event_type = "pee" if pee else "poo"
        accident = row.get("Accident", "").strip().upper() == "TRUE"
        accident_val = accident if USE_POSTGRES else int(accident)

        db_execute(
            "INSERT INTO events (event_type, accident, timestamp, created_at) VALUES (?, ?, ?, ?)",
            (event_type, accident_val, timestamp, timestamp),
        )
        count += 1
    db_commit()
    return jsonify({"imported": count}), 201


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_ENV") != "production")
