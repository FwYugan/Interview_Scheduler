#!/usr/bin/env bash
# Install the runtime packages required by the interview scheduling website.
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo: sudo bash scripts/bootstrap_ubuntu.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  debian-archive-keyring \
  debian-keyring \
  gnupg \
  git \
  python3-pip \
  python3-venv \
  sqlite3

install -d -m 0755 /usr/share/keyrings
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  -o /etc/apt/sources.list.d/caddy-stable.list
chmod a+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod a+r /etc/apt/sources.list.d/caddy-stable.list

apt-get update
apt-get install -y caddy
systemctl enable --now caddy

echo
echo "Installed versions:"
python3 --version
git --version
sqlite3 --version
caddy version
systemctl --no-pager --full status caddy
