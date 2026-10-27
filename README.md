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

## Python API
A Python venv (`/opt/venv`, first on the PATH) has the `Metashape` module
installed from Agisoft's wheel, matching `METASHAPE_VERSION`. Inside the
container: `python my_script.py` (with `import Metashape`). The module uses the
same license file as the application. Verify with
`python -c "import Metashape; print(Metashape.version)"`.
`metashape -r script.py` still works and uses its own bundled Python.
On a host install, `scripts/setup.sh` creates the venv at `/opt/venv`
(override with `VENV_DIR`); activate it with `source /opt/venv/bin/activate`.

## Run a processing workflow (standalone mode, in tmux)
For long runs on a remote machine. A project folder holds a `process.py`
(plus its `config.py`, `helpers.py` and TOML settings file) and lives under
`WORKSPACE_PATH`, i.e. under `/workspace` in the container. tmux keeps the run
going when the SSH connection closes: start a session, open a shell in the
container inside it, run the pipeline and detach.

**1. Start (or reattach to) a tmux session on the host**

    tmux new -s ms          # new session named "ms"
    tmux attach -t ms       # reattach to it later (tmux ls lists the sessions)

**2. In the session, open a shell in the container**

    docker exec -it -w /workspace/<project> metashape-standalone bash

`-w` is the project folder inside the container (the one with `process.py`). The
container is named `metashape-<MODE>`, so `metashape-standalone` in this mode.
`run` is on the PATH there: it runs `process.py` from the current
folder, appends all output to `run.log` and prints the exit code at the end
(0 = all done, 1 = a step failed, 130 = interrupted). From another folder, set
`PROCESS_DIR=/workspace/<project>`.

**3. Run what you need**

    run                        # the steps in [run] steps of the settings file
    run mesh texture export    # only these steps
    run depth cloud filter mesh texture dsm ortho export   # whole dense part
    run photos align           # from scratch: add photos and align, then stop
                                          # to place the GCPs in the GUI ...
    run optimize depth cloud mesh texture dsm ortho export   # ... and continue
    run mesh --force           # rebuild the mesh even if it exists
    run --config other.toml    # another settings file
    run --help

- Steps always run in pipeline order, whatever order they are typed in:
  `photos align optimize depth cloud filter mesh texture dsm ortho export`.
- A step whose product already exists is skipped (`--force` rebuilds it). A step
  whose input is missing is skipped too, and the run ends with exit code 1.
- Ctrl-c stops the run gracefully: the running step is cancelled and not saved,
  earlier steps stay saved.

**4. Detach and come back**
- Detach: press **Ctrl-b**, release, then press **d**. The run keeps going.
- Come back: `tmux attach -t ms`.
- When finished: `exit` the container shell, then `exit` again to close the tmux
  session (or `tmux kill-session -t ms` from outside).

To try one step at a time, or to stop after a step, use the same code from a
Python script or notebook (`import config`, `import process as p`, then
`p.start(cfg)` and `p.run_step("mesh", doc, cfg)`; see the docstring of `process.py`).

`run` comes from `workflow/` (mounted live at `/opt/workflow` and also
copied into the image, which puts it on the PATH). The container runs as a user
named `metashape` with the `PUID`/`PGID` of `.env`, so the shell prompt shows a
name; changing those two needs a rebuild.

## Files
- `dockerfile` — Debian 12-slim, runtime deps, OpenCL, tools, Metashape download, Python venv.
- `entrypoint.sh` — starts node or standalone mode and works out the GPU mask.
- `docker-compose.yml` — build, GPU, workspace and license wiring.
- `.env.example` — settings template.
- `workflow/` — scripts mounted at `/opt/workflow`; `run` is the launcher for `process.py`.
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
