#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

mkdir -p "$LOG_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

{
  echo "[$TIMESTAMP] Iniciando EL_Games_Sheet.py"
  "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/EL_Games_Sheet.py"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ejecucion completada"
} >> "$LOG_DIR/el_games_sheet.log" 2>> "$LOG_DIR/el_games_sheet.error.log"
