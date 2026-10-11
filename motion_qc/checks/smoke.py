"""pop_smoke 的公开版：运行时烟测。

为什么必须跑在浏览器里
--------------------
静态扫描会给出「一切正常」的假象：一个 64/64 全绿的必备项检查、
一个全部打勾的批量渲帧任务，都会把**时间轴根本没注册**的组件记成成功——
因为它照样产出了一个文件。真例：一个组件批量渲显示「✓ 5169 B」，
实际时间轴没起来，产出的是一张近乎静止的空片。两个脚本都判它合格，肉眼才发现。

所以判据必须落在运行时：
  1. 有没有 JS 异常（注意：**必须抓 iframe 内部的异常**，
     组件是独立文档，报在父页面上听不见——原始工具就漏在这里）
  2. window.__timelines.main 在不在
  3. duration() 是不是有限且合理（太短 = 动效没铺开，太长 = 观众要等）
  4. 时间轴上若干采样点，主体是不是全不可见
  5. 画面是不是几乎静止
"""
from __future__ import annotations

from pathlib import Path

from ..browser import BrowserError
from ..config import Config
from .base import ERROR, FAIL, PASS, CheckResult, Finding
from .runtime import probe_cached

CHECK_ID = "smoke"
TITLE = "运行时烟测"


def run(target: Path, cfg: Config) -> CheckResult:
    res = CheckResult(CHECK_ID, TITLE)
    try:
        data = probe_cached(Path(target), cfg)
    except BrowserError as exc:
        res.status = ERROR
        res.info["reason"] = str(exc)
        res.add(Finding("SMOKE_BROWSER", "浏览器没跑起来：%s" % exc, ERROR))
        return res

    if data.get("probeError"):
        res.status = ERROR
        res.add(Finding("SMOKE_PROBE", "探针自身抛错：%s" % data["probeError"], ERROR))
        return res

    res.info.update(
        {
            "canvas": data.get("canvas"),
            "stageSelector": data.get("stageSelector"),
            "duration": data.get("duration"),
            "visiblePerSample": data.get("visible") or [],
            "distinctFrames": data.get("frames"),
            "notes": data.get("notes") or [],
        }
    )

    errs = data.get("errors") or []
    seen = set()
    for e in errs:
        code, message, severity = classify(e)
        key = (code, message)
        if key in seen:
            continue
        seen.add(key)
        res.add(Finding(code, message, severity))

    for f in console_findings(data):
        # 解析错误归 jscheck 那一项管（它能给更准的位置）；这里只报，
        # 让只跑 --only smoke 的人也能看见「这个文件连解析都没过去」。
        res.add(f)

    visible = data.get("visible") or []
    dead = [i for i, n in enumerate(visible) if not n]
    res.info["emptySamples"] = dead
    if visible and dead:
        res.add(
            Finding(
                "SMOKE_EMPTY_FRAME",
                "%d/%d 个采样点上整个画面是空的（采样点 %s）" % (len(dead), len(visible), dead),
                FAIL,
            )
        )
    elif not visible:
        res.add(Finding("SMOKE_NO_SAMPLES", "没能取到任何采样点", FAIL))

    res.info["consoleErrors"] = data.get("console") or []
    if res.status == PASS:
        res.info["headline"] = "时长 %ss · %d 帧形态 · 采样 %s" % (
            data.get("duration"), data.get("frames"), visible,
        )
    return res


def console_findings(data) -> list:
    """浏览器控制台里**属于被检文件**的报错。

    页内 error 监听只能抓到监听挂上之后才发生的异常。组件脚本在自己执行的
    第一行就抛的（以及解析期就死掉的），页内一条都听不见——stderr 的控制台
    日志补的就是这一段，这是加载期异常唯一可靠的来源。
    """
    out = []
    for rec in data.get("console") or []:
        msg = rec.get("message", "")
        out.append(
            Finding(
                "SMOKE_SYNTAX_ERROR" if "SyntaxError" in msg else "SMOKE_LOAD_ERROR",
                "%s（第 %s 行）" % (msg, rec.get("line")),
                FAIL,
                detail=rec,
            )
        )
    return out


#: 探针里探针抛出来的人话 → 稳定的断言码。
#: 码要稳定：自测靠它定位，CI 靠它做门禁，不能跟着文案改。
_ERROR_CODES = (
    ("没有 window.__timelines.main", "SMOKE_NO_TIMELINE", FAIL,
     "没有 window.__timelines.main——时间轴没注册。这条既可能是漏了注册，"
     "也可能是脚本解析期就死了（看 jscheck 那项）。"),
    ("运行时异常", "SMOKE_RUNTIME_ERROR", FAIL, None),
    ("时长过短", "SMOKE_TOO_SHORT", FAIL, None),
    ("时长过长", "SMOKE_TOO_LONG", FAIL, None),
    ("画面几乎是静止的", "SMOKE_STATIC", FAIL, None),
    ("无法取得 iframe 内容", "SMOKE_NO_DOCUMENT", FAIL, None),
    ("根容器尺寸为", "SMOKE_NO_CANVAS", FAIL, None),
    ("duration() 返回了非有限值", "SMOKE_BAD_DURATION", FAIL, None),
    ("拨时间轴失败", "SMOKE_SEEK_FAILED", FAIL, None),
)


def classify(message: str):
    """把探针消息映射成 (码, 人话, 严重度)。"""
    for prefix, code, severity, canned in _ERROR_CODES:
        if message.startswith(prefix):
            return code, canned or message, severity
    return "SMOKE_ERROR", message, FAIL
