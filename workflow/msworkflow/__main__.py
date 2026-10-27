"""python -m msworkflow [steps...] [--force] [--config FILE]: see process.py."""

import argparse
import os
import sys
from pathlib import Path

# --config must be known before Metashape is imported: helpers reads [hardware]
# (license, CPU threads) from the settings file at import time.
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--config", type=Path)
_args, _ = _pre.parse_known_args()
if _args.config:
    os.environ["MSWORKFLOW_CONFIG"] = str(_args.config)

from .helpers import log, log_error
from .process import main

try:
    main()
except KeyboardInterrupt:  # kill -INT <pid>: Metashape cancels the running step
    log("interrupted (SIGINT): the running step was cancelled, not saved")
    sys.exit(130)
except Exception:
    log_error("msworkflow")
    sys.exit(1)
