#!/usr/bin/env bash
# One-time interactive Proton Mail Bridge CLI login.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v protonmail-bridge >/dev/null 2>&1; then
  echo "protonmail-bridge is not installed."
  echo "Run: sudo /home/bill/SmartInbox/scripts/install-proton-bridge.sh"
  exit 1
fi

if ! command -v pass >/dev/null 2>&1; then
  echo "pass is not installed. Run the install script first."
  exit 1
fi

KEY_ID="$(gpg --list-secret-keys --keyid-format LONG 2>/dev/null | awk '/^sec/ {print $2}' | cut -d/ -f2 | head -1)"
if [[ -z "${KEY_ID}" ]]; then
  echo "No GPG key found. Run: sudo ${SCRIPT_DIR:-/home/bill/SmartInbox/scripts}/install-proton-bridge.sh"
  exit 1
fi
FPR="$(gpg --list-keys --keyid-format LONG "${KEY_ID}" 2>/dev/null | awk '/^pub/ {getline; print $1; exit}')"
if [[ -n "${FPR}" ]] && ! gpg --list-keys --keyid-format LONG "${KEY_ID}" 2>/dev/null | grep -q '\[E\]'; then
  echo "Adding encryption subkey to GPG key (required for pass)..."
  gpg --batch --passphrase '' --quick-add-key "${FPR}" rsa4096 encr never
fi
if [[ ! -f "${HOME}/.password-store/.gpg-id" ]]; then
  pass init "${KEY_ID}"
fi
if ! echo test | gpg --encrypt --armor -r "${KEY_ID}" >/dev/null 2>&1; then
  echo "GPG key still cannot encrypt. Check: gpg --list-keys --keyid-format LONG"
  exit 1
fi

cat <<'EOF'

Starting Proton Mail Bridge CLI.

Inside the Bridge shell:
  login          sign in with your Proton email + account password (2FA if enabled)
  info           show IMAP host/port/username/password for SmartInbox
  help           list commands
  exit           quit CLI (Bridge stops unless systemd service is running)

Use the IMAP password from "info" in SmartInbox Settings → Proton Mail.
That password is NOT your Proton account password.

EOF

exec protonmail-bridge -c