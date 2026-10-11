"""运行参数。全部来自命令行 / 环境变量，不带任何机器相关的默认值。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: 根容器候选。第一个命中就用。允许用户用 --stage 覆盖。
DEFAULT_STAGE_SELECTORS = (
    "#stage",
    "#root",
    "[data-composition-id]",
    "main",
    "body",
)

#: 各平台上 Chrome / Edge 的常见安装位置。探测顺序即列表顺序。
CHROME_CANDIDATES = {
    "win32": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ),
    "linux": (
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ),
}

ENV_CHROME = "MOTION_QC_CHROME"
ENV_NODE = "MOTION_QC_NODE"

#: 读不到声明时的兜底视口。
DEFAULT_VIEWPORT = (1920, 1080)

#: 动效文件基本都会在 <meta name="viewport" content="width=480, height=270">
#: 里声明自己的画布。探针必须按**它声明的尺寸**开视口：视口大小会改变
#: 百分比 / vw / 100vh 这类布局，用错了尺寸，量到的就不是它本来的样子。
_RE_META = re.compile(r"<meta\b[^>]*>", re.I)
_RE_VP_NAME = re.compile(r"""name\s*=\s*["']?viewport["']?""", re.I)
_RE_VP_SIZE = re.compile(
    r"""width\s*=\s*["']?(\d+(?:\.\d+)?)["']?\s*[,;x\s]+\s*height\s*=\s*["']?(\d+(?:\.\d+)?)""",
    re.I,
)


def declared_canvas(path) -> tuple | None:
    """读文件自己声明的画布尺寸，读不到返回 None。只读文件头，不改。"""
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return None
    for tag in _RE_META.findall(head):
        if not _RE_VP_NAME.search(tag):
            continue
        m = _RE_VP_SIZE.search(tag)
        if m:
            w, h = int(float(m.group(1))), int(float(m.group(2)))
            if w > 0 and h > 0:
                return (w, h)
    return None


@dataclass
class Config:
    """一次检查运行的全部可调项。"""

    # 浏览器
    chrome: str | None = None
    chrome_args: tuple[str, ...] = ()
    #: None = 自动：优先用文件自己声明的 meta viewport，再兜底 1920×1080。
    viewport: tuple | None = None
    load_timeout_ms: int = 6000
    settle_ms: int = 500
    #: 等字体加载完的上限。超时照常量，但「文字出框」那部分结论会打折。
    font_timeout_ms: int = 4000
    virtual_time_budget: int = 20000
    subprocess_timeout: int = 120

    # 出框判定
    stage_selectors: tuple[str, ...] = DEFAULT_STAGE_SELECTORS
    tolerance: float = 0.5
    settle_from: float = 0.76
    settle_samples: int = 4
    min_opacity: float = 0.05

    # 烟测判定
    min_duration: float = 1.2
    max_duration: float = 60.0
    min_frames: int = 2
    visible_samples: int = 7
    frame_samples: int = 12

    # 静态检查
    node: str | None = None
    node_timeout: int = 60
    #: 显式指定的 node 路径指错了，原样记下来给报告用。
    node_miss: str | None = None

    # 运行
    jobs: int = 4
    tmp_dir: Path | None = None
    keep_tmp: bool = False

    probe_path: Path = field(default_factory=lambda: Path(__file__).with_name("assets") / "probe.js")

    def canvas_viewport(self, path) -> tuple:
        """这次探测该用多大的视口：显式指定 > 文件声明 > 兜底。"""
        if self.viewport:
            return self.viewport
        return declared_canvas(Path(path)) or DEFAULT_VIEWPORT

    def chrome_bin(self) -> str | None:
        """按「显式指定 → 环境变量 → 常见安装位置 → PATH」的顺序找浏览器。"""
        import shutil
        import sys

        for candidate in (self.chrome, os.environ.get(ENV_CHROME)):
            if candidate and (Path(candidate).exists() or shutil.which(candidate)):
                return candidate
        for cand in CHROME_CANDIDATES.get(sys.platform, ()):
            if Path(cand).exists():
                return cand
        for name in ("chrome", "google-chrome", "chromium", "chrome.exe", "msedge"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def node_bin(self) -> str | None:
        """node 是 JS 语法检查的硬依赖。

        显式指定了却指错了，就返回 None（该项照实 skip），**不**悄悄换一个
        别的 node——悄悄换一个比不检查更糟，报出来的东西对不上号。
        """
        import shutil

        override = self.node or os.environ.get(ENV_NODE)
        if override:
            resolved = override if Path(override).exists() else shutil.which(override)
            if resolved:
                return resolved
            self.node_miss = override
            return None
        self.node_miss = None
        return shutil.which("node") or shutil.which("nodejs")

    def work_dir(self) -> Path:
        if self.tmp_dir is None:
            base = Path(os.environ.get("MOTION_QC_TMP") or (Path.home() / ".cache" / "motion-qc"))
        else:
            base = Path(self.tmp_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base
