#!/usr/bin/env python
"""Seed or remove the synthetic demonstration factory.

    ./.venv/bin/python seed_demo.py           # create
    ./.venv/bin/python seed_demo.py --remove  # delete

Everything created is labelled synthetic. It demonstrates the geometry pipeline;
it is not evidence about any real camera.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import SessionLocal, init_db          # noqa: E402
from app.demo_data import remove_demo_data, seed_demo_data   # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="delete the demo data instead")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        result = remove_demo_data(db) if args.remove else seed_demo_data(db)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
