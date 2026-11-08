#!/usr/bin/env python3
"""Poll stop files. Print one line: DONE or FAILED. Silent otherwise."""

from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="Wait until stop files exist and are non-empty.")
    ap.add_argument("--timeout", type=int, default=10800, help="seconds (default 10800)")
    ap.add_argument("--poll", type=int, default=30, help="seconds (default 30)")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    deadline = time.time() + args.timeout
    missing = list(args.paths)
    while time.time() < deadline:
        missing = [
            p for p in args.paths if not (os.path.isfile(p) and os.path.getsize(p) > 0)
        ]
        if not missing:
            print("DONE: " + " ".join(args.paths))
            return 0
        time.sleep(max(1, args.poll))
    print("FAILED: missing " + " ".join(missing))
    return 1


if __name__ == "__main__":
    sys.exit(main())
