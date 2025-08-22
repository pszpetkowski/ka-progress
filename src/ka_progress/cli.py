"""Minimal CLI for ka-progress.

Options:
- --slug / -s: Course slug(s) to traverse (repeatable).
- --headless / --no-headless: Toggle Playwright headless mode (default: headless).
"""

from __future__ import annotations

import argparse
import asyncio

from .app import main as app_main


def main() -> None:
    """Console script entrypoint that parses args and runs the app."""
    parser = argparse.ArgumentParser(
        prog="ka-progress", description="Track Khan Academy course progress"
    )
    parser.add_argument(
        "-s",
        "--slug",
        dest="slugs",
        action="append",
        help=(
            "Course slug like /math/cc-seventh-grade-math. "
            "Repeat the flag to add multiple."
        ),
    )
    # Boolean pair for headless mode
    parser.add_argument(
        "--headless", dest="headless", action="store_true", default=True,
        help="Run browser in headless mode (default)",
    )
    parser.add_argument(
        "--no-headless", dest="headless", action="store_false",
        help="Run browser with UI (non-headless)",
    )

    args = parser.parse_args()
    asyncio.run(app_main(slugs=args.slugs, headless=args.headless))
