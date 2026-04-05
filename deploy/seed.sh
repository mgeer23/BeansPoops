#!/usr/bin/env bash
#
# Seed the database with existing Google Sheets data.
# Run after setup.sh has completed.
#
set -euo pipefail

APP_URL="${1:-http://localhost:5000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEED_FILE="$SCRIPT_DIR/../seed_data.csv"

if [ ! -f "$SEED_FILE" ]; then
    # Try the deployed location
    SEED_FILE="/opt/beans-potty/seed_data.csv"
fi

if [ ! -f "$SEED_FILE" ]; then
    echo "Error: seed_data.csv not found"
    exit 1
fi

# Check if data already exists
echo "Checking for existing data..."
EXISTING=$(curl -s "$APP_URL/api/events?limit=1")
if echo "$EXISTING" | python3 -c "import sys,json; data=json.load(sys.stdin); exit(0 if len(data)==0 else 1)" 2>/dev/null; then
    echo "Database is empty. Importing seed data..."
    RESULT=$(curl -s -X POST "$APP_URL/api/events/import-csv" \
        -H "Content-Type: text/csv" \
        --data-binary "@$SEED_FILE")
    echo "Result: $RESULT"
else
    echo "Database already has data. Skipping seed to prevent duplicates."
    echo "To force re-seed, delete existing events first."
fi
