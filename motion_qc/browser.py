"""无头浏览器驱动：把探针塞进临时宿主页，跑一次，把结果读回来。

通信方式很土但可靠：探针把 JSON 写进 document.title，宿主页 dump 出来。
比接 CDP 少一个依赖，比自己实现 websocket 少两千行。
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from .config import Config

HOST_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>pending</title></head>"
    "<body style=\"margin:0;background:#fff\"></body></html>"
)

#: title 是 HTML 文本节点，里面的引号/尖括号会被转义回来。
_UNESCAPE = (("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))


class BrowserError(RuntimeError):
    """浏览器没跑起来——和「组件有问题」是两回事，报告里要分开说。"""


def _unescape(s: str) -> str:
    for a, b in _UNESCAPE:
        s = s.replace(a, b)
    return s


def _probe_source(cfg: Config, src_uri: str, viewport) -> str:
    raw = cfg.probe_path.read_text(encoding="utf-8")
    conf = {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "stageSelectors": list(cfg.stage_selectors),
        "tolerance": cfg.tolerance,
        "settleFrom": cfg.settle_from,
        "settleSamples": cfg.settle_samples,
        "minOpacity": cfg.min_opacity,
        "minDuration": cfg.min_duration,
        "maxDuration": cfg.max_duration,
        "minFrames": cfg.min_frames,
        "visibleSamples": cfg.visible_samples,
        "frameSamples": cfg.frame_samples,
        "loadTimeoutMs": cfg.load_timeout_ms,
        "fontTimeoutMs": cfg.font_timeout_ms,
        "settleMs": cfg.settle_ms,
    }
    return raw.replace("__SRC__", json.dumps(src_uri)).replace("__CFG__", json.dumps(conf))


def run_probe(target: Path, cfg: Config) -> dict:
    """在无头浏览器里跑一次探针，返回原始结果字典。

    浏览器起不来时抛 BrowserError；组件本身的问题都在返回值里，不抛异常。
    """
    chrome = cfg.chrome_bin()
    if not chrome:
        raise BrowserError(
            "找不到 Chrome/Edge。用 --chrome 指定路径，或设环境变量 MOTION_QC_CHROME"
        )

    target = Path(target).resolve()
    if not target.exists():
        raise BrowserError("文件不存在: %s" % target)
    src_uri = target.as_uri()
    viewport = cfg.canvas_viewport(target)

    page = HOST_PAGE + (
        "<script>window.__MQ = %s;\n"
        "(async () => {"
        "  try { document.title = JSON.stringify(await window.__MQ); }"
        "  catch (e) { document.title = JSON.stringify({ probeError: String(e && e.message || e) }); }"
        "})();</script>"
    ) % _probe_source(cfg, src_uri, viewport)

    work = cfg.work_dir() / ("probe-" + uuid.uuid4().hex[:12])
    work.mkdir(parents=True, exist_ok=True)
    host = work / "host.html"
    host.write_text(page, encoding="utf-8")

    w, h = viewport
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        "--allow-file-access-from-files",
        "--force-device-scale-factor=1",
        "--hide-scrollbars",
        "--window-size=%d,%d" % (min(max(w + 40, 400), 4000), min(max(h + 40, 300), 2500)),
        # 这两条把未捕获异常和解析错误送进 stderr。页内 error 监听只能抓到
        # **监听挂上之后**才发生的异常；组件脚本在自己执行的第一行就抛的，
        # 以及解析期就死掉的，页内一个都听不见。stderr 补的就是这一段。
        "--enable-logging=stderr",
        "--log-level=0",
        "--virtual-time-budget=%d" % cfg.virtual_time_budget,
        "--dump-dom",
        str(host),
    ] + list(cfg.chrome_args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        raise BrowserError("浏览器跑超时（%ds）。组件可能加载了外部资源，或 --virtual-time-budget 需要调大。"
                           % cfg.subprocess_timeout)
    finally:
        if not cfg.keep_tmp:
            for f in host.glob("*"):
                f.unlink(missing_ok=True)
            try:
                host.unlink()
                work.rmdir()
            except OSError:
                pass

    dom = proc.stdout or ""
    data = None
    m = re.search(r"<title>(.*?)</title>", dom, re.S)
    if m and m.group(1).strip() not in ("", "pending"):
        raw = _unescape(m.group(1))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BrowserError("探针返回的不是合法 JSON（%s）：%s" % (e, raw[:200]))
    else:
        tail = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        raise BrowserError(
            "探针没有返回结果。浏览器输出末尾：%s" % (tail[-1][:200] if tail else "(空)")
        )

    # stderr 里的浏览器控制台行：组件**自己**的报错，页内监听抓不到的那些。
    data["console"] = console_errors(proc.stderr or "", target)
    return data


#: Chrome 控制台行长这样（行号前是冒号还是括号，随版本变过，两种都收）：
#:   [12345:6789:...]INFO:CONSOLE:44] "Uncaught TypeError: ...", source: file:///x/y.html (44)
_RE_CONSOLE = re.compile(
    r'CONSOLE[:(]\s*(?P<line>\d+)\)?\]\s*"(?P<msg>.*?)"\s*,\s*source:\s*(?P<src>.+?)\s*$'
)


def console_errors(stderr: str, target: Path) -> list:
    """只留下**被检文件自己**的控制台报错，宿主页和浏览器自己的噪音丢掉。"""
    want = target.name.lower()
    out, seen = [], set()
    for line in stderr.splitlines():
        m = _RE_CONSOLE.search(line)
        if not m:
            continue
        src = re.sub(r"\s*\(\d+\)\s*$", "", m.group("src") or "")
        if want not in unquote(src).lower():
            continue
        msg = (m.group("msg") or "").strip()
        if not msg or msg in seen:
            continue
        seen.add(msg)
        out.append({"line": int(m.group("line")), "message": msg})
    return out[:20]


def unquote(url: str) -> str:
    from urllib.parse import unquote as _u

    return _u(url)


def _decode(data: bytes) -> str:
    """子进程输出的编码不归我们管（Chrome 在中文 Windows 上吐 GBK）。"""
    import locale

    for enc in ("utf-8", locale.getpreferredencoding(False), "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def browser_version(chrome: str | None) -> str:
    exe = chrome or ("chrome" if not _is_windows() else "chrome.exe")
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, timeout=30)
        out = _decode(p.stdout or b"") or _decode(p.stderr or b"")
        return out.strip().splitlines()[0] if out.strip() else "unknown"
    except Exception:
        return "unknown"


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"


def node_version(node: str) -> str:
    try:
        p = subprocess.run([node, "--version"], capture_output=True, timeout=30)
        return _decode(p.stdout or b"").strip()
    except Exception:
        return "unknown"
