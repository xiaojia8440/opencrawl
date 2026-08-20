"""父子任务模块 — 列表→详情 爬取模式

支持功能:
- 列表页自动提取详情链接
- 深度爬取（列表→详情→子页面）
- 详情页字段映射配置
- 支持翻页+详情爬取联动

用法:
    crawler = ListDetailCrawler(config, fetcher, parser)
    results = crawler.crawl_list(
        list_url="https://example.com/list",
        detail_selector="a.detail-link",  # CSS选择器
        max_details=20,  # 最多爬取多少个详情页
        fields={"title": "h1", "content": ".article-body"},  # 详情页字段映射
    )
"""

import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ListDetailCrawler:
    """列表→详情 爬取器（父子任务）"""

    def __init__(self, config: dict, fetcher=None, parser=None):
        self.config = config
        self.fetcher = fetcher
        self.parser = parser
        ld_cfg = config.get("list_detail", {})
        self.default_detail_selector: str = ld_cfg.get("detail_selector", "a")
        self.default_max_details: int = ld_cfg.get("max_details", 20)
        self.detail_delay: float = ld_cfg.get("detail_delay", 1.0)
        self.extract_patterns: List[str] = ld_cfg.get("url_patterns", [])
        # URL 过滤正则（只保留匹配的详情链接）
        self.url_filters: List[str] = ld_cfg.get("url_filters", [])

    def extract_detail_links(self, html: str, base_url: str,
                             selector: str = None,
                             patterns: List[str] = None) -> List[str]:
        """从列表页HTML中提取详情链接

        Args:
            html: 列表页HTML
            base_url: 列表页URL（用于相对路径转绝对路径）
            selector: CSS选择器，如 "a.detail-link" 或 ".list-item a"
            patterns: URL正则过滤模式列表，如 [r"/article/\\d+", r"/detail/"]

        Returns:
            去重后的详情链接列表
        """
        selector = selector or self.default_detail_selector
        patterns = patterns or self.extract_patterns

        soup = BeautifulSoup(html, "lxml")
        links = []

        # 用CSS选择器提取
        elements = soup.select(selector)
        for el in elements:
            href = el.get("href", "")
            if href:
                abs_url = urljoin(base_url, href)
                links.append(abs_url)

        # 如果选择器没匹配到，退化到提取所有链接
        if not links:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if href and not href.startswith(("javascript:", "mailto:", "#")):
                    abs_url = urljoin(base_url, href)
                    links.append(abs_url)

        # URL正则过滤
        if patterns:
            filtered = []
            for url in links:
                for pattern in patterns:
                    if re.search(pattern, url):
                        filtered.append(url)
                        break
            links = filtered

        # URL过滤器（排除模式）
        if self.url_filters:
            links = [url for url in links
                     if not any(re.search(p, url) for p in self.url_filters)]

        # 去重（保持顺序）
        seen = set()
        unique_links = []
        for url in links:
            if url not in seen:
                seen.add(url)
                unique_links.append(url)

        logger.info(f"📋 列表页提取到 {len(unique_links)} 个详情链接（共 {len(links)} 个，去重 {len(links) - len(unique_links)} 个）")
        return unique_links

    def crawl_list(self, list_url: str,
                   detail_selector: str = None,
                   max_details: int = None,
                   fields: Dict[str, str] = None,
                   url_patterns: List[str] = None) -> List[Dict]:
        """爬取列表页+详情页（完整父子任务流程）

        Args:
            list_url: 列表页URL
            detail_selector: 详情链接CSS选择器
            max_details: 最多爬取详情页数量
            fields: 详情页字段映射 {字段名: CSS选择器}
            url_patterns: URL正则过滤模式

        Returns:
            详情页解析结果列表
        """
        if not self.fetcher:
            logger.error("❌ 父子任务需要 Fetcher 实例")
            return []

        max_details = max_details or self.default_max_details

        # Step 1: 爬取列表页
        logger.info(f"📋 [父子任务] 开始爬取列表页: {list_url}")
        list_html = self.fetcher.fetch(list_url)
        if not list_html:
            logger.error(f"❌ 列表页爬取失败: {list_url}")
            return []

        # Step 2: 提取详情链接
        detail_links = self.extract_detail_links(list_html, list_url, detail_selector, url_patterns)
        if not detail_links:
            logger.warning("⚠️ 列表页未提取到任何详情链接")
            return []

        # 限制数量
        if max_details > 0 and len(detail_links) > max_details:
            detail_links = detail_links[:max_details]
            logger.info(f"📋 限制为前 {max_details} 个详情链接")

        # Step 3: 逐个爬取详情页
        results = []
        for i, detail_url in enumerate(detail_links, 1):
            logger.info(f"📄 [详情 {i}/{len(detail_links)}] {detail_url}")

            detail_html = self.fetcher.fetch(detail_url, delay=self.detail_delay)
            if not detail_html:
                logger.warning(f"⚠️ 详情页爬取失败: {detail_url}")
                continue

            if self.parser:
                parsed = self.parser.parse(detail_html, detail_url)
            else:
                parsed = {"url": detail_url, "html": detail_html}

            # 字段提取（如果有字段映射）
            if fields:
                soup = BeautifulSoup(detail_html, "lxml")
                extracted = {}
                for field_name, css_selector in fields.items():
                    el = soup.select_one(css_selector)
                    extracted[field_name] = el.get_text(strip=True) if el else ""
                parsed["extracted_fields"] = extracted

            results.append(parsed)

        logger.info(f"✅ [父子任务] 完成: 列表1页 + 详情{len(results)}/{len(detail_links)} 页")
        return results

    def crawl_multi_list(self, list_urls: List[str],
                         detail_selector: str = None,
                         max_details: int = None,
                         fields: Dict[str, str] = None) -> Dict[str, List[Dict]]:
        """爬取多个列表页+详情页

        Returns:
            {列表URL: [详情结果列表]}
        """
        all_results = {}
        for list_url in list_urls:
            results = self.crawl_list(list_url, detail_selector, max_details, fields)
            all_results[list_url] = results
        return all_results
