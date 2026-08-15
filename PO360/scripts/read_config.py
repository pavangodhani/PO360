"""Tiny helper so setup/monitor/reset scripts can read paths from config.json
without duplicating JSON-parsing logic in both .bat and .sh files.

Usage: python scripts/read_config.py <dotted.path> [default]
Example: python scripts/read_config.py logging.dir logs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: read_config.py <dotted.path> [default]", file=sys.stderr)
        return 2

    dotted_path = sys.argv[1]
    default = sys.argv[2] if len(sys.argv) > 2 else ""

    config_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    if not config_path.exists():
        print(default)
        return 0

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(default)
        return 0

    node = data
    for key in dotted_path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            print(default)
            return 0

    print(node)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
