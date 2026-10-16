FROM debian:12-slim

# Runtime libraries needed by Metashape (see README), plus OpenCL (used for GPU
# processing) and a few handy tools for the interactive/standalone shell.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb-xinerama0 libcurl4 libgl1 libglu1-mesa libgomp1 libglib2.0-0 \
    libfontconfig1 libfreetype6 \
    ocl-icd-libopencl1 clinfo \
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

WORKDIR /opt/metashape

# `metashape` on PATH; Qt runs headless since there is no display in the container.
ENV PATH="/opt/metashape:${PATH}" \
    QT_QPA_PLATFORM=offscreen

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
