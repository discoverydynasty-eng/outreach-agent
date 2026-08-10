#!/bin/bash
#
# daily_run.sh — the full daily job, run by launchd (or cron) each morning.
#
# Two stages:
#   1. Claude Code (headless) sources, enriches, writes emails into outbox.json.
#   2. send_guard.py re-checks every rule in real code and sends what passes.
# Claude never sends. The guard is the only thing that can.
#
# SETUP (one time):
#   1. chmod +x daily_run.sh
#   2. Edit PROJECT_DIR below to the real absolute path of this folder.
#   3. Create a venv and install deps (see SETUP.md step 3).
#   4. Put your Places API key in a `.env` file:  PLACES_API_KEY=your_key_here
#   5. Run ./daily_run.sh by hand once before scheduling.

set -euo pipefail

# --- EDIT THIS to the absolute path of this project folder ---
PROJECT_DIR="/Users/YOURNAME/hvac-outreach"
# -------------------------------------------------------------

cd "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/logs"

# Load Places API key etc. from .env
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# Prevent overlapping runs (e.g. if the Mac woke and launchd stacked jobs)
LOCK="$PROJECT_DIR/.run.lock"
if [ -f "$LOCK" ]; then
  echo "$(date) previous run still active or unclean. Aborting." >> "$PROJECT_DIR/logs/cron.log"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOGFILE="$PROJECT_DIR/logs/run_$STAMP.log"
PYTHON="$PROJECT_DIR/venv/bin/python"   # from the venv created in SETUP.md

echo "$(date) === starting daily outreach run ===" | tee -a "$PROJECT_DIR/logs/cron.log"

# --- Stage 1: Claude Code fills the outbox (does NOT send) ---
echo "$(date) stage 1: Claude Code drafting..." | tee -a "$PROJECT_DIR/logs/cron.log"
claude -p "Read CLAUDE.md in this directory and run today's outreach job through Step 6, following every guardrail. Queue finished emails into outbox.json. Do NOT attempt to send anything yourself." \
  --dangerously-skip-permissions \
  >> "$LOGFILE" 2>&1

# --- Stage 2: the enforced guard validates and sends ---
echo "$(date) stage 2: send guard validating + sending..." | tee -a "$PROJECT_DIR/logs/cron.log"
"$PYTHON" "$PROJECT_DIR/send_guard.py" >> "$LOGFILE" 2>&1

echo "$(date) === finished. summary: ===" | tee -a "$PROJECT_DIR/logs/cron.log"
cat "$PROJECT_DIR/run_summary.txt" 2>/dev/null | tee -a "$PROJECT_DIR/logs/cron.log" || true
tail -n 6 "$LOGFILE" | tee -a "$PROJECT_DIR/logs/cron.log" || true
