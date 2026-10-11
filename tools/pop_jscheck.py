#!/usr/bin/env python3
"""pop_jscheck.py 的公开版入口（内联 JS 语法检查）。

    python tools/pop_jscheck.py a.html

统一入口是 `motion-qc check --only jscheck`。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion_qc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["check", "--only", "jscheck"] + sys.argv[1:]))
