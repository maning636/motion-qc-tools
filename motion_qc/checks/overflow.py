"""pop_overflow 的公开版：渲染落定期出框扫描。

为什么必须自动化
----------------
目检漏过一次：一个片段容器写着 `top:116px`，却被 append 进了一个**已经在
top:116px** 的轨道里 —— 双重偏移，实际落到 y=232，整排片段被画布下边缘
切掉一半。DOM 读数完全正常（元素在、尺寸对、只是位置错），
在缩略联络表里也只是「底部有几个方块」，不放大根本看不出是被切的。

而肉眼是最贵的验收方式：64 条 × 7 个时间点 = 448 个读数，人看会漏，脚本不会。
脚本只负责报出可疑坐标，最后仍然要人看渲帧确认是不是真问题。

报两类：
  A. 元素矩形超出画布（出框）
  B. 元素宽或高为 0（没画出来）

口径说明
--------
* 只扫**落定期**（默认时长后 24%）。全程扫会把「从画布外飞进来」
  「出场飞出画布」这类设计效果全报一遍——上一轮 23/64 报警，绝大多数是这么来的，
  报了等于没报。
* 容差默认 0.5px，**必须小于 1px**。原先留 2px，结果一个正好越界 2px 的 bug
  被判成合规——扫描器自己造了一个假阴性，差点让这个 bug 带着「全族零出框」的
  结论上线。容差放宽到 1px 以上请用 --tolerance，并知道你在换判据。
"""
from __future__ import annotations

from pathlib import Path

from ..browser import BrowserError
from ..config import Config
from .base import ERROR, PASS, WARN, CheckResult, Finding
from .runtime import probe_cached

CHECK_ID = "overflow"
TITLE = "落定期出框"

SIDE_CN = {"left": "左", "top": "上", "right": "右", "bottom": "下"}


def run(target: Path, cfg: Config) -> CheckResult:
    res = CheckResult(CHECK_ID, TITLE)
    try:
        data = probe_cached(Path(target), cfg)
    except BrowserError as exc:
        res.status = ERROR
        res.add(Finding("OF_BROWSER", "浏览器没跑起来：%s" % exc, ERROR))
        return res

    if data.get("probeError"):
        res.status = ERROR
        res.add(Finding("OF_PROBE", "探针自身抛错：%s" % data["probeError"], ERROR))
        return res

    canvas = data.get("canvas")
    res.info.update(
        {
            "canvas": canvas,
            "stageSelector": data.get("stageSelector"),
            "tolerancePx": cfg.tolerance,
            "settleWindow": "%.0f%%–100%% 时长，共 %d 点" % (cfg.settle_from * 100, cfg.settle_samples),
        }
    )

    # 探针没走到出框那一步（没有时间轴 / 画布尺寸为 0），这时「没报出框」
    # 是「没查」不是「通过」。照实标 skip，别让它顶一个绿勾。
    if not canvas or not (canvas.get("w", 0) > 1 and canvas.get("h", 0) > 1):
        res.status = ERROR
        res.add(Finding("OF_NO_CANVAS", "没有可判定的画布，出框这一项没跑成（看 smoke 项）", ERROR))
        return res
    if data.get("duration") is None:
        res.status = ERROR
        res.add(Finding("OF_NO_TIMELINE", "时间轴不可用，出框这一项没跑成（看 smoke 项）", ERROR))
        return res

    for rec in data.get("overflow") or []:
        cls = rec.get("cls") or ""
        who = ("." + cls) if cls else (rec.get("tag") or "?")
        if rec.get("text"):
            who += "「%s」" % rec["text"]
        sides = "".join(SIDE_CN.get(s.split("+")[0], s.split("+")[0]) for s in rec.get("sides") or [])
        res.add(
            Finding(
                "OF_OUT_OF_FRAME",
                "%s 出%s %dpx（画布 %d×%d，t=%ss）"
                % (who, sides, _worst(rec), canvas["w"], canvas["h"], rec.get("t")),
                detail=rec,
            )
        )

    for rec in data.get("zeroSize") or []:
        cls = rec.get("cls") or rec.get("tag") or "?"
        res.add(
            Finding(
                "OF_ZERO_SIZE",
                "%s「%s」宽高为 0，等于没画出来（t=%ss）"
                % ("." + cls if not cls.startswith(".") else cls, rec.get("text", ""), rec.get("t")),
                detail=rec,
            )
        )

    if res.status == PASS:
        res.info["headline"] = "落定期 %d 个采样点全部在框内" % cfg.settle_samples
    _font_note(res, data)
    return res


def _font_note(res: CheckResult, data: dict) -> None:
    """字体没加载上 → 文字宽度是兜底字体的宽度，这类结论要打折。

    只在**确实有带文字的元素被判出框**时才升成警告。字体取不到在本地开发里
    是常态（组件写的是站点绝对路径 /fonts/...），98 个文件里 97 个会中招——
    无条件报警等于没报警，看报告的人会直接把这一行划过去。
    关键程度取决于它影响了哪条结论，所以按结论触发。

    无论触不触发，`fontsNotLoaded` 都进 info：机器能读到，人需要时才用。
    """
    bad = data.get("fonts") or []
    if not bad:
        return
    res.info["fontsNotLoaded"] = bad
    text_hits = [
        f for f in res.findings
        if f.code == "OF_OUT_OF_FRAME" and (f.detail or {}).get("text")
    ]
    if not text_hits:
        return
    res.add(
        Finding(
            "OF_FONT_NOT_LOADED",
            "有 %d 个 @font-face 没加载上（%s），且有 %d 条出框结论涉及文字——"
            "文字宽度是按兜底字体的量，同一段字能差好几个像素，这几条请复核。"
            "常见原因是组件引用了站点绝对路径字体（file:// 下取不到）。"
            % (len(bad), "、".join(sorted({str(f.get("family")) for f in bad})),
               len(text_hits)),
            WARN,
            detail={"fonts": bad, "affectedFindings": [f.message for f in text_hits]},
        )
    )


def _worst(rec: dict) -> int:
    vals = [int(s.split("+")[1]) for s in rec.get("sides") or [] if "+" in s]
    return max(vals) if vals else 0
