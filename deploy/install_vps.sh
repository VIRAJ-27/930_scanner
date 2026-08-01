#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install_vps.sh"
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/scanner930"
CONFIG_DIR="/etc/scanner930"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates git python3 python3-pip python3-venv rsync tzdata

if ! id scanner930 >/dev/null 2>&1; then
  useradd --system --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin scanner930
fi

install -d -m 0750 -o scanner930 -g scanner930 "${INSTALL_DIR}"
install -d -m 0700 -o root -g scanner930 "${CONFIG_DIR}"
rsync -a \
  --exclude='.git' --exclude='.venv' --exclude='.env' \
  --exclude='LiveData' --exclude='LiveReports' --exclude='logs' \
  "${SOURCE_DIR}/" "${INSTALL_DIR}/"
install -d -m 0750 -o scanner930 -g scanner930 \
  "${INSTALL_DIR}/LiveData" "${INSTALL_DIR}/LiveReports" "${INSTALL_DIR}/logs"

python3 -m venv --upgrade-deps "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
chown -R scanner930:scanner930 "${INSTALL_DIR}"

if [[ ! -f "${CONFIG_DIR}/scanner930.env" ]]; then
  install -m 0640 -o root -g scanner930 \
    "${INSTALL_DIR}/deploy/scanner930.env.example" \
    "${CONFIG_DIR}/scanner930.env"
fi

install -m 0644 "${INSTALL_DIR}/deploy/scanner930-option-paper.service" \
  /etc/systemd/system/scanner930-option-paper.service
install -m 0644 "${INSTALL_DIR}/deploy/scanner930-option-paper.timer" \
  /etc/systemd/system/scanner930-option-paper.timer

systemctl daemon-reload
systemctl enable scanner930-option-paper.timer

echo "Installed. Next edit ${CONFIG_DIR}/scanner930.env, run the doctor,"
echo "then start the timer as described in deploy/README_VPS.md."
