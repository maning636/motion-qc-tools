"""compose/2 的图层几何——和站点测试区验收卡**同一套算式**。

这里的每一个常数、每一条边界都来自前端 `pgLayerFrameBox` / `pgMotionOffsetPx` /
`pgQcContract`，是它们的逐行移植。两侧改一处必须同时改另一处，
`tests/test_geometry.py` 里锁了几个已知值做回归。

为什么要有这层
--------------
浏览器里那套判定跑在**已编排好的图层**上（compose/2 JSON），量的是
「这一层在 1920×1080 画布上、含运动轨迹甩出去的最远处」的外框。
DOM 出框扫描量的是同一个物理事实的另一个投影——但它看不见还没被渲染出来的编排。
两套判据必须同源，否则同一个片子在站点里判过、在 CI 里判不过，
或者反过来，那就没有任何可信度可言。

口径：
  · 九宫格锚点表（tl/tc/tr/cl/cc/cr/bl/bc/br）
  · 基准宽 34% 画布宽，随 scale 线性缩放，scale 夹在 20–200
  · 运动轨迹按 24 步采样，取包络（路径运动不单调，只看首尾会漏）
  · 出框容差 0.5px

单位上有一处**必须照抄、不要"修正"**的不一致，它是前端现状：
  · line 的 ``dx`` / ``dy`` 是**设计画布像素**（UI 里标的就是 px，默认 dy=-80），
    换算成实际舞台要乘 ``stage_w / 1920``；
  · path 的 ``points`` 是**画布宽高的比例**（0–1），直接乘 ``stage_w``。
两种运动类型的单位不一样。这里保持原样，否则同一个 compose 在站点判过、
在 CLI 判不过——判定不一致的工具没有可信度可言。

移植保真度：12 组用例（含非法锚点、字符串型字段、超短路径、半尺寸画布、
出点早于运动终点、两种缓动）与 app.js 原函数逐位对照，数值完全一致。
"""
from __future__ import annotations

import math

#: 九宫格锚点（画布宽高的百分比）。前端同名常量 PG_ANCHORS。
ANCHORS = {
    "tl": (8, 10), "tc": (50, 10), "tr": (92, 10),
    "cl": (8, 50), "cc": (50, 50), "cr": (92, 50),
    "bl": (8, 88), "bc": (50, 88), "br": (92, 88),
}
ANCHOR_KEYS = frozenset(ANCHORS)

BASE_WIDTH_PCT = 34.0
MIN_SCALE, MAX_SCALE = 20.0, 200.0
MOTION_STEPS = 24
TOLERANCE_PX = 0.5
BARE_TOLERANCE_S = 0.05

DESIGN_W, DESIGN_H = 1920, 1080


def _num(v, default=0.0):
    """等价于 JS 的 Number(x) || default——0 / '' / NaN 都落到 default。"""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else default
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except (TypeError, ValueError):
            return default
        return f if math.isfinite(f) else default
    return default


def clamp_scale(value) -> float:
    return max(MIN_SCALE, min(MAX_SCALE, _num(value, 100.0) or 100.0))


def motion_offset_px(layer: dict, t: float, stage_w: float, stage_h: float) -> tuple:
    """某一层在 t 时刻相对锚点的位移（像素）。移植自 pgMotionOffsetPx。"""
    m = layer.get("motion")
    if not isinstance(m, dict):
        return 0.0, 0.0
    secs = _num(m.get("secs"), 0.0)
    if not math.isfinite(secs):
        return 0.0, 0.0
    start = _num(layer.get("start"), 0.0)
    if secs == 0:
        return 0.0, 0.0
    p = min(1.0, max(0.0, (t - start) / secs))
    if p <= 0:
        return 0.0, 0.0
    eased = p if m.get("ease") == "linear" else 1 - (1 - p) ** 2
    scale_x = stage_w / DESIGN_W
    scale_y = stage_h / DESIGN_H

    points = m.get("points") or []
    if m.get("type") == "path" and isinstance(points, list) and len(points) >= 2:
        seg_lens, total = [], 0.0
        for i in range(1, len(points)):
            sx = (_num(points[i][0]) - _num(points[i - 1][0])) * stage_w
            sy = (_num(points[i][1]) - _num(points[i - 1][1])) * stage_h
            ln = math.sqrt(sx * sx + sy * sy)
            seg_lens.append(ln)
            total += ln
        if total < 0.5:
            return 0.0, 0.0
        target = eased * total
        acc = 0.0
        for i, ln in enumerate(seg_lens):
            if acc + ln >= target:
                seg_t = (target - acc) / ln if ln > 0.001 else 0.0
                x0, y0 = _num(points[i][0]), _num(points[i][1])
                x1, y1 = _num(points[i + 1][0]), _num(points[i + 1][1])
                return (x0 + (x1 - x0) * seg_t) * stage_w, (y0 + (y1 - y0) * seg_t) * stage_h
            acc += ln
        last = points[-1]
        return _num(last[0]) * stage_w, _num(last[1]) * stage_h

    return _num(m.get("dx")) * eased * scale_x, _num(m.get("dy")) * eased * scale_y


def layer_frame_box(layer: dict, canvas_w: float, canvas_h: float) -> dict:
    """某一层在画布上的最小外框，含运动轨迹途中甩出去的最远处。
    移植自 pgLayerFrameBox。"""
    ax, ay = ANCHORS.get(layer.get("position"), ANCHORS["cc"])
    ratio = BASE_WIDTH_PCT * (clamp_scale(layer.get("scale")) / 100.0) / 100.0
    hw = canvas_w * ratio / 2
    hh = canvas_h * ratio / 2
    cx = ((ax + _num(layer.get("x"))) / 100.0) * canvas_w
    cy = ((ay + _num(layer.get("y"))) / 100.0) * canvas_h
    x0, x1, y0, y1 = cx - hw, cx + hw, cy - hh, cy + hh

    m = layer.get("motion")
    if isinstance(m, dict):
        secs = _num(m.get("secs"), 0.0)
        if math.isfinite(secs) and secs > 0:
            start = _num(layer.get("start"), 0.0)
            end = _num(layer.get("end"), start) or start
            end = min(end, start + secs)
            for i in range(MOTION_STEPS + 1):
                dx, dy = motion_offset_px(layer, start + (end - start) * i / MOTION_STEPS,
                                          canvas_w, canvas_h)
                x0 = min(x0, cx - hw + dx)
                x1 = max(x1, cx + hw + dx)
                y0 = min(y0, cy - hh + dy)
                y1 = max(y1, cy + hh + dy)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "w": x1 - x0, "h": y1 - y0}


def out_of_frame_sides(box: dict, canvas_w: float, canvas_h: float,
                       tolerance: float = TOLERANCE_PX) -> list:
    """外框越过哪几条边。移植自 pgQcReport 的出框判定。"""
    sides = []
    if box["x0"] < -tolerance:
        sides.append("左")
    if box["x1"] > canvas_w + tolerance:
        sides.append("右")
    if box["y0"] < -tolerance:
        sides.append("上")
    if box["y1"] > canvas_h + tolerance:
        sides.append("下")
    return sides


def contract_reasons(compose) -> list:
    """compose/2 契约核对。移植自 pgQcContract，返回能直接贴到卡上的人话。"""
    reasons = []
    if not isinstance(compose, dict):
        return ["编排对象没生成出来"]
    if compose.get("version") != "compose/2":
        reasons.append("version 不是 compose/2")
    cv = compose.get("canvas") or {}
    if _num(cv.get("width")) != DESIGN_W or _num(cv.get("height")) != DESIGN_H:
        reasons.append("画布不是 1920×1080")
    d = _num(compose.get("duration"), None)
    if d is None or d < 3 or d > 600:
        reasons.append("时长越界（须 3–600s）")
    layers = compose.get("layers")
    if not isinstance(layers, list):
        reasons.append("缺 layers 数组")
        return reasons
    for i, layer in enumerate(layers):
        tag = "第 %d 层" % (i + 1)
        if not isinstance(layer, dict):
            reasons.append(tag + "不是对象")
            continue
        if layer.get("type") == "external":
            if not isinstance(layer.get("html"), str) or not layer["html"].strip():
                reasons.append(tag + "外部层没有 HTML")
        elif not layer.get("templateId"):
            reasons.append(tag + "缺 templateId")
        if layer.get("position") not in ANCHOR_KEYS:
            reasons.append(tag + "锚点非法")
        s_raw, e_raw = layer.get("start"), layer.get("end")
        s, e = _num(s_raw, None), _num(e_raw, None)
        if s is None or e is None:
            reasons.append(tag + "入出点不是数字")
        elif e <= s:
            reasons.append(tag + "出点不晚于入点")
        elif math.isfinite(d) and e > d + 1e-6:
            reasons.append(tag + "出点超出成片时长")
        sc = _num(layer.get("scale"), None)
        if sc is None or not (MIN_SCALE <= sc <= MAX_SCALE):
            reasons.append(tag + "缩放越界")
    return reasons


def overlapping_pairs(layers) -> list:
    """两两可见时段相交的对。移植自 pgQcReport 的重叠判定。"""
    spans = []
    for layer in layers if isinstance(layers, list) else []:
        if not isinstance(layer, dict):
            continue
        s, e = _num(layer.get("start"), 0.0), _num(layer.get("end"), 0.0)
        if e > s:
            spans.append((s, e))
    out = []
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            if min(spans[i][1], spans[j][1]) - max(spans[i][0], spans[j][0]) > 1e-6:
                out.append((i, j))
    return out


def bare_duration(duration: float, layers) -> float:
    """全程没有任何动效层在屏的秒数。移植自 pgQcReport 的裸底判定。"""
    d = max(0.0, _num(duration, 0.0))
    spans = []
    for layer in layers if isinstance(layers, list) else []:
        if not isinstance(layer, dict):
            continue
        s = max(0.0, _num(layer.get("start"), 0.0))
        e = min(d, _num(layer.get("end"), 0.0))
        if e > s:
            spans.append((s, e))
    spans.sort(key=lambda p: p[0])
    covered, cursor = 0.0, 0.0
    for s, e in spans:
        a = max(s, cursor)
        if e > a:
            covered += e - a
            cursor = e
    return max(0.0, d - covered)
