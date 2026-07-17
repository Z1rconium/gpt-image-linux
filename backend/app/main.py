from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from .core.logging_config import setup_logging

setup_logging()

from .api.contract_app import app, proxy  # noqa: F401
