FROM debian:12-slim

# Runtime libraries needed by Metashape (see README), plus OpenCL (used for GPU
# processing) and a few handy tools for the interactive/standalone shell.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb-xinerama0 libcurl4 libgl1 libglu1-mesa libgomp1 libglib2.0-0 \
    libfontconfig1 libfreetype6 \
    ocl-icd-libopencl1 clinfo \
    fontconfig fonts-dejavu-core fonts-liberation \
    wget ca-certificates \
    bash-completion nano vim-tiny less htop tmux procps \
    curl rsync unzip zip iputils-ping netcat-openbsd dnsutils pciutils \
    && wget https://mirrors.kernel.org/ubuntu/pool/main/libf/libffi/libffi6_3.2.1-8_amd64.deb \
    && apt install -y ./libffi6_3.2.1-8_amd64.deb \
    && rm libffi6_3.2.1-8_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Register the NVIDIA OpenCL driver (the library itself is injected at runtime
# by the NVIDIA container toolkit) and expose all GPUs with compute support.
RUN mkdir -p /etc/OpenCL/vendors && echo "libnvidia-opencl.so.1" > /etc/OpenCL/vendors/nvidia.icd
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Metashape version to install: "latest" or a specific version like "2.1.3".
# Set via METASHAPE_VERSION in .env or the shell (see docker-compose.yml).
ARG METASHAPE_VERSION=latest

# Download the Linux tarball from Agisoft and unpack it to /opt/metashape.
# "latest" is resolved by scraping the highest version from Agisoft's download page.
RUN set -e; \
    V="${METASHAPE_VERSION}"; \
    if [ "$V" = "latest" ]; then \
        FILE=$(wget -qO- https://www.agisoft.com/downloads/installer/ \
            | grep -oE 'metashape-pro_[0-9_]+_amd64\.tar\.gz' | sort -uV | tail -n1); \
        [ -n "$FILE" ] || { echo "Could not detect latest Metashape version"; exit 1; }; \
    else \
        FILE="metashape-pro_$(echo "$V" | tr . _)_amd64.tar.gz"; \
    fi; \
    echo "Installing $FILE"; \
    wget -O /tmp/metashape.tar.gz "http://download.agisoft.com/$FILE"; \
    mkdir -p /opt/metashape; \
    tar -xzf /tmp/metashape.tar.gz -C /opt/metashape --strip-components=1; \
    rm /tmp/metashape.tar.gz

# Python environment with the Metashape Python module (wheel from Agisoft, same
# version as the application) in a venv at /opt/venv. The wheel is found on the
# download page; "latest" picks the highest version listed.
RUN set -e; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip; \
    rm -rf /var/lib/apt/lists/*; \
    V="${METASHAPE_VERSION}"; \
    PAGE=$(wget -qO- https://www.agisoft.com/downloads/installer/); \
    if [ "$V" = "latest" ]; then PATTERN='[0-9.]+'; else PATTERN=$(echo "$V" | sed 's/\./\\./g'); fi; \
    WHEEL=$(echo "$PAGE" | grep -oiE "metashape-${PATTERN}-[A-Za-z0-9._-]+-linux_x86_64\.whl" \
        | awk '{print tolower($0), $0}' | sort -uV | tail -n1 | cut -d" " -f2); \
    [ -n "$WHEEL" ] || { echo "Could not find Metashape Python wheel for $V"; exit 1; }; \
    echo "Installing $WHEEL"; \
    wget -O "/tmp/$WHEEL" "https://download.agisoft.com/$WHEEL"; \
    python3 -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir "/tmp/$WHEEL"; \
    rm "/tmp/$WHEEL"

# Named user for the UID/GID the container runs as (`user:` in docker-compose.yml, from
# PUID/PGID in .env), so the shell prompt and `whoami` work instead of "I have no name!".
# Its home is /tmp/home, the writable HOME set in docker-compose.yml.
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g "$PGID" metashape \
    && useradd -u "$PUID" -g "$PGID" -M -d /tmp/home -s /bin/bash metashape

WORKDIR /opt/metashape

# `metashape` on PATH; Qt runs headless since there is no display in the container.
# The venv is on PATH, so `python` can `import Metashape`; /opt/workflow is too, so
# its scripts (run) can be called by name, and PYTHONPATH makes the msworkflow package
# importable from any script or notebook.
ENV PATH="/opt/workflow:/opt/venv/bin:/opt/metashape:${PATH}" \
    PYTHONPATH=/opt/workflow \
    QT_QPA_PLATFORM=offscreen

# Workflow scripts, baked in for production. In dev, docker-compose.dev.yml
# bind-mounts ./workflow over this folder so edits are live.
COPY workflow /opt/workflow

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
