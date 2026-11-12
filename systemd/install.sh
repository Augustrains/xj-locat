#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/sport_bot.py" --check-config
"$PROJECT_DIR/.venv/bin/python" "$SCRIPT_DIR/render_units.py"
if systemctl --user is-active --quiet sport-bot.timer 2>/dev/null; then
    echo "用户级 sport-bot.timer 已运行，请先停用它以避免重复打卡。" >&2
    exit 1
fi
sudo install -m 0644 "$PROJECT_DIR/.generated/systemd/sport-bot.service" /etc/systemd/system/sport-bot.service
sudo install -m 0644 "$PROJECT_DIR/.generated/systemd/sport-bot.timer" /etc/systemd/system/sport-bot.timer

sudo systemctl daemon-reload
sudo systemctl enable --now sport-bot.timer

systemctl list-timers sport-bot.timer
