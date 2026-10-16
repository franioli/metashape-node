# Metashape in Docker (cluster node or standalone)

Runs Metashape in a container, either as a processing node for a Metashape
cluster or as a standalone instance with a shell. The version is configurable,
so it can match the geoGOCE cluster's version (2.1.3) even though Windows domain
policy blocks updating the host (DESKTOP-PE8O2II).

## Quick start
1. Copy `.env.example` to `.env` and edit it (see below).
2. Put your license in `license/license.lic`.
3. `docker compose up -d --build`

## Settings (`.env`)
| Variable | Meaning |
|---|---|
| `METASHAPE_VERSION` | `latest` (newest on agisoft.com) or e.g. `2.1.3` |
| `MODE` | `node` (join cluster) or `standalone` (shell) |
| `METASHAPE_HOST` | Server IP, node mode only |
| `WORKSPACE_PATH` | Host folder mounted at `/workspace` (default `/mnt/p`) |
| `GPU_COUNT` | GPUs given to the container: `all` or a number |
| `GPU_MASK` | Which GPUs Metashape uses (bitmask: 1 = first, 2 = second, 3 = both). Empty = all visible |

Changing the version needs `--build`; `latest` is cached, so use
`docker compose build --no-cache` to pick up a newer release. Images are tagged
`metashape:<version>`.

## Modes
- **node** — connects to the server at `METASHAPE_HOST` and appears in the
  Network Monitor. Logs: `docker compose logs -f`.
- **standalone** — container idles; open a shell with
  `docker compose exec metashape bash`, then e.g. `metashape -r script.py`
  (`metashape` is on the PATH and runs headless). Or one-off:
  `docker compose run --rm metashape bash`.

Switch with `MODE=standalone docker compose up -d` (PowerShell:
`$env:MODE="standalone"; docker compose up -d`) or edit `.env`.
Only one mode runs at a time (`docker compose down` first when switching).

## GPUs
All GPUs are passed through by default (requires the NVIDIA container toolkit /
Docker Desktop with WSL2 GPU support). To limit them: `GPU_COUNT=1` hands over
only one GPU; `GPU_MASK` picks which of the visible GPUs Metashape uses.
Check inside the container with `nvidia-smi` and `clinfo -l` (Metashape uses
OpenCL). With no GPU detected, the container falls back to CPU only — but on a
machine without NVIDIA runtime, delete the `deploy:` block in
`docker-compose.yml` or compose will refuse to start.

## Tools in the container
Metashape (with its bundled Python), `nvidia-smi` (injected by the toolkit),
`clinfo`, `bash`, `nano`, `vim`, `less`, `htop`, `tmux`, `curl`, `wget`, `rsync`,
`zip`/`unzip`, `ping`, `nc`, `dig`, `lspci`.

## Files
- `dockerfile` — Debian 12-slim, runtime deps, OpenCL, tools, Metashape download.
- `entrypoint.sh` — starts node or standalone mode and works out the GPU mask.
- `docker-compose.yml` — build, GPU, workspace and license wiring.
- `.env.example` — settings template.
- `license/license.lic` — network license file (points to the license server).

## Manage
    docker compose logs -f          # live logs
    docker compose stop/start
    docker compose down             # stop + remove container
    docker compose up -d --build    # rebuild after Dockerfile/version changes

Requires `P:` already mapped in the same Windows session running Docker Desktop
(for the default `WORKSPACE_PATH`).

## Node options (see `entrypoint.sh`)
- `-platform offscreen` — required Qt flag for headless operation (no X server).
- `--host` = server IP (`METASHAPE_HOST`)
- `--root` = shared project drive, must match the server's --root exactly
  (`/workspace/`)
- `--gpu_mask` = from `GPU_MASK`, or all detected GPUs — verify via Preferences > GPU
- `--priority 5` (`NODE_PRIORITY`) = matches native Windows nodes used elsewhere

## Licensing
Network floating license, pointing to the university license server.
- `license/license.lic` mounted read-only into the container at the path
  Metashape expects: `/var/tmp/agisoft/licensing/licenses/license.lic`
- Points to: LMW-2E.POLIMI.IT:2020
- To update/replace, overwrite `license/license.lic` and `docker compose restart`

## Runtime dependencies (why the Dockerfile installs what it does)
Metashape's Linux tarball is mostly self-contained but needs these external
libs, discovered via `ldd`/`QT_DEBUG_PLUGINS=1` debugging:
- `libxcb-xinerama0`, `libcurl4`, `libgl1`, `libglu1-mesa`, `libgomp1`,
  `libglib2.0-0` — main `metashape` binary deps
- `libfontconfig1`, `libfreetype6` — required by the Qt `offscreen` platform
  plugin (needed even headless, since Metashape always initializes Qt)
- `libffi6` — not in current Debian repos, installed manually from an
  Ubuntu mirror `.deb` (legacy package, needed by Metashape's bundled Python)

## Debugging tips (if it breaks again after an update)
    docker compose run --rm metashape bash
    ldd ./metashape | grep "not found"                       # main binary
    ldd ./plugins/platforms/libqoffscreen.so | grep "not found"  # Qt plugin
    QT_DEBUG_PLUGINS=1 ./metashape -platform offscreen ...    # verbose Qt errors

## Resource limits
Container RAM/CPU is capped by the WSL2 VM allocation in
`%UserProfile%\.wslconfig` (Docker Desktop runs inside WSL2), not by Docker
itself. Current allocation:
    [wsl2]
    memory=24GB
    processors=10
    swap=8GB
    localhostForwarding=true

To change: edit `.wslconfig`, then:
    docker compose down
    wsl --shutdown
    docker compose up -d

Optional per-container cap (reserves headroom for other workloads sharing
the same WSL2 VM) — add to docker-compose.yml under `metashape`:
    mem_limit: 20g

## Notes
- No explicit "registration" step — node auto-connects to server on start,
  confirmed via `registration accepted` in logs, then appears in Network Monitor.
- If Agisoft releases a version-compatible update to the geoGOCE cluster
  later, this container becomes unnecessary — native local node can be used.
## Without Docker (Debian/Ubuntu host)
`sudo ./scripts/setup.sh [latest|2.1.3]` runs the same steps as the dockerfile
directly on the machine: installs the libraries, downloads Metashape to
`/opt/metashape`, links `metashape` into the PATH and installs
`license/license.lic`. Then start a node with:

    MODE=node METASHAPE_HOST=<server-ip> METASHAPE_ROOT=/path/to/shared/ ./entrypoint.sh

The NVIDIA driver must already be installed on the host.
