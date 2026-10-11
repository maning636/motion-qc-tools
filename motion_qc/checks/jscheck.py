"""pop_jscheck 的公开版：内联 <script> 的 JS 语法检查。

为什么需要它
------------
有一个组件的时间轴**完全没注册**，而：
  · 必备项扫描全绿（它确实写了 window.__timelines.main）
  · 批量渲帧显示「✓」并产出了文件（只是那片子几乎静止）
  · Chrome 运行时日志**一条异常都没有**
唯一说得通的解释是**解析期就死了**：一个 SyntaxError 会让整个 <script>
不执行，于是注册那行压根没跑到。而解析错误在无头 Chrome 的 error 事件里
**抓不到**——本仓库实测过：故意写一个 `const a = ;`，iframe 内部的
`window.addEventListener('error')` 一声不吭，只有全局没被建起来这件事
能反推出它挂了。所以只能静态验语法。

哪些块会被检查
--------------
**白名单思路：只有「确实是 JavaScript」的块才送 node --check。**
`<script>` 标签不只装 JS——`<script type="application/json">` 是数据块，
`<script type="application/ld+json">` 是结构化数据，`<script type="importmap">`
是模块映射。这些块的内容按 JSON 解析，送去当 JS 解析必然报错，
而且是**每一次都报**——一个完全合法的 compose/2 外部图层（用
`<script type="application/json" data-hyperframes-variables>` 做变量自描述）
会被判成语法错。所以先认 type，再决定要不要查。

只读，不改任何文件。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..config import Config
from .base import FAIL, SKIP, WARN, CheckResult, Finding

CHECK_ID = "jscheck"
TITLE = "JS 语法"

#: 取出 <script> 的开标签和内容。开标签单独抓，才能看 type / src。
RE_SCRIPT = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>", re.S | re.I)
RE_SRC = re.compile(r"\bsrc\s*=", re.I)
RE_TYPE = re.compile(r"""\btype\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s>]+))""", re.I)

#: 能交给 node --check 的 type → 临时文件后缀。不在表里的一律不当 JS。
#:   ""         没写 type：传统脚本
#:   "module"   ES module。存成 .mjs，老版本 node 才不会把 import/export 误判成错
PARSEABLE_TYPES = {
    "": "js",
    "module": "mjs",
    "text/javascript": "js",
    "application/javascript": "js",
    "application/ecmascript": "js",
    "text/ecmascript": "js",
}

#: 是 JS 语族、但 node 的解析器吃不了的。照查必然误报，所以跳过——但要在报告里说。
UNPARSEABLE_JS = {
    "text/babel": "含 JSX，node --check 解析不了",
    "text/jsx": "含 JSX，node --check 解析不了",
    "text/typescript": "TypeScript，node --check 解析不了",
    "application/typescript": "TypeScript，node --check 解析不了",
    "application/x-typescript": "TypeScript，node --check 解析不了",
}


def extract_blocks(html: str) -> list:
    """把内联 <script> 拆成 [{type, index, body}]。带 src 的外部脚本不进这里。"""
    out = []
    for m in RE_SCRIPT.finditer(html):
        attrs = m.group("attrs")
        if RE_SRC.search(attrs):
            continue
        t = RE_TYPE.search(attrs)
        script_type = ""
        if t:
            script_type = (t.group("dq") or t.group("sq") or t.group("bare") or "").strip().lower()
        out.append({"type": script_type, "index": len(out) + 1, "body": m.group("body")})
    return out


def classify(block: dict):
    """→ ('parse', suffix) | ('unparseable', 理由) | ('data', 说明)"""
    st = block["type"]
    if st in PARSEABLE_TYPES:
        return "parse", PARSEABLE_TYPES[st]
    if st in UNPARSEABLE_JS:
        return "unparseable", UNPARSEABLE_JS[st]
    if not st:
        return "data", "没有 type 属性却不在白名单里"
    if st.endswith("json") or st.endswith("+json") or st in ("importmap", "speculationrules"):
        return "data", "数据块，不是 JavaScript"
    return "data", "非 JavaScript 的 script type"


def run(target: Path, cfg: Config) -> CheckResult:
    res = CheckResult(CHECK_ID, TITLE)
    path = Path(target)
    html = path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_blocks(html)
    res.info["inlineScriptBlocks"] = len(blocks)

    if not blocks:
        res.status = SKIP
        res.add(Finding("JS_NO_INLINE", "文件里没有内联 <script>，这一项无需检查", SKIP))
        return res

    to_check, data_blocks, unchecked_js = [], [], []
    for b in blocks:
        kind, why = classify(b)
        if kind == "parse":
            to_check.append((b, why))
        elif kind == "unparseable":
            unchecked_js.append({"index": b["index"], "type": b["type"] or "(无)", "reason": why})
        else:
            data_blocks.append({"index": b["index"], "type": b["type"] or "(无)", "reason": why})

    res.info["checked"] = [{"index": b["index"], "type": b["type"] or "(无)", "as": suffix}
                           for b, suffix in to_check]
    res.info["dataBlocks"] = data_blocks
    res.info["uncheckedJs"] = unchecked_js
    if data_blocks:
        res.info["dataBlockTypes"] = sorted({str(s["type"]) for s in data_blocks})

    if not to_check:
        # 一段 JS 都没有 → 这是「没查」，不是「通过」。
        res.status = SKIP
        res.add(
            Finding(
                "JS_NO_PARSABLE",
                "内联 <script> 共 %d 段，但都不是 JavaScript（type：%s），这一项无从检查"
                % (len(blocks), "、".join(sorted({str(s["type"]) for s in data_blocks + unchecked_js}))),
                SKIP,
                detail=data_blocks + unchecked_js,
            )
        )
        return res

    node = cfg.node_bin()
    if node:
        return _check_with_node(res, node, to_check, unchecked_js, cfg)

    # 没有 node：退到浏览器控制台日志。这不是完整解析器（只报解析期就炸的），
    # 但比什么都不做强，而且不需要装任何东西。
    from ..browser import BrowserError
    from .runtime import probe_cached

    if not cfg.chrome_bin():
        res.status = SKIP
        res.info["hint"] = "装 Node.js 后重跑，或用 --node 指定路径"
        res.add(
            Finding(
                "JS_NO_NODE",
                "%s：语法检查没跑。"
                "提示：烟测大概率会同时报「时间轴没注册」，但那是推断不是诊断。"
                % ("--node 指向的路径不存在：%s" % cfg.node_miss if cfg.node_miss
                   else "找不到 node，也没有浏览器可退回"),
                SKIP,
            )
        )
        return res

    try:
        data = probe_cached(path, cfg)
    except BrowserError as exc:
        res.status = SKIP
        res.add(Finding("JS_NO_ENGINE", "没有 node，浏览器也退不回去：%s" % exc, SKIP))
        return res

        if syntax:
            for rec in syntax:
                res.add(Finding("JS_SYNTAX", "%s（第 %s 行）" % (rec["message"], rec.get("line")), FAIL,
                                detail=rec))
            res.info["headline"] = "解析失败——整个 <script> 不会执行"
            _note_unchecked(res, unchecked_js)
            return res
        res.status = SKIP
        res.add(
            Finding(
                "JS_DEGRADED",
                "没有 node，只做了退化检查（未发现解析错误，但不等于全部合法）。"
                "装 Node.js 后重跑可得到完整判定。",
                SKIP,
            )
        )
        return res


def _check_with_node(res: CheckResult, node: str, to_check, unchecked_js, cfg: Config) -> CheckResult:
    """逐块 node --check。一段一段查，出了错能指到具体第几段。"""
    res.info["engine"] = "node --check"
    failed = 0
    for block, suffix in to_check:
        label = "第 %d 段%s" % (block["index"], "（type=%s）" % block["type"] if block["type"] else "")
        with tempfile.NamedTemporaryFile(
            "w", suffix="." + suffix, delete=False, encoding="utf-8", dir=str(cfg.work_dir())
        ) as fh:
            fh.write(block["body"])
            tmp_js = Path(fh.name)
        try:
            proc = subprocess.run(
                [node, "--check", str(tmp_js)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cfg.node_timeout,
            )
        except subprocess.TimeoutExpired:
            res.status = SKIP
            res.add(Finding("JS_NODE_TIMEOUT", "node --check 超时，%s 没查成" % label, SKIP))
            continue
        finally:
            tmp_js.unlink(missing_ok=True)

        if proc.returncode == 0:
            continue
        failed += 1
        lines = [ln.rstrip() for ln in (proc.stderr or proc.stdout or "").strip().splitlines()]
        # node 的第一行是「文件路径 + 报错摘要」，后面是源码片段和位置指针。
        # 去掉纯源码/指针行，只留人读得懂的部分。临时文件名对用户没意义，换成
        # 「内联脚本」——行号是相对这一段脚本的，本来就不指向 HTML 的行。
        detail_lines = [
            ln.replace(str(tmp_js), "<内联脚本>")
            for ln in lines
            if ln and not ln.lstrip().startswith(("^", "|"))
        ]
        msg = " / ".join(ln.strip() for ln in detail_lines[:4]) or (lines[0] if lines else "语法错")
        res.add(
            Finding("JS_SYNTAX", "%s 解析失败：%s" % (label, msg), FAIL,
                    detail={"exitCode": proc.returncode, "block": block["index"],
                            "type": block["type"] or "(无)", "raw": "\n".join(lines[:12])})
        )

    if failed:
        res.info["headline"] = "%d / %d 段 JavaScript 解析失败——整段不会执行" % (
            failed, len(to_check))
        return res

    res.info["headline"] = "%d 段 JavaScript 全部通过 node --check" % len(to_check)
    _note_unchecked(res, unchecked_js)
    return res


def _note_unchecked(res: CheckResult, unchecked_js) -> None:
    """有 JS 语族但 node 解析不了的块（JSX / TypeScript）要说一声。

    数据块（application/json、importmap……）**不说**：那些本来就不是 JS，
    跳过是正确处理，不是缺口。compose/2 外部图层普遍用
    `<script type="application/json" data-hyperframes-variables>` 做变量自描述，
    对每一条都报警等于没报警。它们只进 info。
    """
    if not unchecked_js:
        return
    by_type = {}
    for s in unchecked_js:
        by_type.setdefault(str(s["type"]), s["reason"])
    res.add(
        Finding(
            "JS_BLOCKS_UNCHECKED",
            "有 %d 段 <script> 没查（type：%s）——是 JS 语族，但 node --check 解析不了。"
            "需要覆盖的话得换解析器。"
            % (len(unchecked_js), "、".join(sorted(by_type))),
            WARN,
            detail=unchecked_js,
        )
    )


__all__ = ["run", "CHECK_ID", "TITLE", "extract_blocks", "classify",
           "PARSEABLE_TYPES", "UNPARSEABLE_JS"]
