"""页面解析模块 — HTML 解析、链接提取、结构化内容提取、Schema.org/OG/表格/评论/视频提取"""

import json
import re
import logging
import traceback
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


class Parser:
    """HTML 解析器，支持通用内容提取和增强结构化数据提取"""

    def __init__(self, config: dict):
        self.config = config
        parser_cfg = config.get("parser", {})

        self._content_tags = [
            "article", "main", "[role=main]", ".post-content",
            ".entry-content", ".article-content", ".content",
            "#content", "#article", ".post", ".entry",
            # 政府/企业/CMS网站常见选择器
            ".pages_content", ".TRS_Editor", ".article_con",
            ".Custom_UnionStyle", ".pages_editor", "#zoom",
            ".article-body", ".news_content", ".text",
            ".rich_media_content", "#js_content",
        ]

        self.extract_schema: bool = parser_cfg.get("extract_schema", True)
        self.extract_og: bool = parser_cfg.get("extract_og", True)
        self.extract_tables: bool = parser_cfg.get("extract_tables", True)
        self.extract_comments: bool = parser_cfg.get("extract_comments", True)
        self.extract_pagination: bool = parser_cfg.get("extract_pagination", True)
        self.extract_srcset: bool = parser_cfg.get("extract_srcset", True)
        self.extract_videos: bool = parser_cfg.get("extract_videos", True)
        self.max_chars: int = parser_cfg.get("max_chars", 0)  # 0 = 不限制
        self.strip_quotes: bool = parser_cfg.get("strip_quotes", False)

    def parse(self, html: str, url: str) -> Dict:
        """解析 HTML，提取结构化内容"""
        # 保留原始 HTML 字符串，因为 _extract_text 会 decompose 修改 soup
        raw_html = html
        soup = BeautifulSoup(html, "lxml")
        base_url = self._get_base_url(soup, url)

        result = {
            "url": url,
            "title": self._extract_title(soup),
            "text": self._extract_text(raw_html, url),
            "html": str(self._extract_main_content(soup)),
            "links": self._extract_links(soup, base_url),
            "images": self._extract_images(soup, base_url),
            "metadata": self._extract_metadata(soup),
            "headings": self._extract_headings(soup),
        }

        if self.extract_schema:
            result["schema_org"] = self._extract_schema_org(soup)
        if self.extract_og:
            result["open_graph"] = self._extract_open_graph(soup)
            result["twitter_card"] = self._extract_twitter_card(soup)
        if self.extract_tables:
            result["tables"] = self._extract_tables(soup)
        if self.extract_comments:
            result["comments"] = self._extract_comments(soup)
        if self.extract_pagination:
            result["pagination"] = self._extract_pagination_links(soup, base_url)
        if self.extract_srcset:
            result["srcset_images"] = self._extract_srcset(soup, base_url)
        if self.extract_videos:
            result["videos"] = self._extract_videos(soup, base_url)

        return result

    def parse_batch(self, html_pages: Dict[str, str]) -> List[Dict]:
        """批量解析多个页面"""
        results = []
        for url, html in html_pages.items():
            if html is None:
                continue
            try:
                result = self.parse(html, url)
                results.append(result)
            except Exception as e:
                logger.error(f"解析失败: {url} - {e}\n{traceback.format_exc()}")
        return results

    def quick_extract(self, html: str, xpath_map: Dict[str, str],
                      base_url: str = "") -> Dict[str, List[str]]:
        """快速批量 XPath 提取

        不逐个操作浏览器，而是把整个 HTML 一次性用 lxml 解析，
        然后对所有 XPath 批量提取，速度比逐个操作快数倍。

        Args:
            html: HTML 字符串
            xpath_map: {字段名: XPath表达式} 如 {"title": "//h1/text()", "price": "//span[@class='price']/text()"}
            base_url: 基础 URL（用于相对路径转绝对路径）

        Returns:
            {字段名: [匹配值列表]}
        """
        try:
            from lxml import etree
        except ImportError:
            logger.warning("lxml 未安装，快速提取降级为 BeautifulSoup")
            return self._quick_extract_bs(html, xpath_map, base_url)

        try:
            tree = etree.HTML(html)
            if tree is None:
                return {k: [] for k in xpath_map}

            result = {}
            for field, xpath_expr in xpath_map.items():
                try:
                    nodes = tree.xpath(xpath_expr)
                    values = []
                    for node in nodes:
                        if isinstance(node, str):
                            values.append(node.strip())
                        elif hasattr(node, "text"):
                            values.append((node.text or "").strip())
                        else:
                            values.append(str(node).strip())
                    # 相对路径转绝对路径（对链接和图片）
                    if any(kw in field.lower() for kw in ["url", "link", "href", "src", "img", "image"]):
                        from urllib.parse import urljoin
                        values = [urljoin(base_url, v) if v else v for v in values]
                    result[field] = values
                except Exception as e:
                    logger.debug(f"快速提取 XPath 失败: {field}={xpath_expr}: {e}")
                    result[field] = []

            return result
        except Exception as e:
            logger.error(f"快速提取失败: {e}")
            return {k: [] for k in xpath_map}

    def _quick_extract_bs(self, html: str, xpath_map: Dict[str, str],
                          base_url: str = "") -> Dict[str, List[str]]:
        """BeautifulSoup 降级版快速提取"""
        soup = BeautifulSoup(html, "lxml")
        result = {}
        for field, xpath_expr in xpath_map.items():
            try:
                elements = soup.select(xpath_expr.replace("//", "").replace("[", ":").replace("]", ""))
                values = [e.get_text(strip=True) for e in elements]
                result[field] = values
            except Exception:
                result[field] = []
        return result

    def _get_base_url(self, soup: BeautifulSoup, fallback_url: str) -> str:
        base_tag = soup.find("base")
        if base_tag and base_tag.get("href"):
            return base_tag["href"]
        return fallback_url

    def _extract_title(self, soup: BeautifulSoup) -> str:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()
            title = re.sub(r"\s*[–\-|·•]\s*.*$", "", title).strip()
            return title
        return ""

    def _extract_text(self, raw_html: str, url: str = "") -> str:
        # --- 第一步：用干净副本移除所有非正文标签 ---
        soup = BeautifulSoup(raw_html, "lxml")
        # 结构性非正文标签
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "noscript", "iframe", "svg", "form",
                          "aside", "button", "input", "select"]):
            tag.decompose()

        # 按 class/id 关键词移除侧边栏、广告、推荐、评论等噪音区域
        noise_patterns = [
            "sidebar", "side-bar", "aside", "widget", "recommend",
            "related", "comment", "share", "social", "follow",
            "advertisement", "ad-", "ads-", "banner", "promotion",
            "cookie", "newsletter", "subscribe", "popup", "modal",
            "breadcrumb", "crumb", "pagination", "pager",
            "toc", "table-of-contents", "menu", "toolbar",
            "nav", "footer", "copyright", "license", "disclaimer",
            "login", "signup", "register", "search-box",
            "hot-", "trending", "popular", "tags", "tag-cloud",
            "author-box", "author-info", "post-meta",
            "reading-list", "bookmark", "like-button",
            "weixin", "wechat", "qrcode", "scan",
            "recommend-article", "related-post", "more-story",
        ]
        # 计算页面总文本量，用于防止误删正文容器
        total_text_len = len(soup.get_text(strip=True))
        for el in soup.find_all(True):
            # 某些畸形HTML元素的 attrs 可能为 None，跳过
            if el.attrs is None:
                continue
            el_class = el.get("class") or []
            if not isinstance(el_class, list):
                el_class = [str(el_class)]
            el_id = el.get("id") or ""
            attrs_str = " ".join(el_class + [el_id]).lower()
            if any(pat in attrs_str for pat in noise_patterns):
                # 安全保护：如果该元素包含页面超过30%的文本，可能是正文容器，跳过
                el_text_len = len(el.get_text(strip=True))
                if total_text_len > 0 and el_text_len / total_text_len > 0.3:
                    logger.debug(f"跳过噪音过滤（含正文）: {el.name} class={el_class} id={el_id}")
                    continue
                el.decompose()

        # --- 第二步：提取正文内容 ---
        main_content = self._extract_main_content(soup)
        target = main_content if main_content else soup

        # 块级标签之间插入双换行，形成段落分隔
        block_tags = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                      "li", "blockquote", "tr", "pre", "section",
                      "article", "figure", "figcaption", "dt", "dd"}
        for tag in target.find_all(block_tags):
            tag.insert_before("\n\n")
            tag.insert_after("\n")

        text = target.get_text(separator="\n", strip=False)

        # --- 第三步：后处理清洗 ---
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                # 保留段落间的空行（合并连续空行为一个）
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
                continue
            # 跳过明显的噪音行：纯数字、过短的导航/按钮文本
            if len(line) <= 3 and not re.search(r'[\u4e00-\u9fff]', line):
                continue
            # 跳过纯"阅读全文"/"展开全文"/"举报"/"评论"等按钮文字
            if re.fullmatch(r'(全文|阅读全文|展开全文|收起|展开|举报|评论|分享|收藏|关注|点赞|转发|登录|注册|查看更多|加载更多|展开全部|下一篇|上一篇|首页|目录|相关推荐)', line):
                continue
            cleaned.append(line)

        # 去掉首尾空行
        text = "\n".join(cleaned).strip()

        # --- 兜底：如果清洗后内容过少，用原始HTML重新解析提取 ---
        if len(text) < 100:
            logger.warning(f"文本提取结果过短（{len(text)}字符），启用兜底模式重新提取: {url}")
            fresh_soup = BeautifulSoup(raw_html, "lxml")
            # 只做最简清洗：移除 script/style
            for tag in fresh_soup(["script", "style", "noscript", "iframe", "svg"]):
                tag.decompose()
            fallback_target = self._extract_main_content(fresh_soup) or fresh_soup
            for tag in fallback_target.find_all(block_tags):
                tag.insert_before("\n\n")
                tag.insert_after("\n")
            text = fallback_target.get_text(separator="\n", strip=True)
            # 兜底模式只做最基础的行清理
            lines = text.split("\n")
            cleaned = []
            for line in lines:
                line = line.strip()
                if not line:
                    if cleaned and cleaned[-1] != "":
                        cleaned.append("")
                    continue
                cleaned.append(line)
            text = "\n".join(cleaned).strip()

        # 字数限制（0=不限制）
        if self.max_chars > 0 and len(text) > self.max_chars:
            orig_len = len(text)
            text = text[:self.max_chars] + f"\n... [文本已截断，原文共{orig_len}字符]"

        return text

    def _extract_main_content(self, soup: BeautifulSoup) -> Optional[Tag]:
        for selector in self._content_tags:
            if selector.startswith("[") and selector.endswith("]"):
                attr_name, attr_value = selector[1:-1].split("=")
                attr_value = attr_value.strip("'\"")
                elements = soup.find_all(attrs={attr_name: attr_value})
            else:
                elements = soup.select(selector)
            if elements:
                return max(elements, key=lambda el: len(el.get_text(strip=True)))
        return None

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        links: Set[str] = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            parsed = urlparse(href)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                continue
            full_url = urljoin(base_url, href)
            links.add(full_url)
        return list(links)

    def _extract_images(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        images: List[str] = []
        seen: Set[str] = set()
        lazy_attrs = [
            "data-src", "data-original", "data-lazy_src",
            "data-actualsrc", "data-original-src", "data-bg",
            "data-url", "data-lazy",
        ]
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith("data:"):
                full_url = urljoin(base_url, src)
                if full_url not in seen:
                    images.append(full_url)
                    seen.add(full_url)
            for attr in lazy_attrs:
                val = img.get(attr, "")
                if val and not val.startswith("data:"):
                    full_url = urljoin(base_url, val)
                    if full_url not in seen:
                        images.append(full_url)
                        seen.add(full_url)
            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if srcset:
                for part in srcset.split(","):
                    part = part.strip()
                    if part:
                        url_part = part.split(" ")[0].strip()
                        if url_part and not url_part.startswith("data:"):
                            full_url = urljoin(base_url, url_part)
                            if full_url not in seen:
                                images.append(full_url)
                                seen.add(full_url)
        return images

    def _extract_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        metadata = {"description": "", "keywords": "", "author": ""}
        for name in ("description", "og:description", "twitter:description"):
            meta = soup.find("meta", attrs={"name": name}) or \
                   soup.find("meta", attrs={"property": name})
            if meta and meta.get("content"):
                metadata["description"] = meta["content"].strip()
                break
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and meta_kw.get("content"):
            metadata["keywords"] = meta_kw["content"].strip()
        for name in ("author", "og:author", "article:author"):
            meta = soup.find("meta", attrs={"name": name}) or \
                   soup.find("meta", attrs={"property": name})
            if meta and meta.get("content"):
                metadata["author"] = meta["content"].strip()
                break
        return metadata

    def _extract_headings(self, soup: BeautifulSoup) -> Dict[str, List[str]]:
        headings = {"h1": [], "h2": [], "h3": []}
        for level in headings:
            for tag in soup.find_all(level):
                text = tag.get_text(strip=True)
                if text:
                    headings[level].append(text)
        return headings

    def _extract_schema_org(self, soup: BeautifulSoup) -> List[Dict]:
        schemas = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    schemas.extend(data)
                elif isinstance(data, dict):
                    schemas.append(data)
            except (json.JSONDecodeError, TypeError):
                continue
        for item in soup.find_all(attrs={"itemscope": True}):
            item_type = item.get("itemtype", "")
            props = {}
            for prop in item.find_all(attrs={"itemprop": True}):
                prop_name = prop.get("itemprop", "")
                prop_value = prop.get("content", "") or prop.get_text(strip=True)
                if prop_name:
                    props[prop_name] = prop_value
            if props:
                schema_entry = {"@type": item_type.split("/")[-1] if item_type else "Thing"}
                schema_entry.update(props)
                schemas.append(schema_entry)
        if schemas:
            logger.debug(f"📋 提取 {len(schemas)} 条 Schema.org 数据")
        return schemas

    def _extract_open_graph(self, soup: BeautifulSoup) -> Dict[str, str]:
        og_data: Dict[str, str] = {}
        for meta in soup.find_all("meta", property=True):
            prop = meta.get("property", "")
            if prop.startswith("og:"):
                content = meta.get("content", "")
                if content:
                    og_data[prop] = content.strip()
        if og_data:
            logger.debug(f"📋 提取 {len(og_data)} 条 Open Graph 数据")
        return og_data

    def _extract_twitter_card(self, soup: BeautifulSoup) -> Dict[str, str]:
        tc_data: Dict[str, str] = {}
        for meta in soup.find_all("meta", attrs={"name": True}):
            name = meta.get("name", "")
            if name.startswith("twitter:"):
                content = meta.get("content", "")
                if content:
                    tc_data[name] = content.strip()
        return tc_data

    def _extract_tables(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        tables = []
        for table in soup.find_all("table"):
            table_data: Dict[str, Any] = {"headers": [], "rows": []}
            thead = table.find("thead")
            if thead:
                for th in thead.find_all("th"):
                    table_data["headers"].append(th.get_text(strip=True))
            if not table_data["headers"]:
                first_row = table.find("tr")
                if first_row:
                    ths = first_row.find_all("th")
                    if ths:
                        table_data["headers"] = [th.get_text(strip=True) for th in ths]
            tbody = table.find("tbody") or table
            for tr in tbody.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                row = [cell.get_text(strip=True) for cell in cells]
                if row:
                    table_data["rows"].append(row)
            if table_data["rows"]:
                tables.append(table_data)
        if tables:
            logger.debug(f"📊 提取 {len(tables)} 个表格")
        return tables

    def _extract_comments(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        comments = []
        comment_selectors = [
            ".comment", ".comments", ".comment-list li",
            ".comment-item", ".review", ".reviews li",
            "[data-comment]", ".post-comment",
            ".user-comment", ".media-comment",
        ]
        for selector in comment_selectors:
            elements = soup.select(selector)
            if not elements:
                continue
            for el in elements:
                comment: Dict[str, str] = {}
                author_el = (
                    el.select_one(".comment-author, .author, .reviewer, [itemprop='author']")
                    or el.select_one("cite, .name, .username, strong")
                )
                if author_el:
                    comment["author"] = author_el.get_text(strip=True)
                content_el = (
                    el.select_one(".comment-content, .comment-text, .review-text, [itemprop='text']")
                    or el.select_one("p, .content, .text")
                )
                if content_el:
                    comment["text"] = content_el.get_text(strip=True)
                date_el = el.select_one("time, .date, .comment-date, [itemprop='datePublished']")
                if date_el:
                    comment["date"] = date_el.get("datetime", "") or date_el.get_text(strip=True)
                if comment.get("text"):
                    comments.append(comment)
            if comments:
                break
        if comments:
            logger.debug(f"💬 提取 {len(comments)} 条评论")
        return comments

    def _extract_pagination_links(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        pagination: Dict[str, Any] = {"next_url": None, "prev_url": None, "all_pages": [], "current_page": None}
        next_link = soup.find("link", rel="next") or soup.find("a", rel="next")
        if next_link and next_link.get("href"):
            pagination["next_url"] = urljoin(base_url, next_link["href"])
        prev_link = soup.find("link", rel="prev") or soup.find("a", rel="prev")
        if prev_link and prev_link.get("href"):
            pagination["prev_url"] = urljoin(base_url, prev_link["href"])
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
                        pagination["all_pages"].append(urljoin(base_url, href))
                current = container.select_one(".active, .current, [aria-current='page']")
                if current:
                    text = current.get_text(strip=True)
                    try:
                        pagination["current_page"] = int(text)
                    except ValueError:
                        pass
                break
        return pagination

    def _extract_srcset(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
        srcset_data = []
        for img in soup.find_all("img"):
            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if not srcset:
                continue
            for part in srcset.split(","):
                part = part.strip()
                if not part:
                    continue
                tokens = part.split()
                url = tokens[0]
                descriptor = tokens[1] if len(tokens) > 1 else ""
                if url:
                    srcset_data.append({"url": urljoin(base_url, url), "descriptor": descriptor, "source_img_src": img.get("src", "")})
        return srcset_data

    def _extract_videos(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
        videos: List[Dict[str, str]] = []
        seen: Set[str] = set()

        def _add_video(url: str, vtype: str = "", title: str = "", source: str = ""):
            if not url or url in seen or url.startswith("data:"):
                return
            seen.add(url)
            videos.append({"url": urljoin(base_url, url), "type": vtype, "title": title, "source": source})

        for video in soup.find_all("video"):
            src = video.get("src", "")
            if src:
                _add_video(src, "video/html5", video.get("title", ""), "video_tag")
            for source in video.find_all("source"):
                src = source.get("src", "")
                if src:
                    _add_video(src, source.get("type", "video/html5"), "", "video_source")
            poster = video.get("poster", "")
            if poster:
                _add_video(poster, "poster", "", "video_poster")

        embed_patterns = {
            "youtube": r"(?:youtube\.com/embed/|youtu\.be/)([a-zA-Z0-9_-]+)",
            "bilibili": r"bilibili\.com/video/(BV\w+)",
            "youku": r"v\.youku\.com/v_show/id_(\w+)",
            "vimeo": r"player\.vimeo\.com/video/(\d+)",
            "dailymotion": r"dailymotion\.com/embed/video/(\w+)",
            "tencent": r"v\.qq\.com/x/page/(\w+)",
        }
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            for platform, pattern in embed_patterns.items():
                match = re.search(pattern, src)
                if match:
                    _add_video(src, f"embed/{platform}", iframe.get("title", ""), "iframe")
                    break

        video_extensions = (".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".m3u8")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if any(href.lower().endswith(ext) for ext in video_extensions):
                _add_video(href, "direct_link", a_tag.get_text(strip=True), "a_tag")

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("@type") == "VideoObject":
                    _add_video(data.get("contentUrl", "") or data.get("embedUrl", ""), "schema_org", data.get("name", ""), "json_ld")
            except (json.JSONDecodeError, TypeError):
                continue

        for embed in soup.find_all("embed", src=True):
            src = embed["src"]
            if "video" in embed.get("type", "") or any(ext in src for ext in video_extensions):
                _add_video(src, "embed", "", "embed_tag")

        if videos:
            logger.debug(f"🎬 提取 {len(videos)} 个视频 URL")
        return videos

    # ==================== 15种内容类型提取 ====================

    CONTENT_TYPES = [
        "title",          # 1. 标题
        "text",           # 2. 正文文本
        "html",           # 3. HTML源码
        "links",          # 4. 链接URL
        "images",         # 5. 图片URL
        "videos",        # 6. 视频URL
        "tables",         # 7. 表格数据
        "metadata",      # 8. 元数据（meta标签）
        "headings",      # 9. 标题结构（h1-h6）
        "schema_org",    # 10. Schema.org结构化数据
        "open_graph",    # 11. Open Graph协议数据
        "comments",      # 12. 评论区数据
        "pagination",    # 13. 分页信息
        "srcset_images", # 14. 响应式图片（srcset）
        "custom_fields", # 15. 自定义字段提取
    ]

    def extract_content_by_type(self, html: str, url: str,
                                content_types: List[str] = None) -> Dict:
        """按内容类型批量提取（15种内容类型）

        Args:
            html: HTML字符串
            url: 页面URL
            content_types: 要提取的类型列表，默认提取全部

        Returns:
            {类型名: 提取结果}
        """
        if content_types is None:
            content_types = self.CONTENT_TYPES

        soup = BeautifulSoup(html, "lxml")
        base_url = self._get_base_url(soup, url)
        result = {}

        type_map = {
            "title": lambda: self._extract_title(soup),
            "text": lambda: self._extract_text(html, url),
            "html": lambda: str(self._extract_main_content(soup)),
            "links": lambda: self._extract_links(soup, base_url),
            "images": lambda: self._extract_images(soup, base_url),
            "videos": lambda: self._extract_videos(soup, base_url),
            "tables": lambda: self._extract_tables(soup) if self.extract_tables else [],
            "metadata": lambda: self._extract_metadata(soup),
            "headings": lambda: self._extract_headings(soup),
            "schema_org": lambda: self._extract_schema_org(soup) if self.extract_schema else {},
            "open_graph": lambda: {
                "og": self._extract_open_graph(soup) if self.extract_og else {},
                "twitter": self._extract_twitter_card(soup) if self.extract_og else {},
            },
            "comments": lambda: self._extract_comments(soup) if self.extract_comments else [],
            "pagination": lambda: self._extract_pagination_links(soup, base_url) if self.extract_pagination else {},
            "srcset_images": lambda: self._extract_srcset(soup, base_url) if self.extract_srcset else [],
        }

        for ct in content_types:
            if ct in type_map:
                try:
                    result[ct] = type_map[ct]()
                except Exception as e:
                    logger.debug(f"提取 {ct} 失败: {e}")
                    result[ct] = None
            elif ct == "custom_fields":
                result["custom_fields"] = {}

        return result

    def extract_attribute(self, html: str, selector: str,
                          attribute: str = "text",
                          base_url: str = "") -> List[str]:
        """提取指定CSS选择器的属性值（通用字段提取器）

        Args:
            html: HTML字符串
            selector: CSS选择器，如 ".price" 或 "h1.title"
            attribute: 要提取的属性: "text" / "html" / "href" / "src" / "data-id" 等
            base_url: 基础URL（用于相对路径转绝对路径）

        Returns:
            匹配值列表
        """
        soup = BeautifulSoup(html, "lxml")
        elements = soup.select(selector)
        values = []

        for el in elements:
            if attribute == "text":
                val = el.get_text(strip=True)
            elif attribute == "html":
                val = str(el)
            elif attribute == "href" or attribute == "src":
                val = el.get(attribute, "")
                if val and base_url:
                    val = urljoin(base_url, val)
            else:
                val = el.get(attribute, "")
            if val:
                values.append(val)

        return values