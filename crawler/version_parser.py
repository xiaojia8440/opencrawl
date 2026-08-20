"""
通用版本号解析器

从文件名/URL中解析版本号，支持数值化比较，避免字符串比较的字典序陷阱。
例如 "3.14.7" 与 "3.13.15" 不能直接用字符串比较。

示例:
    python-3.14.7-amd64.exe           → 3.14.7 final
    python-3.15.0a8-amd64.exe         → 3.15.0 alpha 8
    python-3.7.5rc1-embed-amd64.zip   → 3.7.5 rc 1
"""

import re
from dataclasses import dataclass
from typing import Optional


_STAGE_ORDER = {"alpha": 0, "beta": 1, "rc": 2, "final": 3}
_STAGE_CHAR_MAP = {"a": "alpha", "b": "beta", "r": "rc", "rc": "rc"}


@dataclass
class VersionInfo:
    """版本号信息"""
    major: int
    minor: int
    patch: int
    stage: str = "final"       # alpha / beta / rc / final
    stage_num: int = 0         # alpha/beta/rc 的序号
    raw: str = ""

    def is_stable(self) -> bool:
        """是否为正式稳定版"""
        return self.stage == "final"

    def _stage_rank(self) -> int:
        return _STAGE_ORDER.get(self.stage, 0)

    def _tuple(self):
        return (self.major, self.minor, self.patch, self._stage_rank(), self.stage_num)

    def __eq__(self, other):
        if not isinstance(other, VersionInfo):
            return NotImplemented
        return self._tuple() == other._tuple()

    def __lt__(self, other):
        if not isinstance(other, VersionInfo):
            return NotImplemented
        return self._tuple() < other._tuple()

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        if not isinstance(other, VersionInfo):
            return NotImplemented
        return self._tuple() > other._tuple()

    def __ge__(self, other):
        return self == other or self > other

    def __str__(self):
        if self.stage == "final":
            return f"{self.major}.{self.minor}.{self.patch}"
        suffix = {"alpha": "a", "beta": "b", "rc": "rc"}.get(self.stage, "")
        return f"{self.major}.{self.minor}.{self.patch}{suffix}{self.stage_num}"


# Python 版本：python-3.14.7-amd64.exe / python-3.15.0a8-amd64.exe
_PYTHON_VERSION_RE = re.compile(
    r"python-(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?",
    re.IGNORECASE,
)

# 通用 SemVer：name-1.2.3 / name_1.2.3 / name 1.2.3 / v1.2.3
# 预发布后缀：-alpha1 / -beta2 / -rc3 / -a1 / -b2
_GENERIC_VERSION_RE = re.compile(
    r"[vV]?(\d+)\.(\d+)\.(\d+)"
    r"(?:[-_. ]?(alpha|beta|rc|a|b|r)[-_. ]?(\d+))?",
    re.IGNORECASE,
)


def parse_python_version(text: str) -> Optional[VersionInfo]:
    """从 URL/文件名中解析 Python 版本号"""
    if not text:
        return None
    m = _PYTHON_VERSION_RE.search(text)
    if not m:
        return None

    stage_char = (m.group(4) or "").lower()
    stage = _STAGE_CHAR_MAP.get(stage_char, "final")
    stage_num = int(m.group(5)) if m.group(5) else 0

    return VersionInfo(
        major=int(m.group(1)),
        minor=int(m.group(2)),
        patch=int(m.group(3)),
        stage=stage,
        stage_num=stage_num,
        raw=m.group(0),
    )


def parse_version(text: str) -> Optional[VersionInfo]:
    """
    通用版本号解析。优先匹配 python- 前缀，否则按 SemVer 风格解析。

    返回第一个看起来像版本号的结果。调用方如果知道目标软件，
    应优先使用对应的专用解析函数（如 parse_python_version）。
    """
    if not text:
        return None

    # 先尝试 Python 专用模式（更精确）
    py = parse_python_version(text)
    if py:
        return py

    m = _GENERIC_VERSION_RE.search(text)
    if not m:
        return None

    stage_raw = (m.group(4) or "").lower()
    stage_map = {"alpha": "alpha", "a": "alpha", "beta": "beta", "b": "beta", "rc": "rc", "r": "rc"}
    stage = stage_map.get(stage_raw, "final")
    stage_num = int(m.group(5)) if m.group(5) else 0

    return VersionInfo(
        major=int(m.group(1)),
        minor=int(m.group(2)),
        patch=int(m.group(3)),
        stage=stage,
        stage_num=stage_num,
        raw=m.group(0),
    )


def latest_version(urls_or_texts, parser=parse_version):
    """
    从一组 URL/文件名中找出最新版本。

    Args:
        urls_or_texts: URL 或文件名字符串列表
        parser: 版本解析函数，默认 parse_version

    Returns:
        (latest_text, latest_version) 或 (None, None)
    """
    best_text = None
    best_ver = None
    for text in urls_or_texts:
        ver = parser(text)
        if ver and (best_ver is None or ver > best_ver):
            best_ver = ver
            best_text = text
    return best_text, best_ver
