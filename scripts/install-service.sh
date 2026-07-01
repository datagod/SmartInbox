#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/smartinbox.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/smartinbox.service"

if [[ ! -x "$ROOT/.venv/bin/smartinbox" ]]; then
  echo "Missing $ROOT/.venv/bin/smartinbox — create the venv and pip install first." >&2
  exit 1
fi

mkdir -p "$(dirname "$UNIT_DST")"
sed "s|/home/bill/SmartInbox|$ROOT|g" "$UNIT_SRC" >"$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable smartinbox.service
systemctl --user restart smartinbox.service
systemctl --user --no-pager status smartinbox.service

echo ""
echo "SmartInbox user service installed."
echo "  status:  systemctl --user status smartinbox"
echo "  logs:    journalctl --user -u smartinbox -f"
echo "  stop:    systemctl --user stop smartinbox"
echo "  disable: systemctl --user disable smartinbox"