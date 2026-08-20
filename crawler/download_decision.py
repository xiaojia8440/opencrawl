"""
下载决策树

统一下载链路的 URL 分类门禁。下载器必须根据 classify_url() 的结果决定行为，
不能看到 HTML 就无差别启动跳转跟踪。

分类:
- direct_file     直链文件
- html_middle     确认或疑似下载中间页
- netdisk_share   网盘分享页
- download_page   下载列表/导航页
- blocked         不相关/黑名单/应跳过
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from crawler.version_parser import parse_python_version, VersionInfo


# 默认识别为文件的后缀
DEFAULT_FILE_EXTS: Set[str] = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".apk", ".exe", ".msi", ".iso", ".pdf", ".docx",
    ".xlsx", ".pptx", ".epub", ".mp4", ".mkv", ".avi",
    ".mov", ".mp3", ".flac", ".wav",
}

# 网盘分享页特征
_NETDISK_PATTERNS = [
    # Cloudreve 分享页
    (re.compile(r"^cloudreve://", re.IGNORECASE), "cloudreve_scheme"),
    (re.compile(r"/s/[a-zA-Z0-9]{3,}", re.IGNORECASE), "share_path"),
]

# 已知网盘域名
_NETDISK_DOMAINS = {
    "mypikpak.com": "pikpak",
    "www.mypikpak.com": "pikpak",
    "pan.baidu.com": "baidu",
    "www.aliyundrive.com": "aliyun",
    "aliyundrive.com": "aliyun",
    "www.alipan.com": "aliyun",
    "alipan.com": "aliyun",
    "lanzou.com": "lanzou",
    "lanzoui.com": "lanzou",
    "lanzoux.com": "lanzou",
    "lanzoup.com": "lanzou",
    "cloud.189.cn": "tianyi",
    "www.ilanzou.com": "lanzou",
}

# 下载中间页/下载接口的路径特征
_DOWNLOAD_PATH_RE = re.compile(
    r"/(download|dl|file|attachment|down|getfile|fetch)/",
    re.IGNORECASE,
)
_DOWNLOAD_QUERY_RE = re.compile(
    r"[?&](action|act|mod)=(download|getfile|get_file|dl)",
    re.IGNORECASE,
)

# 明显不是下载目标的页面（文章/新闻/文档/代码仓库等）
_BLOCKED_PATH_RE = re.compile(
    r"/(news|article|blog|post|posts|about|author|category|tag|tags|"
    r"wiki|doc|docs|documentation|manual|help|support|forum|bbs|"
    r"issues?|pulls?|commit|commits|tree|blob|src|source|misc)/?",
    re.IGNORECASE,
)

# Python Windows 安装包精确匹配
# 无架构后缀的旧版安装包（python-3.8.8.exe = 32位包，必须拦截）
_PY_NOARCH_INSTALLER_RE = re.compile(
    r"python-\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?\.exe$",
    re.IGNORECASE,
)
_PY_AMD64_INSTALLER_RE = re.compile(
    r"python-\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?-amd64\.exe$",
    re.IGNORECASE,
)
_PY_OTHER_INSTALLER_RE = re.compile(
    r"python-\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?"
    r"-(?:arm64|win32|webinstall|embed-amd64|embed-win32|pdb-amd64|pdb|amd64-pdb)"
    r"\.(?:exe|zip)$",
    re.IGNORECASE,
)
# 历史版本目录页（如 /downloads/release/python-326/）
_PY_VERSION_DIR_RE = re.compile(
    r"/python-\d+/?$",
    re.IGNORECASE,
)


@dataclass
class ClassifyResult:
    """URL 分类结果"""
    category: str                 # direct_file / html_middle / netdisk_share / download_page / blocked
    confidence: float = 0.0       # 0.0 ~ 1.0
    reason: str = ""
    metadata: Dict = field(default_factory=dict)

    def __bool__(self):
        return True


class DownloadDecisionTree:
    """
    下载决策树。

    可通过 config 控制:
      downloader.strict_gate: bool        高置信度判断是否强制服从
      downloader.cross_domain: str        same_domain / allow_list / allow_all
      downloader.cross_domain_allow: list 跨域白名单
      downloader.allowed_file_exts: list  额外允许的文件后缀
      downloader.blocked_domains: list    额外黑名单域名
    """

    def __init__(self, config: dict = None, base_url: str = ""):
        self.config = (config or {}).get("downloader", {}) or {}
        # 同时兼容旧的 file_download 段
        self._legacy_cfg = (config or {}).get("file_download", {}) or {}

        self.strict_gate: bool = self.config.get(
            "strict_gate",
            self._legacy_cfg.get("strict_gate", False),
        )
        self.max_redirect_depth: int = self.config.get(
            "max_redirect_depth",
            self._legacy_cfg.get("max_redirect_depth", 5),
        )
        self.cross_domain: str = self.config.get(
            "cross_domain",
            self._legacy_cfg.get("cross_domain", "allow_all"),
        )
        self.cross_domain_allow: List[str] = [
            d.lower().lstrip(".")
            for d in self.config.get(
                "cross_domain_allow",
                self._legacy_cfg.get("cross_domain_allow", []),
            )
        ]
        self.blocked_domains: List[str] = [
            d.lower() for d in self.config.get("blocked_domains", [])
        ]

        extra_exts = self.config.get("allowed_file_exts") or self._legacy_cfg.get("allowed_file_exts") or []
        self.file_exts: Set[str] = set(DEFAULT_FILE_EXTS)
        for ext in extra_exts:
            ext = ext.lower()
            if not ext.startswith("."):
                ext = "." + ext
            self.file_exts.add(ext)

        self.base_domain = ""
        if base_url:
            try:
                self.base_domain = urlparse(base_url).netloc.lower()
            except Exception:
                self.base_domain = ""

    # ── 公开接口 ──────────────────────────────────────────

    def set_base_url(self, url: str):
        """设置入口域名，用于跨域判断"""
        try:
            self.base_domain = urlparse(url).netloc.lower()
        except Exception:
            pass

    def is_domain_allowed(self, url: str) -> bool:
        """判断 URL 是否符合跨域策略"""
        try:
            target_domain = urlparse(url).netloc.lower()
        except Exception:
            return False

        if not target_domain:
            return False

        if target_domain in self.blocked_domains or any(
            target_domain.endswith("." + d) for d in self.blocked_domains
        ):
            return False

        if self.cross_domain == "same_domain":
            if not self.base_domain:
                return True  # 未设置入口域名时不阻拦
            return target_domain == self.base_domain or target_domain.endswith(
                "." + self.base_domain
            )

        if self.cross_domain == "allow_list":
            if not self.cross_domain_allow:
                return False
            return any(
                target_domain == allowed or target_domain.endswith("." + allowed)
                for allowed in self.cross_domain_allow
            )

        # allow_all
        return True

    def classify_url(self, url: str, context: str = "") -> ClassifyResult:
        """
        分类一个 URL。

        Args:
            url: 待分类的 URL
            context: 可选上下文，例如链接文本、标题、页面片段，用于辅助判断
        """
        if not url or not isinstance(url, str):
            return ClassifyResult("blocked", 0.9, "空URL")

        url = url.strip()
        try:
            parsed = urlparse(url)
        except Exception:
            return ClassifyResult("blocked", 0.6, "URL解析失败")

        scheme = (parsed.scheme or "").lower()
        netloc = (parsed.netloc or "").lower()
        path = parsed.path or ""
        path_lower = path.lower()
        ext = os.path.splitext(path_lower)[1]

        # javascript / mailto / anchor 等
        if scheme in ("javascript", "mailto", "tel", "data"):
            return ClassifyResult("blocked", 0.95, f"非HTTP协议: {scheme}")
        if url.startswith("#"):
            return ClassifyResult("blocked", 0.95, "页内锚点")

        # 自定义 cloudreve:// 协议
        if url.lower().startswith("cloudreve://"):
            return ClassifyResult(
                "netdisk_share", 0.95, "cloudreve:// 协议",
                {"provider": "cloudreve"},
            )

        # 网盘域名
        for domain, provider in _NETDISK_DOMAINS.items():
            if netloc == domain or netloc.endswith("." + domain):
                return ClassifyResult(
                    "netdisk_share", 0.9,
                    f"网盘域名: {provider}",
                    {"provider": provider},
                )

        # 网盘分享路径
        for pattern, name in _NETDISK_PATTERNS:
            if pattern.search(path):
                return ClassifyResult(
                    "netdisk_share", 0.8,
                    f"网盘分享路径特征: {name}",
                )

        # 域名黑名单
        for bad in self.blocked_domains:
            if netloc == bad or netloc.endswith("." + bad):
                return ClassifyResult("blocked", 0.95, f"黑名单域名: {bad}")

        # ── Python 下载站精确过滤 ──
        is_python_org = netloc in (
            "python.org", "www.python.org",
            "downloads.python.org", "docs.python.org",
        )
        if "python.org" in netloc:
            py_result = self._classify_python_url(url, path_lower, ext)
            if py_result:
                return py_result
            # python.org 上不是目标安装包的页面，高置信度跳过
            if is_python_org or netloc.endswith(".python.org"):
                if ext and ext not in self.file_exts:
                    return ClassifyResult(
                        "blocked", 0.85,
                        "python.org 非目标文件类型",
                    )

        # 直链文件
        if ext in self.file_exts:
            return self._classify_direct_file(url, path_lower, ext)

        # 下载接口/下载中间页路径特征
        if _DOWNLOAD_PATH_RE.search(path) or _DOWNLOAD_QUERY_RE.search(url):
            return ClassifyResult(
                "html_middle", 0.8,
                "路径/参数含下载接口特征",
            )

        # 明显无关页面
        if _BLOCKED_PATH_RE.search(path):
            return ClassifyResult(
                "blocked", 0.8,
                "非下载类页面路径(文档/新闻/代码等)",
            )

        # 下载列表页：常见的 downloads 列表
        if re.search(r"/downloads?/?$", path_lower) or path_lower == "/download":
            return ClassifyResult(
                "download_page", 0.6,
                "下载列表页",
            )

        # 看上下文（链接文本）
        if context:
            ctx_lower = context.lower()
            if any(kw in ctx_lower for kw in ("下载", "立即下载", "download", "save")):
                return ClassifyResult(
                    "html_middle", 0.6,
                    "链接文本含下载关键词",
                )

        # 兜底：不确定的 HTML 页面
        return ClassifyResult(
            "html_middle", 0.3,
            "特征不明确，默认作为候选中间页",
        )

    # ── Python 下载站专用分类 ──────────────────────────────

    def _classify_python_url(
        self, url: str, path_lower: str, ext: str,
    ) -> Optional[ClassifyResult]:
        """python.org 下载站的精确分类"""
        # 无架构后缀的 .exe（旧版默认32位包），必须拦截
        if _PY_NOARCH_INSTALLER_RE.search(url):
            ver = parse_python_version(url)
            ver_str = f" {ver}" if ver else ""
            return ClassifyResult(
                "blocked", 0.95,
                f"Python{ver_str}: 无架构后缀.exe(32位旧包)，需-amd64",
            )

        # 目标：最新稳定版 Windows amd64 离线安装程序
        if _PY_AMD64_INSTALLER_RE.search(url):
            ver = parse_python_version(url)
            if ver:
                if ver.is_stable():
                    return ClassifyResult(
                        "direct_file", 0.99,
                        f"Python {ver} Windows amd64 稳定版安装包",
                        {"python_version": str(ver), "arch": "amd64", "stable": True},
                    )
                return ClassifyResult(
                    "blocked", 0.95,
                    f"Python 预发布版 {ver.stage}{ver.stage_num}，非稳定版",
                    {"python_version": str(ver)},
                )
            return ClassifyResult(
                "direct_file", 0.9,
                "Python amd64.exe 安装包（版本解析失败）",
            )

        # arm64 / win32 / webinstall / embed / pdb
        if _PY_OTHER_INSTALLER_RE.search(url):
            ver = parse_python_version(url)
            ver_str = f" {ver}" if ver else ""
            reason = "非目标安装包"
            if "webinstall" in url.lower():
                reason = "webinstall 在线安装stub"
            elif "embed" in url.lower():
                reason = "embed 嵌入式包"
            elif "arm64" in url.lower():
                reason = "ARM64 架构包"
            elif "win32" in url.lower():
                reason = "32位安装包"
            elif "pdb" in url.lower():
                reason = "调试符号包"
            return ClassifyResult(
                "blocked", 0.95,
                f"Python{ver_str}: {reason}",
                {"python_version": str(ver) if ver else ""},
            )

        # 历史版本目录页 /release/python-326/
        if _PY_VERSION_DIR_RE.search(url):
            return ClassifyResult(
                "blocked", 0.9,
                "Python 历史版本目录页",
            )

        # hg.python.org / svn.python.org 等代码仓库
        netloc = urlparse(url).netloc.lower()
        if netloc in ("hg.python.org", "svn.python.org", "bugs.python.org"):
            return ClassifyResult(
                "blocked", 0.95,
                f"非下载站点: {netloc}",
            )
        if netloc.endswith(".python.org") and netloc not in (
            "www.python.org", "downloads.python.org",
        ):
            # docs./hg./bugs. 等子域
            return ClassifyResult(
                "blocked", 0.85,
                f"非下载子域: {netloc}",
            )

        # python.org 上的 .txt / .asc / .sig 等签名/文本文件
        if ext in (".txt", ".asc", ".sig", ".md5", ".sha1", ".sha256", ".sha512"):
            return ClassifyResult(
                "blocked", 0.9,
                f"校验/签名文件 {ext}",
            )

        return None

    # ── 直链文件分类 ─────────────────────────────────────

    def _classify_direct_file(
        self, url: str, path_lower: str, ext: str,
    ) -> ClassifyResult:
        # 预发布/调试/嵌入式等明显不是目标的包
        name = os.path.basename(path_lower)

        # 常见的非目标后缀名过滤词
        non_target_markers = [
            "pdb", "debug", "symbols",
            "webinstall", "online", "stub",
            "embed",
            "src", "source", "sources",
            "changelog", "news", "readme", "license",
            "sha256", "md5", "sig", "asc",
        ]
        for marker in non_target_markers:
            # 用 - 或 _ 或 . 分隔，避免误杀
            if re.search(rf"[-_.]{re.escape(marker)}[-_.]", name) or name.startswith(marker + "-"):
                return ClassifyResult(
                    "blocked", 0.8,
                    f"非目标文件(含{marker})",
                    {"ext": ext},
                )

        return ClassifyResult(
            "direct_file", 0.9,
            f"直链文件 {ext}",
            {"ext": ext},
        )


# 兼容性别名
DownloadClassifier = DownloadDecisionTree
