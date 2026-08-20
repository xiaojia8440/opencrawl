"""
智能翻页模块 — 自动识别并处理分页逻辑

支持功能:
- 自动识别"下一页"按钮/链接（常见选择器 + 文本匹配）
- URL 模式翻页（?page=N, /p/N, offset=N 等）
- "加载更多"按钮点击
- 滚动到底部触发加载
- 最大页数/深度控制
- 翻页终止条件检测
"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode, urljoin, urlunparse

logger = logging.getLogger(__name__)


# 常见"下一页"按钮/链接的 CSS 选择器
NEXT_PAGE_SELECTORS = [
    'a.next', 'a.pagination-next', 'a.pager-next',
    '.next a', '.pager a:last-child', '.page-next a',
    '[rel="next"]',
    '.pagination .next', '.pagination-next',
    'li.next > a', 'li.next-page > a',
    '#pagination-next', '.js-pagination-next',
]

# 翻页 URL 参数模式
PAGINATION_PATTERNS = [
    (r'[?&]page=(\d+)', 'page'),
    (r'[?&]p=(\d+)', 'p'),
    (r'[?&]pn=(\d+)', 'pn'),
    (r'[?&]pagenum=(\d+)', 'pagenum'),
    (r'[?&]pageNum=(\d+)', 'pageNum'),
    (r'[?&]pg=(\d+)', 'pg'),
    (r'[?&]offset=(\d+)', 'offset'),
    (r'[?&]start=(\d+)', 'start'),
    (r'[?&]skip=(\d+)', 'skip'),
    (r'/page/(\d+)', 'path_page'),
    (r'/p/(\d+)', 'path_p'),
    (r'-(\d+)\.html?$', 'suffix_page'),
]


class PaginationResult:
    """翻页结果封装"""

    def __init__(self):
        self.pages: List[Dict[str, Any]] = []
        self.total_pages: int = 0
        self.current_page: int = 0
        self.has_next: bool = False
        self.next_urls: List[str] = []
        self.method: str = ""
        self.stopped_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_pages": self.total_pages,
            "current_page": self.current_page,
            "has_next": self.has_next,
            "method": self.method,
            "stopped_reason": self.stopped_reason,
            "pages_count": len(self.pages),
        }


class Paginator:
    """
    智能翻页管理器

    Args:
        config: 配置字典
        fetcher: Fetcher 实例（用于获取页面）
    """

    def __init__(self, config: dict, fetcher: Any = None):
        self.config = config
        self.fetcher = fetcher

        paginator_cfg = config.get("paginator", {})
        self.max_pages: int = paginator_cfg.get("max_pages", 20)
        self.delay: float = paginator_cfg.get("delay", 1.0)
        self.method: str = paginator_cfg.get("method", "auto")
        self.next_selector: Optional[str] = paginator_cfg.get("next_selector", None)
        self.load_more_selector: Optional[str] = paginator_cfg.get("load_more_selector", None)
        self.scroll_pause: float = paginator_cfg.get("scroll_pause", 1.5)
        self.max_scrolls: int = paginator_cfg.get("max_scrolls", 20)

        url_pattern_cfg = paginator_cfg.get("url_pattern", {})
        self.page_param: Optional[str] = url_pattern_cfg.get("param", None)
        self.page_start: int = url_pattern_cfg.get("start", 1)
        self.page_size: int = url_pattern_cfg.get("page_size", 0)
        self.url_template: Optional[str] = url_pattern_cfg.get("template", None)

        stop_cfg = paginator_cfg.get("stop", {})
        self.stop_on_empty: bool = stop_cfg.get("on_empty", True)
        self.stop_on_duplicate: bool = stop_cfg.get("on_duplicate", True)
        self.stop_on_status: bool = stop_cfg.get("on_status_code", True)
        self.max_consecutive_empty: int = stop_cfg.get("max_consecutive_empty", 2)

        self._seen_urls: Set[str] = set()
        self._consecutive_empty: int = 0
        self._total_pages_fetched: int = 0

    def detect_pagination(self, html: str, url: str) -> Dict[str, Any]:
        """分析页面，检测分页方式和下一页 URL"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        result = {
            "method": "none",
            "next_url": None,
            "pagination_urls": [],
            "page_number": 1,
            "total_pages": 0,
        }

        url_info = self._detect_url_pattern(url)
        if url_info:
            result["method"] = "url_pattern"
            result["page_number"] = url_info["current_page"]
            next_url = self._generate_next_url(url, url_info)
            if next_url:
                result["next_url"] = next_url

        next_link = soup.find("link", rel="next")
        if next_link and next_link.get("href"):
            result["method"] = "link_rel"
            result["next_url"] = urljoin(url, next_link["href"])

        if result["method"] == "none":
            next_url = self._find_next_link(soup, url)
            if next_url:
                result["method"] = "selector"
                result["next_url"] = next_url

        pagination_urls = self._collect_pagination_urls(soup, url)
        result["pagination_urls"] = pagination_urls

        total = self._detect_total_pages(soup)
        result["total_pages"] = total

        return result

    def paginate_urls(
        self,
        start_url: str,
        max_pages: Optional[int] = None,
    ) -> List[str]:
        """基于 URL 模式生成分页 URL 列表"""
        limit = max_pages or self.max_pages
        urls = []

        url_info = self._detect_url_pattern(start_url)
        if not url_info:
            logger.warning(f"⚠️ 无法识别翻页模式: {start_url}")
            return [start_url]

        param_name = url_info["param"]
        current = url_info["current_page"]
        is_offset_based = param_name in ("offset", "start", "skip")
        step = url_info.get("page_size", 1) if is_offset_based else 1

        for i in range(limit):
            if i == 0:
                urls.append(start_url)
            else:
                if is_offset_based:
                    page_val = current + (i * step)
                else:
                    page_val = current + i
                page_url = self._set_url_param(start_url, param_name, page_val, url_info)
                urls.append(page_url)

        logger.info(f"📑 生成 {len(urls)} 个分页 URL（模式: {param_name}）")
        return urls

    def fetch_all_pages(
        self,
        start_url: str,
        callback: Optional[Callable] = None,
        max_pages: Optional[int] = None,
    ) -> PaginationResult:
        """同步翻页爬取"""
        limit = max_pages or self.max_pages
        result = PaginationResult()
        self._seen_urls.clear()
        self._consecutive_empty = 0
        self._total_pages_fetched = 0

        method = self.method
        if method == "auto" and self.fetcher:
            html = self.fetcher.fetch(start_url, delay=0)
            if html:
                detection = self.detect_pagination(html, start_url)
                method = detection.get("method", "url_pattern")

                if callback:
                    should_continue = callback(html, start_url, 1)
                    if should_continue is False:
                        result.pages.append({"url": start_url, "html": html})
                        result.total_pages = 1
                        result.stopped_reason = "callback_stopped"
                        return result

                result.pages.append({"url": start_url, "html": html})
                self._seen_urls.add(start_url)
                self._total_pages_fetched = 1

                if method == "url_pattern":
                    next_url = detection.get("next_url")
                    if next_url:
                        self._paginate_by_url_pattern(next_url, result, callback, limit)
                elif method in ("selector", "link_rel"):
                    self._paginate_by_next_link(detection.get("next_url"), result, callback, limit)

        elif method == "url_pattern":
            urls = self.paginate_urls(start_url, limit)
            for i, page_url in enumerate(urls):
                if self._total_pages_fetched >= limit:
                    result.stopped_reason = "max_pages_reached"
                    break

                if self.fetcher:
                    if i > 0:
                        time.sleep(self.delay)
                    html = self.fetcher.fetch(page_url, delay=0)
                    if html is None:
                        result.stopped_reason = "fetch_failed"
                        break
                    if not html.strip():
                        self._consecutive_empty += 1
                        if self._consecutive_empty >= self.max_consecutive_empty:
                            result.stopped_reason = "consecutive_empty"
                            break
                        continue
                    self._consecutive_empty = 0

                    result.pages.append({"url": page_url, "html": html})
                    self._total_pages_fetched += 1
                    result.current_page = i + 1

                    if callback:
                        should_continue = callback(html, page_url, i + 1)
                        if should_continue is False:
                            result.stopped_reason = "callback_stopped"
                            break

        result.total_pages = len(result.pages)
        result.has_next = result.stopped_reason == "max_pages_reached" or result.stopped_reason == ""
        result.method = method
        logger.info(f"📑 翻页完成: 共 {result.total_pages} 页, 方式: {method}")
        return result

    # ─── 内部方法 ───

    def _detect_url_pattern(self, url: str) -> Optional[Dict[str, Any]]:
        """检测 URL 中的翻页参数模式"""
        for pattern, param_name in PAGINATION_PATTERNS:
            match = re.search(pattern, url)
            if match:
                current_page = int(match.group(1))
                return {
                    "param": param_name,
                    "current_page": current_page,
                    "match": match.group(0),
                    "page_size": 0,
                }

        if self.url_template:
            return {
                "param": self.page_param or "page",
                "current_page": self.page_start,
                "match": "",
                "template": self.url_template,
            }

        return None

    def _generate_next_url(self, current_url: str, url_info: Dict[str, Any]) -> Optional[str]:
        """根据 URL 模式生成下一页 URL"""
        param_name = url_info["param"]
        current_page = url_info["current_page"]

        if param_name in ("offset", "start", "skip"):
            step = self.page_size or 10
            next_val = current_page + step
        else:
            next_val = current_page + 1

        return self._set_url_param(current_url, param_name, next_val, url_info)

    def _set_url_param(self, url: str, param_name: str, value: int, url_info: Dict[str, Any]) -> str:
        """设置 URL 中的分页参数"""
        if param_name.startswith("path_"):
            if "template" in url_info:
                return url_info["template"].format(page=value)
            pattern = url_info.get("match", "")
            if pattern:
                if param_name == "path_page":
                    return url.replace(pattern, f"/page/{value}")
                elif param_name == "path_p":
                    return url.replace(pattern, f"/p/{value}")

        if param_name == "suffix_page":
            pattern = url_info.get("match", "")
            if pattern:
                return re.sub(r'-\d+(\.html?)$', f'-{value}\\1', url)

        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        actual_param = param_name
        for key in params:
            if key.lower() == param_name.lower():
                actual_param = key
                break

        params[actual_param] = [str(value)]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _find_next_link(self, soup: Any, base_url: str) -> Optional[str]:
        """在页面中查找下一页链接"""
        next_link = soup.find("a", rel="next")
        if next_link and next_link.get("href"):
            return urljoin(base_url, next_link["href"])

        for selector in NEXT_PAGE_SELECTORS:
            try:
                clean_selector = selector.split(":has-text")[0].split(":has")[0]
                if not clean_selector or clean_selector == "a" or clean_selector == "button":
                    continue

                elements = soup.select(clean_selector)
                for el in elements:
                    href = el.get("href")
                    if href and not href.startswith("#") and not href.startswith("javascript:"):
                        return urljoin(base_url, href)
            except Exception:
                continue

        next_texts = ["下一页", "下一頁", "next", "Next", "NEXT", "更多", "加载更多"]
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(strip=True).lower()
            if any(nt.lower() in text for nt in next_texts):
                href = a_tag["href"]
                if href and not href.startswith("#") and not href.startswith("javascript:"):
                    return urljoin(base_url, href)

        return None

    def _collect_pagination_urls(self, soup: Any, base_url: str) -> List[str]:
        """收集页面中所有分页链接"""
        urls: Set[str] = set()

        pagination_selectors = [
            ".pagination", ".pager", ".page-nav", ".paginator",
            "#pagination", ".page-numbers", ".pagination-wrap",
        ]

        for selector in pagination_selectors:
            container = soup.select_one(selector)
            if container:
                for a_tag in container.find_all("a", href=True):
                    href = a_tag["href"]
                    if href and not href.startswith("#") and not href.startswith("javascript:"):
                        urls.add(urljoin(base_url, href))

        return list(urls)

    def _detect_total_pages(self, soup: Any) -> int:
        """尝试检测总页数"""
        pagination_selectors = [
            ".pagination", ".pager", ".page-nav", ".paginator",
            "#pagination", ".page-numbers",
        ]

        max_page = 0
        for selector in pagination_selectors:
            container = soup.select_one(selector)
            if container:
                for a_tag in container.find_all("a", href=True):
                    text = a_tag.get_text(strip=True)
                    nums = re.findall(r'\d+', text)
                    for num in nums:
                        n = int(num)
                        if 1 < n < 10000:
                            max_page = max(max_page, n)

                    href = a_tag.get("href", "")
                    for pattern, _ in PAGINATION_PATTERNS:
                        match = re.search(pattern, href)
                        if match:
                            n = int(match.group(1))
                            if 1 < n < 10000:
                                max_page = max(max_page, n)

        return max_page

    def _paginate_by_url_pattern(self, next_url, result, callback, limit):
        """按 URL 模式翻页"""
        current_url = next_url
        page_num = 2

        while self._total_pages_fetched < limit:
            if current_url in self._seen_urls:
                result.stopped_reason = "duplicate_url"
                break

            time.sleep(self.delay)
            html = self.fetcher.fetch(current_url, delay=0) if self.fetcher else None

            if html is None:
                result.stopped_reason = "fetch_failed"
                break

            if not html.strip():
                self._consecutive_empty += 1
                if self.stop_on_empty and self._consecutive_empty >= self.max_consecutive_empty:
                    result.stopped_reason = "consecutive_empty"
                    break
                page_num += 1
                url_info = self._detect_url_pattern(current_url)
                if url_info:
                    current_url = self._generate_next_url(current_url, url_info) or ""
                continue

            self._consecutive_empty = 0
            self._seen_urls.add(current_url)
            result.pages.append({"url": current_url, "html": html})
            result.next_urls.append(current_url)
            self._total_pages_fetched += 1
            result.current_page = page_num

            if callback:
                should_continue = callback(html, current_url, page_num)
                if should_continue is False:
                    result.stopped_reason = "callback_stopped"
                    break

            url_info = self._detect_url_pattern(current_url)
            if url_info:
                current_url = self._generate_next_url(current_url, url_info) or ""
                if not current_url:
                    result.stopped_reason = "no_more_pages"
                    break
            else:
                result.stopped_reason = "pattern_lost"
                break

            page_num += 1

    def _paginate_by_next_link(self, next_url, result, callback, limit):
        """按下一页链接翻页"""
        from bs4 import BeautifulSoup

        current_url = next_url
        page_num = 2

        while current_url and self._total_pages_fetched < limit:
            if current_url in self._seen_urls:
                result.stopped_reason = "duplicate_url"
                break

            time.sleep(self.delay)
            html = self.fetcher.fetch(current_url, delay=0) if self.fetcher else None

            if html is None:
                result.stopped_reason = "fetch_failed"
                break

            if not html.strip():
                self._consecutive_empty += 1
                if self.stop_on_empty and self._consecutive_empty >= self.max_consecutive_empty:
                    result.stopped_reason = "consecutive_empty"
                    break
                continue

            self._consecutive_empty = 0
            self._seen_urls.add(current_url)
            result.pages.append({"url": current_url, "html": html})
            result.next_urls.append(current_url)
            self._total_pages_fetched += 1
            result.current_page = page_num

            if callback:
                should_continue = callback(html, current_url, page_num)
                if should_continue is False:
                    result.stopped_reason = "callback_stopped"
                    break

            soup = BeautifulSoup(html, "lxml")
            next_link = self._find_next_link(soup, current_url)
            if not next_link:
                result.stopped_reason = "no_next_link"
                break

            current_url = next_link
            page_num += 1
