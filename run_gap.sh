#!/usr/bin/env bash
# NeoGap — start the gap trading strategy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/.neogap.pid"

mkdir -p "$LOG_DIR"

# Activate virtual environment if it exists
if [[ -d "$VENV_DIR" ]]; then
    source "$VENV_DIR/bin/activate"
fi

# Guard: don't start a second instance
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "NeoGap is already running (PID $PID). Exiting."
        exit 1
    fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting NeoGap…"
cd "$SCRIPT_DIR"
nohup python main.py run >> "$LOG_DIR/gap_strategy.log" 2>&1 &
echo $! > "$PID_FILE"
echo "NeoGap started (PID $(cat $PID_FILE)). Logs: $LOG_DIR/gap_strategy.log"
