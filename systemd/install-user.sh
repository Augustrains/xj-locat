#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--prepare-only" ) ]]; then
    echo "用法：bash systemd/install-user.sh [--prepare-only]" >&2
    exit 2
fi
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/sport_bot.py" --check-config
"$PROJECT_DIR/.venv/bin/python" "$SCRIPT_DIR/render_units.py"
if [[ "${1:-}" == "--prepare-only" ]]; then
    exit 0
fi

# 在复制服务文件前确认用户总线可访问。
systemctl --user show-environment >/dev/null
if systemctl is-active --quiet sport-bot.timer 2>/dev/null; then
    echo "系统级 sport-bot.timer 已运行，请先停用它以避免重复打卡。" >&2
    exit 1
fi
mkdir -p "$UNIT_DIR"
install -m 0644 "$PROJECT_DIR/.generated/systemd/user/sport-bot.service" "$UNIT_DIR/sport-bot.service"
install -m 0644 "$PROJECT_DIR/.generated/systemd/user/sport-bot.timer" "$UNIT_DIR/sport-bot.timer"

systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR || true
systemctl --user daemon-reload
systemctl --user enable --now sport-bot.timer

systemctl --user list-timers sport-bot.timer
