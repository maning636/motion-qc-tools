"""命令行入口。

    motion-qc check  a.html b/ --json report.json
    motion-qc selftest
    motion-qc doctor
    motion-qc explain overflow

退出码：0 全部通过（有 skip 不算失败）· 1 有不合格项 · 2 工具自己跑不起来
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from .checks import CHECKS, DESCRIPTIONS
from .checks.base import FileReport, display_path

HTML_EXT = {".html", ".htm"}
JSON_EXT = {".json"}


def _utf8_stdout() -> None:
    """Windows 终端默认 GBK，中文报告会炸。这三行是唯一可靠的兜底。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def collect(targets, base: Path | None = None) -> list:
    """把命令行给的路径展开成待检文件清单（目录递归）。"""
    out, seen = [], set()
    for t in targets:
        p = Path(t)
        if p.is_dir():
            for ext in (".html", ".htm", ".json"):
                for f in sorted(p.rglob("*" + ext)):
                    key = f.resolve()
                    if key not in seen:
                        seen.add(key)
                        out.append(f)
        else:
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def common_base(paths) -> Path:
    """报告里显示相对路径，基准取所有目标的公共目录。

    扫一个目录树时，一堆文件全叫 index.html，光看报告根本分不清谁是谁——
    所以基准不是 cwd，是这些路径的公共前缀。
    """
    resolved = [Path(p).resolve() for p in paths]
    if not resolved:
        return Path.cwd()
    if len(resolved) == 1:
        one = resolved[0]
        return one.parent if one.is_file() else one
    parts = [p.parts for p in resolved]
    common = []
    for segs in zip(*parts):
        if len(set(segs)) != 1:
            break
        common.append(segs[0])
    return Path(*common) if common else Path(resolved[0].anchor)


def _applies(check_id: str, path: Path, only) -> bool:
    if only and check_id not in only:
        return False
    if check_id == "compose":
        return path.suffix.lower() in JSON_EXT
    return path.suffix.lower() in HTML_EXT


def build_config(args) -> "Config":  # noqa: F821
    from .config import Config

    cfg = Config()
    if args.chrome:
        cfg.chrome = args.chrome
    if args.chrome_arg:
        cfg.chrome_args = tuple(args.chrome_arg)
    if args.viewport:
        cfg.viewport = args.viewport
    if args.stage:
        cfg.stage_selectors = tuple(args.stage)
    cfg.tolerance = args.tolerance
    cfg.settle_from = args.settle_from
    cfg.settle_samples = args.settle
    cfg.min_duration = args.min_duration
    cfg.max_duration = args.max_duration
    cfg.min_frames = args.max_frames
    if args.node:
        cfg.node = args.node
    cfg.jobs = max(1, args.jobs)
    if args.tmp:
        cfg.tmp_dir = Path(args.tmp)
    cfg.keep_tmp = getattr(args, "keep_tmp", False)
    return cfg


def check_one(path: Path, cfg, only, base: Path) -> FileReport:
    from .checks import compose, jscheck, overflow, smoke
    from .checks.base import SKIP, CheckResult, Finding

    registry = {"smoke": smoke, "overflow": overflow, "jscheck": jscheck, "compose": compose}
    fr = FileReport(display_path(path, base))
    for check_id in CHECKS:
        if _applies(check_id, path, only):
            fr.checks.append(registry[check_id].run(path, cfg))
    if not fr.checks:
        none = CheckResult("none", "无适用检查", status=SKIP)
        none.add(Finding("QC_NOT_APPLICABLE", "文件类型不在任何一项的检查范围", SKIP))
        fr.checks.append(none)
    return fr


def cmd_check(args) -> int:
    import time

    from . import report as reporter

    targets = collect(args.targets)
    if not targets:
        sys.stderr.write("没有可检的文件：%s\n" % " ".join(args.targets))
        return 2

    only = None
    if args.only:
        only = [x.strip() for x in args.only.split(",") if x.strip()]
        unknown = [x for x in only if x not in CHECKS]
        if unknown:
            sys.stderr.write("未知的检查项：%s（可选：%s）\n" % (",".join(unknown), ",".join(CHECKS)))
            return 2

    base = common_base(targets)
    cfg = build_config(args)
    started = time.time()
    if args.jobs > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=cfg.jobs) as pool:
            files = list(pool.map(lambda p: check_one(p, cfg, only, base), targets))
    else:
        files = [check_one(p, cfg, only, base) for p in targets]
    elapsed = time.time() - started

    if args.quiet:
        pass
    else:
        reporter.render_text(files, cfg=cfg, only=only, verbose=args.verbose)

    payload = reporter.to_dict(files, cfg=cfg, elapsed=elapsed)
    if args.json:
        if args.json == "-":
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            reporter.write_json(payload, args.json)
            if not args.quiet:
                sys.stdout.write("JSON 报告已写入 %s\n" % args.json)

    return 0 if payload["summary"]["ok"] else 1


def cmd_selftest(args) -> int:
    from .selftest import run_selftest

    return run_selftest(keep=args.keep_tmp)


def cmd_doctor(args) -> int:
    from .browser import browser_version, node_version

    cfg = build_config(args)
    chrome = cfg.chrome_bin()
    print("motion-qc %s  Python %s" % (__version__, sys.version.split()[0]))
    print("")
    print("Chrome/Edge : %s" % (chrome or "没找到"))
    if chrome:
        print("             %s" % browser_version(chrome))
    print("             覆盖方式：--chrome / 环境变量 MOTION_QC_CHROME")
    node = cfg.node_bin()
    print("node        : %s" % (node or "没找到（jscheck 会标成 skip）"))
    if node:
        print("             %s" % node_version(node))
    print("临时目录    : %s" % cfg.work_dir())
    print("根容器候选  : %s" % " → ".join(cfg.stage_selectors))
    print("")
    for k in CHECKS:
        print("  %-9s %s" % (k, DESCRIPTIONS[k]))
    ok = bool(chrome)
    print("")
    print("结论：%s" % ("可以干活（出框/烟测需要浏览器）" if ok else
                      "缺浏览器——只有 jscheck 和 compose 能跑"))
    return 0 if ok else 2


def cmd_explain(args) -> int:
    key = args.check
    if key not in DESCRIPTIONS:
        sys.stderr.write("没有这一项：%s（可选：%s）\n" % (key, ", ".join(CHECKS)))
        return 2
    from .explain import EXPLAIN

    print(EXPLAIN[key])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="motion-qc",
        description="AI 动效验收工具：把你生成的动效 HTML 喂进来，告诉你哪里不行。",
    )
    p.add_argument("--version", action="version", version="motion-qc " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="检查文件或目录")
    c.add_argument("targets", nargs="+", help="HTML 文件、compose/2 JSON，或包含它们的目录")
    c.add_argument("--only", help="只跑这几项，逗号分隔：%s" % ",".join(CHECKS))
    c.add_argument("--json", help="机器可读报告；填 - 打到 stdout")
    c.add_argument("--chrome", help="Chrome/Edge 可执行文件路径")
    c.add_argument("--chrome-arg", action="append", default=[], help="额外传给浏览器的参数，可重复")
    c.add_argument("--node", help="node 可执行文件路径")
    c.add_argument("--viewport", type=_viewport, default=None, metavar="WxH",
                   help="强制探针视口。默认自动：优先读文件里 "
                        "<meta name=\"viewport\"> 声明的尺寸，读不到才用 1920x1080。"
                        "视口大小会改变百分比/vw 布局，别随手写死")
    c.add_argument("--stage", action="append", default=None, metavar="SELECTOR",
                   help="根容器选择器，可重复。默认依次试 #stage #root [data-composition-id] main body")
    c.add_argument("--tolerance", type=float, default=0.5, metavar="PX",
                   help="出框容差（px），默认 0.5。放宽到 1 以上会造出假阴性")
    c.add_argument("--settle-from", type=float, default=0.76, metavar="FRAC",
                   help="落定期起点（时长占比），默认 0.76")
    c.add_argument("--settle", type=int, default=4, metavar="N", help="落定期采样点数，默认 4")
    c.add_argument("--min-duration", type=float, default=1.2, metavar="S", help="时长下限，默认 1.2s")
    c.add_argument("--max-duration", type=float, default=60.0, metavar="S", help="时长上限，默认 60s")
    c.add_argument("--max-frames", type=int, default=2, metavar="N",
                   help="画面状态数不超过 N 就判为几乎静止，默认 2")
    c.add_argument("--jobs", type=int, default=min(4, (os.cpu_count() or 2)), metavar="N",
                   help="并行度，默认 4")
    c.add_argument("--tmp", help="临时目录，默认 ~/.cache/motion-qc 或 $MOTION_QC_TMP")
    c.add_argument("--keep-tmp", action="store_true", help="保留临时宿主页（排查用）")
    c.add_argument("-v", "--verbose", action="store_true", help="展开每条发现的明细")
    c.add_argument("-q", "--quiet", action="store_true", help="不打印终端报告（配合 --json 用）")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("selftest", help="拿仓库自带的已知好/坏样本验证工具本身没坏")
    s.add_argument("--keep-tmp", action="store_true")
    s.set_defaults(func=cmd_selftest)

    d = sub.add_parser("doctor", help="检查这台机器缺什么")
    d.add_argument("--chrome")
    d.add_argument("--chrome-arg", action="append", default=[])
    d.add_argument("--node")
    d.add_argument("--viewport", type=_viewport, default=None)
    d.add_argument("--stage", action="append", default=None)
    d.add_argument("--tolerance", type=float, default=0.5)
    d.add_argument("--settle-from", type=float, default=0.76)
    d.add_argument("--settle", type=int, default=4)
    d.add_argument("--min-duration", type=float, default=1.2)
    d.add_argument("--max-duration", type=float, default=60.0)
    d.add_argument("--max-frames", type=int, default=2)
    d.add_argument("--jobs", type=int, default=1)
    d.add_argument("--tmp")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("explain", help="讲清某一项到底在判什么")
    e.add_argument("check", help=" | ".join(CHECKS))
    e.set_defaults(func=cmd_explain)
    return p


def _viewport(text: str):
    try:
        w, _, h = text.lower().partition("x")
        return (int(w), int(h))
    except ValueError:
        raise argparse.ArgumentTypeError("视口要写成 WxH，比如 1920x1080")


def main(argv=None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        sys.stderr.write("\n中断。\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
