"""工具自测：拿仓库自带的已知好/坏样本，验「工具本身没坏」。

为什么必须有
------------
**一条从不触发的断言等于没有断言，而且比没有更坏——它给假安全感。**

容差就是一个会骗你的参数。历史上容差留 2px，结果一个正好越界 2px 的片段容器
被判成合规：扫描器自己造了一个假阴性，差点让这个 bug 带着「全族零出框」的
结论上线。改了容差（2px → 0.5px）却没人验过新的容差。

所以这里从**两侧**钉住判据：

    越界 1.0px   → 必须报     旧容差 2px 在这里会放过，就是那个假阴性
    越界 0.2px   → 必须不报   容差要是塌成 0，满屏浮点噪声全红
    已知好样本    → 必须全绿   误报率高的扫描器等于没有扫描器
    已知坏样本    → 必须报出   每种缺陷都要真的响

外加：几何算式（compose 的锚点/34% 基准宽/运动包络）有纯 Python 的单元断言，
不依赖浏览器，跑得飞快。

跑法：``motion-qc selftest``，退出码 0 = 全部符合预期，1 = 有断言没过。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .checks import compose, jscheck, overflow, smoke
from .config import Config

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class _Result:
    def __init__(self):
        self.passed = []
        self.failed = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        line = "%s %-46s %s" % ("✓" if ok else "✗", name, detail)
        (self.passed if ok else self.failed).append(name)
        _emit(line)
        return ok


def _emit(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _codes(check_result) -> set:
    return {f.code for f in check_result.findings}


def run_selftest(keep: bool = False) -> int:
    cfg = Config()
    r = _Result()

    _emit("motion-qc 自测 · 样本目录 %s" % SAMPLES)
    chrome = cfg.chrome_bin()
    node = cfg.node_bin()
    _emit("  Chrome/Edge : %s" % (chrome or "没找到（smoke/overflow 全部跳过）"))
    _emit("  node        : %s" % (node or "没找到（jscheck 相关断言记 skipped）"))
    _emit("")

    # ── 1. 几何算式：不依赖浏览器，永远要跑 ──────────────────────
    _emit("几何算式（compose 判定口径）")
    _geometry_assertions(r)

    # ── 2. compose 样本：纯 JSON，不需要浏览器 ───────────────────
    _emit("")
    _emit("compose/2 样本")
    good = compose.run(SAMPLES / "good" / "compose-ok.json", cfg)
    r.check("好编排全绿", good.status == "pass",
            "发现：%s" % (sorted(_codes(good)) or "无"))
    bad = compose.run(SAMPLES / "bad" / "compose-out-of-frame.json", cfg)
    codes = _codes(bad)
    for want, why in (
        ("QC_COMPOSE_OUT_OF_FRAME", "运动包络出框"),
        ("QC_COMPOSE_CONTRACT", "契约不符"),
        ("QC_COMPOSE_OVERLAP", "时段重叠"),
        ("QC_COMPOSE_BARE", "裸底时长"),
    ):
        r.check("坏编排必须报出：%s" % why, want in codes, "报出 %s" % sorted(codes))

    if not chrome:
        _emit("")
        _emit("!! 没有浏览器，smoke / overflow 的样本断言全部跳过——这不是通过")
        r.check("浏览器可用", False, "装 Chrome/Edge 后重跑，或设 MOTION_QC_CHROME")
        _return(r)
        return 1

    # ── 3. 已知好样本：必须全绿 ─────────────────────────────────
    _emit("")
    _emit("已知好样本（必须全绿）")
    for name in ("minimal-title.html", "wide-composition.html", "subpixel-inside.html",
                 "font-fallback.html"):
        p = SAMPLES / "good" / name
        s, o = smoke.run(p, cfg), overflow.run(p, cfg)
        # 只按严重度判：WARN 是「结论要打折」，不是「判错了」。
        # 字体取不到那一类样本就是 WARN，它仍然是好样本。
        bad = [f for res in (s, o) for f in res.findings if f.severity in ("fail", "error")]
        detail = []
        if bad:
            detail.extend(f.message for f in bad)
        if node:
            j = jscheck.run(p, cfg)
            if j.status not in ("pass", "skip"):
                detail.extend(f.message for f in j.findings)
        warns = sorted({f.code for res in (s, o) for f in res.findings if f.severity == "warn"})
        r.check("好样本不误报：%s" % name, not detail,
                " | ".join(detail) or "时长 %ss · 采样 %s%s"
                % (s.info.get("duration"), s.info.get("visiblePerSample"),
                   (" · 警告 %s" % warns) if warns else ""))

    # 字体取不到要能被机器读到，但**不该**在没牵扯到出框结论时报警告：
    # 本地开发里 98 个组件有 97 个字体取不到，无条件报警等于没报警。
    fo = overflow.run(SAMPLES / "good" / "font-fallback.html", cfg)
    r.check("字体取不到但没牵扯出框 → 不报警告，字体情况仍进 info",
            fo.status == "pass" and "OF_FONT_NOT_LOADED" not in _codes(fo)
            and fo.info.get("fontsNotLoaded"),
            "状态 %s · 发现 %s · info 字体 %d"
            % (fo.status, sorted(_codes(fo)) or "无", len(fo.info.get("fontsNotLoaded") or [])))

    # ── 4. 已知坏样本：每种缺陷都必须真的响 ─────────────────────
    _emit("")
    _emit("已知坏样本（必须报出）")
    expectations = (
        ("overflow-1px.html", overflow, "OF_OUT_OF_FRAME", "越界 1px 必须报出框"),
        ("overflow-offcanvas.html", overflow, "OF_OUT_OF_FRAME", "整块出画必须报出框"),
        ("zero-size.html", overflow, "OF_ZERO_SIZE", "宽高为 0 必须报"),
        ("js-syntax-error.html", jscheck, "JS_SYNTAX", "解析失败必须报语法错"),
        ("js-syntax-error.html", smoke, "SMOKE_NO_TIMELINE", "解析失败必须表现为时间轴缺失"),
        ("runtime-error.html", smoke, "SMOKE_LOAD_ERROR", "加载期异常必须报出"),
        ("no-timeline.html", smoke, "SMOKE_NO_TIMELINE", "没注册时间轴必须报"),
        ("static-frame.html", smoke, "SMOKE_STATIC", "静止片必须报出"),
        ("too-short.html", smoke, "SMOKE_TOO_SHORT", "时长过短必须报出"),
        ("font-caveat.html", overflow, "OF_OUT_OF_FRAME", "文字出框必须报"),
        ("font-caveat.html", overflow, "OF_FONT_NOT_LOADED", "文字出框 + 字体取不到必须附带提示"),
    )
    cache: dict = {}
    for name, mod, code, label in expectations:
        p = SAMPLES / "bad" / name
        key = (name, mod.__name__)
        if key not in cache:
            cache[key] = mod.run(p, cfg)
        res = cache[key]
        if not node and mod is jscheck:
            r.check(label + "（skipped：没有 node）", res.status == "skip", res.status)
            continue
        r.check(label, code in _codes(res), "报出 %s" % sorted(_codes(res)))

    # ── 5. 亚像素：容差塌成 0 的那一侧 ──────────────────────────
    _emit("")
    _emit("容差两侧（容差会骗人，所以从两边钉）")
    p1 = SAMPLES / "bad" / "overflow-1px.html"
    p2 = SAMPLES / "good" / "subpixel-inside.html"
    o1 = _codes(overflow.run(p1, cfg))
    o2 = _codes(overflow.run(p2, cfg))
    r.check("容差 0.5px：越界 1px 报，越界 0.2px 不报",
            "OF_OUT_OF_FRAME" in o1 and "OF_OUT_OF_FRAME" not in o2,
            "1px→%s  0.2px→%s" % ("报" if "OF_OUT_OF_FRAME" in o1 else "静",
                                  "报" if "OF_OUT_OF_FRAME" in o2 else "静"))
    loose = Config()
    loose.tolerance = 2.0
    o_loose = _codes(overflow.run(p1, loose))
    r.check("容差 2px：1px 越界被放过（这就是那个假阴性）",
            "OF_OUT_OF_FRAME" not in o_loose,
            "2px 容差下报出 %s" % (sorted(o_loose) or "静"))

    _return(r)
    return 0 if not r.failed else 1


def _geometry_assertions(r: _Result) -> None:
    from . import geometry as G

    box = G.layer_frame_box({"position": "cc", "scale": 100, "x": 0, "y": 0}, 1920, 1080)
    r.check("中心锚点 scale100 外框 = 34% 画布宽",
            abs(box["w"] - 1920 * 0.34) < 1e-6 and abs(box["h"] - 1080 * 0.34) < 1e-6,
            "%.2f×%.2f" % (box["w"], box["h"]))
    r.check("中心锚点在正中心",
            abs((box["x0"] + box["x1"]) / 2 - 960) < 1e-6 and abs((box["y0"] + box["y1"]) / 2 - 540) < 1e-6,
            "%.1f,%.1f" % ((box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2))
    r.check("中心锚点 scale100 不出框", not G.out_of_frame_sides(box, 1920, 1080))

    box2 = G.layer_frame_box({"position": "cc", "scale": 200, "x": 0, "y": 0}, 1920, 1080)
    r.check("scale200 外框 = 68% 画布宽",
            abs(box2["w"] - 1920 * 0.68) < 1e-6, "%.2f" % box2["w"])

    moved = G.layer_frame_box(
        {"position": "cc", "scale": 100, "x": 40, "y": 0,
         "start": 0, "end": 2, "motion": {"type": "offset", "dx": 0.9, "dy": 0, "secs": 2}},
        1920, 1080)
    r.check("x=40% + 运动 0.9 画布宽 → 甩出右边界",
            "右" in G.out_of_frame_sides(moved, 1920, 1080),
            "外框右沿 %.0f" % moved["x1"])

    path = G.layer_frame_box(
        {"position": "cc", "scale": 100, "x": 0, "y": 0, "start": 0, "end": 2,
         "motion": {"type": "path", "points": [[-0.4, 0], [0, 0], [0.4, 0]], "secs": 2}},
        1920, 1080)
    r.check("路径运动包络含中途甩出的两端",
            "左" in G.out_of_frame_sides(path, 1920, 1080) and
            "右" in G.out_of_frame_sides(path, 1920, 1080),
            "外框 %.0f – %.0f" % (path["x0"], path["x1"]))

    r.check("时段相邻不算重叠",
            G.overlapping_pairs([{"start": 0, "end": 3}, {"start": 3, "end": 6}]) == [])
    r.check("时段相交算重叠",
            len(G.overlapping_pairs([{"start": 0, "end": 4}, {"start": 3.5, "end": 6}])) == 1)
    r.check("裸底时长：中间空 2s",
            abs(G.bare_duration(10, [{"start": 0, "end": 4}, {"start": 6, "end": 10}]) - 2.0) < 1e-6,
            "%.2f" % G.bare_duration(10, [{"start": 0, "end": 4}, {"start": 6, "end": 10}]))

    ok = {"version": "compose/2", "canvas": {"width": 1920, "height": 1080}, "duration": 6,
          "layers": [{"templateId": "a", "position": "cc", "scale": 100, "start": 0, "end": 6}]}
    r.check("合规编排零原因", G.contract_reasons(ok) == [], str(G.contract_reasons(ok)))
    r.check("版本错会被抓出来", "version 不是 compose/2" in G.contract_reasons(dict(ok, version="compose/1")))
    r.check("画布错会被抓出来",
            "画布不是 1920×1080" in G.contract_reasons(dict(ok, canvas={"width": 1280, "height": 720})))


def _return(r: _Result) -> None:
    _emit("")
    if r.failed:
        _emit("自测没过（%d/%d）：%s" % (len(r.failed), len(r.passed) + len(r.failed),
                                          "；".join(r.failed)))
    else:
        _emit("全部 %d 条断言符合预期：坏样本响、亚像素放过、好样本静、几何口径一致" % len(r.passed))
