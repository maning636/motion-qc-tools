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

只读，不改任何文件。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..config import Config
from .base import FAIL, SKIP, CheckResult, Finding

CHECK_ID = "jscheck"
TITLE = "JS 语法"

#: 只取内联脚本：有 src 属性的不取（那是外部文件，逐个查是另一回事）。
RE_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)


def run(target: Path, cfg: Config) -> CheckResult:
    res = CheckResult(CHECK_ID, TITLE)
    path = Path(target)
    html = path.read_text(encoding="utf-8", errors="replace")
    blocks = RE_INLINE_SCRIPT.findall(html)
    res.info["inlineScriptBlocks"] = len(blocks)

    if not blocks:
        res.status = SKIP
        res.add(Finding("JS_NO_INLINE", "文件里没有内联 <script>，这一项无需检查", SKIP))
        return res

    node = cfg.node_bin()
    if node:
        js = "\n\n".join(blocks)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8", dir=str(cfg.work_dir())
        ) as fh:
            fh.write(js)
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
            res.add(Finding("JS_NODE_TIMEOUT", "node --check 超时，这一项没跑成", SKIP))
            return res
        finally:
            tmp_js.unlink(missing_ok=True)

        if proc.returncode == 0:
            res.info.update({"engine": "node --check",
                             "headline": "%d 段内联脚本全部通过 node --check" % len(blocks)})
            return res

        lines = [ln.rstrip() for ln in (proc.stderr or proc.stdout or "").strip().splitlines()]
        # node 的第一行是「文件路径 + 报错摘要」，后面是源码片段和位置指针。
        # 去掉纯源码/指针行，只留人读得懂的部分。临时文件名对用户没意义，换成
        # 「内联脚本」——行号是相对拼接后的脚本的，本来就不指向 HTML 的行。
        detail_lines = [
            ln.replace(str(tmp_js), "<内联脚本>")
            for ln in lines
            if ln and not ln.lstrip().startswith(("^", "|"))
        ]
        msg = " / ".join(ln.strip() for ln in detail_lines[:4]) or (lines[0] if lines else "语法错")
        res.add(
            Finding("JS_SYNTAX", msg, FAIL,
                    detail={"exitCode": proc.returncode, "raw": "\n".join(lines[:12])})
        )
        res.info["engine"] = "node --check"
        res.info["headline"] = "解析失败——整个 <script> 不会执行"
        return res

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

    syntax = [r for r in (data.get("console") or []) if "SyntaxError" in (r.get("message") or "")]
    res.info["engine"] = "浏览器控制台日志（退化路径，只报解析期就炸的）"
    if syntax:
        for rec in syntax:
            res.add(Finding("JS_SYNTAX", "%s（第 %s 行）" % (rec["message"], rec.get("line")), FAIL,
                            detail=rec))
        res.info["headline"] = "解析失败——整个 <script> 不会执行"
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
