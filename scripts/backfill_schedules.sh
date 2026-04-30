#!/bin/bash
# Backfill all historical PBS schedules sequentially.
# Waits for each ingest to complete before starting the next.
#
# Usage:
#   INGEST_TOKEN=<token> API_BASE=https://your-service.up.railway.app ./scripts/backfill_schedules.sh

set -e

API_BASE="${API_BASE:-https://pbsdata-api-production.up.railway.app}"
INGEST_TOKEN="${INGEST_TOKEN:?INGEST_TOKEN env var is required}"
API_KEY="${API_KEY:?API_KEY env var is required}"
POLL_INTERVAL=30

# Schedules available from the PBS API (oldest first)
declare -a SCHEDULES=(
    "2025-05 2025-05-01"
    "2025-06 2025-06-01"
    "2025-07 2025-07-01"
    "2025-08 2025-08-01"
    "2025-09 2025-09-01"
    "2025-10 2025-10-01"
    "2025-11 2025-11-01"
    "2025-12 2025-12-01"
    "2026-01 2026-01-01"
    "2026-02 2026-02-01"
    "2026-03 2026-03-01"
)

wait_for_complete() {
    local month="$1"
    echo "  Waiting for $month to complete..."
    while true; do
        status=$(check_status "$month")

        echo "  [$month] status: $status"
        if [[ "$status" == "complete" ]]; then
            echo "  [$month] Done."
            return 0
        elif [[ "$status" == "failed" ]]; then
            echo "  [$month] FAILED. Check Railway logs."
            return 1
        fi
        sleep "$POLL_INTERVAL"
    done
}

check_status() {
    local month="$1"
    curl -sf "$API_BASE/v1/schedules" \
        -H "X-API-Key: $API_KEY" 2>/dev/null | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('data', []):
    if s['month'] == '$month':
        print(s['ingest_status'])
        sys.exit(0)
print('not_found')
" 2>/dev/null || echo "error"
}

echo "Starting backfill of ${#SCHEDULES[@]} schedules..."
echo ""

for entry in "${SCHEDULES[@]}"; do
    month=$(echo "$entry" | cut -d' ' -f1)
    schedule_date=$(echo "$entry" | cut -d' ' -f2)

    current_status=$(check_status "$month")
    if [[ "$current_status" == "complete" ]]; then
        echo "==> Skipping $month (already complete)"
        continue
    fi

    echo "==> Triggering ingest for $month (schedule_date=$schedule_date)"

    response=$(curl -sf -X POST "$API_BASE/internal/ingest" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $INGEST_TOKEN" \
        -d "{\"month\": \"$month\", \"schedule_date\": \"$schedule_date\"}" || echo "ERROR")

    if [[ "$response" == "ERROR" ]]; then
        echo "  Failed to trigger ingest for $month — skipping."
        continue
    fi

    echo "  Triggered: $response"
    wait_for_complete "$month" || true
    echo ""
done

echo "Backfill complete."
