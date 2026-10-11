#!/usr/bin/env python3
"""pop_overflow.py 的公开版入口（落定期出框扫描）。

和原来的同名工具用法一致，只是路径全参数化了：

    python tools/pop_overflow.py a.html b.html
    python tools/pop_overflow.py a.html --tolerance 0.5 --settle-from 0.76

统一入口是 `motion-qc check --only overflow`，这个脚本是为了让从旧工具链
迁过来的人不用改调用习惯。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion_qc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["check", "--only", "overflow"] + sys.argv[1:]))
