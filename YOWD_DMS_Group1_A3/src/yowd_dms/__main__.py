"""Command-line entry point: ``python -m yowd_dms [options]``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .app.dms import DeliveryManagementSystem
from .demo_data import seed_demo
from .domain.common import Clock, DomainError, parse_datetime
from .ui.app import ConsoleApp


def main(argv: list[str] | None = None) -> None:
    """Parse options, bootstrap the system and start the console UI."""
    parser = argparse.ArgumentParser(
        prog="yowd_dms",
        description="YOWD Delivery Management System (SWE30003 A3, G1)")
    parser.add_argument("--data", default="data", type=Path,
                        help="folder for the JSON data files (default: data)")
    parser.add_argument("--seed-demo", action="store_true",
                        help="add demo staff, a client and availability "
                             "if the data folder is empty")
    parser.add_argument("--demo-time", metavar="'YYYY-MM-DD HH:MM'",
                        help="fix the clock for a repeatable demonstration")
    args = parser.parse_args(argv)

    clock = Clock()
    if args.demo_time:
        fixed = parse_datetime(args.demo_time)
        clock = Clock(lambda: fixed)
    try:
        dms = DeliveryManagementSystem.start(args.data, clock)
    except DomainError as exc:
        # Bootstrap readiness checks failed (A2 section 6.3): do not run on
        # inconsistent records; tell the operator what to correct.
        raise SystemExit(f"Startup stopped: {exc}\nCorrect the data files "
                         f"in '{args.data}' and start again.") from exc
    if args.seed_demo:
        seed_demo(dms)
    ConsoleApp(dms, demo_mode=bool(args.demo_time)).run()


if __name__ == "__main__":
    main()
