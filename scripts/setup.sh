#!/bin/bash
# Installs Metashape directly on a Debian/Ubuntu host (no Docker).
# Mirrors the steps in the dockerfile.
#
# Usage: sudo ./scripts/setup.sh [VERSION]
#   VERSION  "latest" (default) or a specific version, e.g. 2.1.3
#   INSTALL_DIR env var overrides the install location (default /opt/metashape)
#   LICENSE_FILE env var: path to a license.lic to install (default ./license/license.lic if present)
set -euo pipefail

VERSION="${1:-${METASHAPE_VERSION:-latest}}"
INSTALL_DIR="${INSTALL_DIR:-/opt/metashape}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LICENSE_FILE="${LICENSE_FILE:-$REPO_DIR/license/license.lic}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)"; exit 1; }

# 1. Runtime libraries + OpenCL + tools (same as the dockerfile)
apt-get update
apt-get install -y --no-install-recommends \
    libxcb-xinerama0 libcurl4 libgl1 libglu1-mesa libgomp1 libglib2.0-0 \
    libfontconfig1 libfreetype6 \
    ocl-icd-libopencl1 clinfo \
    wget ca-certificates

# 2. libffi6 is not in current repos; install it from an Ubuntu mirror
if ! dpkg -s libffi6 >/dev/null 2>&1; then
    wget -O /tmp/libffi6.deb https://mirrors.kernel.org/ubuntu/pool/main/libf/libffi/libffi6_3.2.1-8_amd64.deb
    apt-get install -y /tmp/libffi6.deb
    rm /tmp/libffi6.deb
fi

# 3. Register the NVIDIA OpenCL driver (needs the NVIDIA driver installed on the host)
mkdir -p /etc/OpenCL/vendors
echo "libnvidia-opencl.so.1" > /etc/OpenCL/vendors/nvidia.icd

# 4. Download and unpack Metashape
if [ "$VERSION" = "latest" ]; then
    FILE=$(wget -qO- https://www.agisoft.com/downloads/installer/ \
        | grep -oE 'metashape-pro_[0-9_]+_amd64\.tar\.gz' | sort -uV | tail -n1)
    [ -n "$FILE" ] || { echo "Could not detect latest Metashape version"; exit 1; }
else
    FILE="metashape-pro_$(echo "$VERSION" | tr . _)_amd64.tar.gz"
fi
echo "Installing $FILE to $INSTALL_DIR"
wget -O /tmp/metashape.tar.gz "http://download.agisoft.com/$FILE"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -xzf /tmp/metashape.tar.gz -C "$INSTALL_DIR" --strip-components=1
rm /tmp/metashape.tar.gz

# 5. Put `metashape` on the PATH
ln -sf "$INSTALL_DIR/metashape" /usr/local/bin/metashape

# 6. License (network license file), if available
if [ -f "$LICENSE_FILE" ]; then
    mkdir -p /var/tmp/agisoft/licensing/licenses
    cp "$LICENSE_FILE" /var/tmp/agisoft/licensing/licenses/license.lic
    echo "License installed from $LICENSE_FILE"
else
    echo "No license file found at $LICENSE_FILE - copy it to /var/tmp/agisoft/licensing/licenses/license.lic"
fi

echo
echo "Done. Start a node with the same entrypoint used by Docker:"
echo "  MODE=node METASHAPE_HOST=<server-ip> METASHAPE_ROOT=<shared-root>/ $REPO_DIR/entrypoint.sh"
echo "Or run Metashape directly: metashape -platform offscreen -r script.py"
