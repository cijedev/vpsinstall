#!/usr/bin/env bash
# Cije VPS Installer. Installs Python 3 if needed, then launches the program.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

need_pkg() {
  command -v "$1" >/dev/null 2>&1
}

if ! need_pkg python3; then
  if [[ "${EUID}" -ne 0 ]]; then
    echo "python3 is required. Re-run as root so it can be installed." >&2
    exit 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3
fi

exec python3 -u -m debian_vps_setup "$@"
