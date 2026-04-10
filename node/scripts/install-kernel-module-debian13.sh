#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root"
    exit 1
fi

echo "[amneziawg] Installing dependencies for Debian 13..."
apt-get update
apt-get install --yes \
    gnupg2 \
    apt-transport-https \
    dkms \
    build-essential \
    "linux-headers-$(uname -r)"

echo "[amneziawg] Importing Amnezia PPA signing key..."
gpg --keyserver keyserver.ubuntu.com --recv-keys 75c9dd72c799870e310542e24166f2c257290828
gpg --export 75c9dd72c799870e310542e24166f2c257290828 | tee /usr/share/keyrings/amnezia.gpg > /dev/null

echo "[amneziawg] Configuring deb822 apt source..."
cat > /etc/apt/sources.list.d/amnezia.sources <<'EOF'
Types: deb deb-src
URIs: https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu
Suites: focal
Components: main
Signed-By: /usr/share/keyrings/amnezia.gpg
EOF

echo "[amneziawg] Installing kernel module and tools..."
apt-get update
apt-get install --yes amneziawg amneziawg-tools

echo "[amneziawg] Building/loading module for kernel $(uname -r)..."
dkms autoinstall -k "$(uname -r)"
depmod -a
modprobe amneziawg

echo "[amneziawg] Verifying kernel support..."
modinfo amneziawg >/dev/null
ip link add awg_probe type amneziawg
ip link del awg_probe

echo "[amneziawg] Kernel module installed and loaded successfully."
