#!/usr/bin/env bash
# Install Proton Mail Bridge on Debian for headless CLI use with SmartInbox.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEB="${SCRIPT_DIR}/protonmail-bridge_3.22.0-1_amd64.deb"
GPG_KEY_ID="${PROTON_BRIDGE_GPG_KEY_ID:-970601774CB75284}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo:"
  echo "  sudo ${SCRIPT_DIR}/install-proton-bridge.sh"
  exit 1
fi

if [[ ! -f "${DEB}" ]]; then
  echo "Missing ${DEB}"
  echo "Downloading..."
  wget -q "https://proton.me/download/bridge/protonmail-bridge_3.22.0-1_amd64.deb" -O "${DEB}"
fi

echo "Installing dependencies..."
apt-get update -qq
apt-get install -y \
  pass \
  libsecret-1-0 \
  libfido2-1 \
  libxcb-cursor0 \
  libegl1 \
  fonts-dejavu \
  libglib2.0-0 \
  libpulse0

echo "Installing Proton Mail Bridge..."
apt-get install -y "${DEB}"

TARGET_USER="${SUDO_USER:-bill}"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"

echo "Setting up GPG + pass for ${TARGET_USER}..."
sudo -u "${TARGET_USER}" bash -c '
set -euo pipefail
KEY_ID="'"${GPG_KEY_ID}"'"
if ! gpg --list-secret-keys --keyid-format LONG "${KEY_ID}" >/dev/null 2>&1; then
  gpg --batch --passphrase "" --quick-generate-key \
    "Proton Bridge (SmartInbox) <proton-bridge@local>" rsa4096 default never
  KEY_ID="$(gpg --list-secret-keys --keyid-format LONG | awk "/^sec/ {print \$2}" | cut -d/ -f2 | head -1)"
fi
FPR="$(gpg --list-keys --keyid-format LONG "${KEY_ID}" | awk "/^pub/ {getline; print \$1; exit}")"
if ! gpg --list-keys --keyid-format LONG "${KEY_ID}" | grep -q "\[E\]"; then
  gpg --batch --passphrase "" --quick-add-key "${FPR}" rsa4096 encr never
fi
if [[ ! -f "${HOME}/.password-store/.gpg-id" ]]; then
  pass init "${KEY_ID}"
fi
'

UNIT_DIR="${TARGET_HOME}/.config/systemd/user"
mkdir -p "${UNIT_DIR}"
cat > "${UNIT_DIR}/protonmail-bridge.service" <<'EOF'
[Unit]
Description=Proton Mail Bridge (noninteractive)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/protonmail-bridge --noninteractive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
chown -R "${TARGET_USER}:${TARGET_USER}" "${TARGET_HOME}/.config/systemd"

echo
echo "Proton Mail Bridge installed."
echo
echo "Next steps (as ${TARGET_USER}, not root):"
echo "  1. Log in once in CLI mode:"
echo "       ${SCRIPT_DIR}/proton-bridge-login.sh"
echo "     Commands inside Bridge CLI: login, then info"
echo "  2. Copy the Bridge IMAP password into SmartInbox Settings → Proton Mail"
echo "  3. Enable the background service:"
echo "       systemctl --user enable --now protonmail-bridge.service"
echo "       loginctl enable-linger ${TARGET_USER}   # keep running after logout"