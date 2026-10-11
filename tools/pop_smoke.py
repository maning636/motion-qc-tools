#!/usr/bin/env python3
"""pop_smoke.py 的公开版入口（运行时烟测）。

    python tools/pop_smoke.py a.html b.html

统一入口是 `motion-qc check --only smoke`。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion_qc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["check", "--only", "smoke"] + sys.argv[1:]))
