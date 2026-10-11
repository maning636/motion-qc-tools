"""compose/2 编排核对——站点测试区验收卡的前四项，搬到命令行。

对应关系（前端 → 本项）：
    pgQcContract    → QC_COMPOSE_CONTRACT
    pgLayerFrameBox → QC_COMPOSE_OUT_OF_FRAME（运动包络，含 24 步采样）
    时段重叠        → QC_COMPOSE_OVERLAP
    裸底时长        → QC_COMPOSE_BARE

第五项「素材可溯源」需要素材池 catalog，留在服务端，CLI 不做——
没有 catalog 却报「全部命中」和原版把「未检查」当成「通过」是一个错误，
这里直接不列这一项，而不是给它一个假的绿勾。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..geometry import (
    DESIGN_H,
    DESIGN_W,
    TOLERANCE_PX,
    bare_duration,
    contract_reasons,
    layer_frame_box,
    out_of_frame_sides,
    overlapping_pairs,
)
from .base import FAIL, PASS, WARN, CheckResult, Finding

CHECK_ID = "compose"
TITLE = "compose/2 编排"


def run(target: Path, cfg: Config) -> CheckResult:
    res = CheckResult(CHECK_ID, TITLE)
    raw = Path(target).read_text(encoding="utf-8", errors="replace")
    try:
        compose = json.loads(raw)
    except json.JSONDecodeError as exc:
        res.add(Finding("QC_COMPOSE_PARSE", "不是合法 JSON：%s" % exc, FAIL))
        return res

    canvas = (compose.get("canvas") if isinstance(compose, dict) else None) or {}
    cw = float(canvas.get("width") or DESIGN_W)
    ch = float(canvas.get("height") or DESIGN_H)
    layers = compose.get("layers") if isinstance(compose, dict) else None
    res.info.update({"canvas": {"w": cw, "h": ch}, "layerCount": len(layers or [])})

    if not isinstance(layers, list) or not layers:
        # 没有图层时不能因为「空数组结构合法」就报合规——空片子「通过验收」
        # 是最容易被甲方当场问住的那种假通过。这里一律走 WARN 并说明。
        reasons = contract_reasons(compose)
        for r in reasons:
            res.add(Finding("QC_COMPOSE_CONTRACT", r, FAIL))
        res.add(Finding("QC_COMPOSE_EMPTY", "编排里没有图层，无从验收", WARN))
        res.info["headline"] = "未检查：没有图层"
        return res

    for reason in contract_reasons(compose):
        res.add(Finding("QC_COMPOSE_CONTRACT", reason, FAIL))

    # 出框：按运动包络扫
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            continue
        box = layer_frame_box(layer, cw, ch)
        sides = out_of_frame_sides(box, cw, ch, TOLERANCE_PX)
        if not sides:
            continue
        name = layer.get("name") or layer.get("templateId") or layer.get("type") or "未命名"
        res.add(
            Finding(
                "QC_COMPOSE_OUT_OF_FRAME",
                "第 %d 层「%s」出%s（运动包络外框 %.0f,%.0f – %.0f,%.0f，画布 %d×%d）"
                % (i + 1, name, "".join(sides), box["x0"], box["y0"], box["x1"], box["y1"],
                   int(cw), int(ch)),
                detail={"index": i + 1, "name": name, "sides": sides, "box": box},
            )
        )

    pairs = overlapping_pairs(layers)
    res.info["overlapPairs"] = len(pairs)
    if pairs:
        names = [
            (layers[i].get("templateId") or layers[i].get("name") or "第%d层" % (i + 1))
            for pair in pairs[:3] for i in pair
        ]
        res.add(
            Finding(
                "QC_COMPOSE_OVERLAP",
                "%d 对图层时段相交——同一秒有两样东西抢画面（%s%s）"
                % (len(pairs), "、".join(dict.fromkeys(names)),
                   "…" if len(pairs) > 3 else ""),
                FAIL,
                detail={"pairs": pairs},
            )
        )

    duration = float(compose.get("duration") or 0)
    bare = bare_duration(duration, layers)
    res.info["bareSeconds"] = round(bare, 3)
    if bare > 0.05:
        res.add(
            Finding("QC_COMPOSE_BARE", "裸底 %.1fs——这段只有底片、没有动效" % bare, FAIL,
                    detail={"bare": bare})
        )

    if res.status == PASS:
        res.info["headline"] = "%d 层 · %g×%g · %gs · 无出框、无重叠、无裸底" % (
            len(layers), cw, ch, duration,
        )
    return res
