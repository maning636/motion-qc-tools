#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 motion-qc 生成的 JSON 报告里 summary.ok 为 true。

为什么抽成独立脚本（而不是在 workflow 里 `python -c "..."`）
----------------------------------------------------------
1. **Windows 控制台编码**：`python -c` 里 `print('好样本全过：', ...)` 在
   windows-latest 上按 cp1252 输出中文 → `UnicodeEncodeError: 'charmap' codec
   can't encode characters`。注意 assert 其实已经通过了，是 print 炸的 ——
   一条「工具没跑起来」的假红。
   这里自己 `reconfigure(encoding='utf-8', errors='replace')` 兜住。
2. **shell 引号地狱**：同一段多行代码要同时在 bash / pwsh 下能跑，
   内联进 yaml 迟早出问题。独立文件没有这个问题。

用法:
  python tools/assert_summary_ok.py good.json
退出码: 0 = ok 为真；1 = ok 为假或报告不合法
"""
import json
import sys
from pathlib import Path

# 兜住 Windows 控制台编码（必须在任何 print 之前）
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: assert_summary_ok.py <report.json>')
        return 2
    p = Path(sys.argv[1])
    if not p.is_file():
        print('report not found: %s' % p)
        return 1
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print('report is not valid JSON: %s' % e)
        return 1

    summary = d.get('summary') or {}
    ok = summary.get('ok')
    # 只打印 ASCII，避免任何编码问题
    print('summary = %s' % json.dumps(summary, ensure_ascii=True, sort_keys=True))
    if ok is not True:
        print('FAIL: summary.ok is not true')
        return 1
    print('PASS: summary.ok is true')
    return 0


if __name__ == '__main__':
    sys.exit(main())
