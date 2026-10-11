"""检查项的公共骨架：一次检查 = 一份发现列表 + 一个状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"
ERROR = "error"

_SEVERITY = {PASS: 0, WARN: 1, SKIP: 2, ERROR: 3, FAIL: 4}


@dataclass
class Finding:
    """一条具体的、可直接贴到工单里的问题。"""

    code: str
    message: str
    severity: str = FAIL
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: str = PASS
    findings: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if _SEVERITY[finding.severity] > _SEVERITY[self.status]:
            self.status = finding.severity

    def to_dict(self) -> dict:
        return {
            "id": self.check_id,
            "title": self.title,
            "status": self.status,
            "info": self.info,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class FileReport:
    path: str
    checks: list = field(default_factory=list)

    @property
    def status(self) -> str:
        worst = PASS
        for c in self.checks:
            if _SEVERITY[c.status] > _SEVERITY[worst]:
                worst = c.status
        return worst

    def to_dict(self) -> dict:
        return {
            "file": self.path,
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
        }


def display_path(p: Path, base: Path | None = None) -> str:
    """报告里用相对路径，短一些；换算不了就用绝对路径。"""
    if base is not None:
        try:
            return str(Path(p).resolve().relative_to(Path(base).resolve()))
        except (ValueError, OSError):
            pass
    return str(p)
