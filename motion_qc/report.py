"""报告输出：终端人话 + 机器可读 JSON。

两份输出喂的是同一份数据。终端那份给「想知道哪儿坏了的人」，
JSON 那份给 CI 和 Agent——后者不该去 parse 中文。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import __version__
from .checks import DESCRIPTIONS
from .checks.base import ERROR, FAIL, PASS, SKIP, WARN

MARK = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP", ERROR: "ERR "}
_ICON = {PASS: "·", FAIL: "×", WARN: "!", SKIP: "-", ERROR: "?"}


def _w(stream, text=""):
    stream.write(text + "\n")


def render_text(files, cfg=None, only=None, verbose=False, stream=None) -> None:
    out = stream or sys.stdout
    total = len(files)
    _w(out, "motion-qc %s · 检查 %d 个文件%s"
       % (__version__, total, ("（只跑 %s）" % ",".join(only)) if only else ""))

    for fr in files:
        _w(out)
        _w(out, "%s  %s" % (fr.path, MARK[fr.status]))
        for ch in fr.checks:
            _w(out, "  [%s] %s" % (MARK[ch.status], ch.title))
            for f in ch.findings:
                head = "      %s %s" % (_ICON[f.severity], f.message)
                _w(out, head)
                if verbose and f.detail:
                    _w(out, "        %s" % json.dumps(f.detail, ensure_ascii=False))
            if not ch.findings and (verbose or ch.info.get("headline")):
                _w(out, "      · %s" % (ch.info.get("headline") or "通过"))

    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0, ERROR: 0}
    for fr in files:
        counts[fr.status] = counts.get(fr.status, 0) + 1
    _w(out)
    _w(out, "─" * 52)
    _w(out, "合计 %d 个文件：%d 通过 / %d 有问题 / %d 警告 / %d 跳过 / %d 工具出错"
       % (total, counts[PASS], counts[FAIL], counts[WARN], counts[SKIP], counts[ERROR]))
    if cfg is not None:
        _w(out, "判定口径：容差 %.2fpx · 落定期后 %.0f%% · 时长 %.1f–%.1fs · 静止阈值 %d 帧"
           % (cfg.tolerance, cfg.settle_from * 100, cfg.min_duration, cfg.max_duration,
              cfg.min_frames + 1))
    bad = [fr.path for fr in files if fr.status in (FAIL, ERROR)]
    if bad:
        _w(out, "不合格：" + "  ".join(bad))


def to_dict(files, cfg=None, elapsed: float | None = None) -> dict:
    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0, ERROR: 0}
    for fr in files:
        counts[fr.status] = counts.get(fr.status, 0) + 1
    payload = {
        "tool": "motion-qc",
        "version": __version__,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": DESCRIPTIONS,
        "summary": {
            "files": len(files),
            "pass": counts[PASS],
            "fail": counts[FAIL],
            "warn": counts[WARN],
            "skip": counts[SKIP],
            "error": counts[ERROR],
            "ok": counts[FAIL] == 0 and counts[ERROR] == 0,
        },
        "criteria": None,
        "results": [fr.to_dict() for fr in files],
    }
    if cfg is not None:
        payload["criteria"] = {
            "tolerancePx": cfg.tolerance,
            "settleWindowFrom": cfg.settle_from,
            "settleSamples": cfg.settle_samples,
            "minDurationS": cfg.min_duration,
            "maxDurationS": cfg.max_duration,
            "minDistinctFrames": cfg.min_frames + 1,
            "viewport": ("%dx%d" % cfg.viewport) if cfg.viewport else "auto（按文件声明）",
            "stageSelectors": list(cfg.stage_selectors),
        }
    if elapsed is not None:
        payload["elapsedSeconds"] = round(elapsed, 2)
    return payload


def write_json(payload: dict, dest: str | Path) -> None:
    p = Path(dest)
    if p.parent and str(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
