"""Run the deployment-level backup and retention maintenance tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.logger import configure_logging  # noqa: E402
from web.database import assert_schema_current  # noqa: E402
from web.services.maintenance import MaintenanceError, run_maintenance  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute audited School AI maintenance tasks.",
    )
    parser.add_argument(
        "--job",
        choices=("backup", "retention", "all"),
        default="all",
        help="Maintenance task to execute (default: all).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging()
    assert_schema_current()
    try:
        run_maintenance(args.job)
    except MaintenanceError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
