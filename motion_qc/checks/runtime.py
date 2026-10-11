"""运行期探针的共享入口。

smoke 和 overflow 读的是同一次浏览器加载的结果，所以这里做一层缓存：
同一个文件在一次 `motion-qc check` 里只开一次浏览器。
"""
from __future__ import annotations

import threading
from pathlib import Path

from ..browser import BrowserError, run_probe
from ..config import Config

_lock = threading.Lock()
_cache: dict = {}


def probe_cached(target: Path, cfg: Config) -> dict:
    target = Path(target).resolve()
    viewport = cfg.canvas_viewport(target)
    key = (str(target), viewport, cfg.stage_selectors,
           cfg.tolerance, cfg.settle_from, cfg.settle_samples, cfg.min_duration,
           cfg.max_duration, cfg.min_frames, cfg.virtual_time_budget)
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        if isinstance(hit, Exception):
            raise hit
        return hit
    try:
        result = run_probe(target, cfg)
    except BrowserError as exc:
        result = exc
    with _lock:
        _cache[key] = result
    if isinstance(result, Exception):
        raise result
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()
