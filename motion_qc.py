#!/usr/bin/env python3
"""motion-qc 的单文件入口。

装了就用 ``motion-qc``；不想装就 ``python motion_qc.py check ...``。
两种走的是同一份代码，不存在「命令行能跑、模块跑不通」的差异。

    python motion_qc.py check out/*.html --json report.json
    python motion_qc.py selftest
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion_qc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
