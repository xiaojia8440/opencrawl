"""数据清洗模块 — 数据验证、HTML 清理、编码修正、格式化、质量评分"""

import re
import logging
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class DataCleaner:
    """
    数据清洗器

    支持功能:
    - HTML 标签去除与文本提取
    - 编码修正（乱码检测与修复）
    - 字段提取与格式化（日期、电话、邮箱、URL 等）
    - 数据验证与过滤
    - 数据质量评分
    - 自定义清洗规则管道
    - 批量清洗与统计
    """

    def __init__(self, config: dict):
        self.config = config
        cleaner_cfg = config.get("data_cleaner", {})

        # 清洗选项
        self._strip_html: bool = cleaner_cfg.get("strip_html", True)
        self._fix_encoding: bool = cleaner_cfg.get("fix_encoding", True)
        self._normalize_whitespace: bool = cleaner_cfg.get("normalize_whitespace", True)
        self._remove_empty: bool = cleaner_cfg.get("remove_empty", True)
        self._min_length: int = cleaner_cfg.get("min_length", 10)
        self._quality_threshold: float = cleaner_cfg.get("quality_threshold", 0.3)

        # 自定义规则
        self._rules: List[Dict] = cleaner_cfg.get("rules", [])

        # 统计
        self._stats: Dict[str, int] = {
            "total_processed": 0,
            "html_stripped": 0,
            "encoding_fixed": 0,
            "items_removed": 0,
            "low_quality": 0,
        }

        logger.info("🧹 DataCleaner 初始化完成")

    # ──────────────────────────────
    # HTML 清理
    # ──────────────────────────────

    def strip_html(self, text: str) -> str:
        """去除 HTML 标签，保留纯文本"""
        if not text:
            return ""
        # 去除 script 和 style 标签及内容
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # 去除 HTML 注释
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        # 将块级标签转为换行
        text = re.sub(r'<(br|p|div|h[1-6]|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
        # 去除所有剩余标签
        text = re.sub(r'<[^>]+>', '', text)
        # 解码 HTML 实体
        text = self._decode_html_entities(text)
        return text.strip()

    @staticmethod
    def _decode_html_entities(text: str) -> str:
        """解码 HTML 实体"""
        entities = {
            '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
            '&#39;': "'", '&apos;': "'", '&nbsp;': ' ',
            '&mdash;': '—', '&ndash;': '–', '&hellip;': '…',
            '&copy;': '©', '&reg;': '®', '&trade;': '™',
        }
        for entity, char in entities.items():
            text = text.replace(entity, char)
        # 处理数字实体 &#123;
        text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
        text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
        return text

    # ──────────────────────────────
    # 编码修正
    # ──────────────────────────────

    def fix_encoding(self, text: str) -> str:
        """修正编码问题（乱码检测与修复）"""
        if not text:
            return ""

        # 检测常见乱码模式
        # Mojibake: UTF-8 被错误解码为 Latin-1
        mojibake_patterns = [
            r'[\u00c0-\u00ff]{2,}',  # 连续的高位 Latin-1 字符
        ]

        for pattern in mojibake_patterns:
            if re.search(pattern, text):
                try:
                    # 尝试修复：先编码为 Latin-1，再解码为 UTF-8
                    fixed = text.encode('latin-1').decode('utf-8')
                    self._stats["encoding_fixed"] += 1
                    return fixed
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass

        # Unicode 规范化
        text = unicodedata.normalize('NFC', text)
        return text

    # ──────────────────────────────
    # 文本规范化
    # ──────────────────────────────

    def normalize_whitespace(self, text: str) -> str:
        """规范化空白字符"""
        if not text:
            return ""
        # 全角空格转半角
        text = text.replace('\u3000', ' ')
        # 多个空白合并为一个
        text = re.sub(r'[ \t]+', ' ', text)
        # 多个换行合并为两个
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 去除行首行尾空白
        lines = [line.strip() for line in text.split('\n')]
        return '\n'.join(lines)

    # ──────────────────────────────
    # 字段提取
    # ──────────────────────────────

    @staticmethod
    def extract_emails(text: str) -> List[str]:
        """从文本中提取邮箱"""
        pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
        return list(set(re.findall(pattern, text)))

    @staticmethod
    def extract_phones(text: str) -> List[str]:
        """从文本中提取电话号码"""
        # 中国大陆手机号
        cn_mobile = r'1[3-9]\d{9}'
        # 固定电话
        cn_landline = r'\d{3,4}[-\s]?\d{7,8}'
        # 国际格式
        intl = r'\+?\d{1,3}[-\s]?\d{6,12}'

        phones = set()
        for pattern in [cn_mobile, cn_landline, intl]:
            phones.update(re.findall(pattern, text))
        return list(phones)

    @staticmethod
    def extract_urls(text: str) -> List[str]:
        """从文本中提取 URL"""
        pattern = r'https?://[^\s<>"\')\]]+'
        urls = re.findall(pattern, text)
        # 去除末尾标点
        return [url.rstrip('.,;:!?') for url in set(urls)]

    @staticmethod
    def extract_dates(text: str) -> List[str]:
        """从文本中提取日期"""
        patterns = [
            r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?',  # 2024-01-01, 2024年1月1日
            r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',  # 01/01/2024
            r'\d{4}\d{2}\d{2}',  # 20240101
        ]
        dates = set()
        for pattern in patterns:
            dates.update(re.findall(pattern, text))
        return list(dates)

    @staticmethod
    def extract_prices(text: str) -> List[str]:
        """从文本中提取价格"""
        patterns = [
            r'[¥$€£]\s?\d+(?:[.,]\d+)?',  # ¥100, $99.99
            r'\d+(?:[.,]\d+)?\s?(?:元|美元|欧元|英镑)',  # 100元
            r'￥\d+(?:\.\d+)?',  # ￥100.00
        ]
        prices = set()
        for pattern in patterns:
            prices.update(re.findall(pattern, text))
        return list(prices)

    # ──────────────────────────────
    # 数据质量评分
    # ──────────────────────────────

    def quality_score(self, item: Dict, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """评估数据质量，返回评分报告

        Args:
            item: 数据项
            fields: 要评估的字段列表，None 则评估所有字符串字段

        Returns:
            {
                "score": 0.0-1.0,
                "details": {field: {score, issues}},
                "passed": bool
            }
        """
        if fields is None:
            fields = [k for k, v in item.items() if isinstance(v, str)]

        details = {}
        total_score = 0.0

        for field in fields:
            value = item.get(field)
            if value is None:
                details[field] = {"score": 0.0, "issues": ["字段缺失"]}
                continue

            if not isinstance(value, str):
                value = str(value)

            field_score = 1.0
            issues = []

            # 检查1: 空值
            if not value.strip():
                details[field] = {"score": 0.0, "issues": ["内容为空"]}
                continue

            # 检查2: 过短
            if len(value) < self._min_length:
                field_score -= 0.3
                issues.append(f"内容过短 ({len(value)} 字符)")

            # 检查3: 残留 HTML 标签
            html_tags = re.findall(r'<[^>]+>', value)
            if html_tags:
                field_score -= 0.2
                issues.append(f"残留 {len(html_tags)} 个 HTML 标签")

            # 检查4: 乱码检测
            if self._has_mojibake(value):
                field_score -= 0.3
                issues.append("疑似乱码")

            # 检查5: 重复字符比例
            if len(value) > 10:
                unique_ratio = len(set(value)) / len(value)
                if unique_ratio < 0.1:
                    field_score -= 0.2
                    issues.append("重复字符比例过高")

            # 检查6: 特殊字符比例
            special_count = len(re.findall(r'[^\w\s\u4e00-\u9fff.,;:!?，。；：！？、""''（）\-]', value))
            special_ratio = special_count / len(value) if value else 0
            if special_ratio > 0.3:
                field_score -= 0.2
                issues.append(f"特殊字符比例过高 ({special_ratio:.1%})")

            field_score = max(0.0, field_score)
            details[field] = {"score": round(field_score, 2), "issues": issues}
            total_score += field_score

        avg_score = round(total_score / len(fields), 2) if fields else 0.0
        passed = avg_score >= self._quality_threshold

        if not passed:
            self._stats["low_quality"] += 1

        return {
            "score": avg_score,
            "details": details,
            "passed": passed,
        }

    @staticmethod
    def _has_mojibake(text: str) -> bool:
        """检测是否存在乱码"""
        # 检测连续的高位 Latin-1 字符（常见乱码特征）
        mojibake = re.findall(r'[\u00c0-\u00ff]{3,}', text)
        return len(mojibake) > 0

    # ──────────────────────────────
    # 清洗管道
    # ──────────────────────────────

    def clean_item(self, item: Dict, custom_rules: Optional[List[Dict]] = None) -> Dict:
        """清洗单个数据项

        Args:
            item: 原始数据项
            custom_rules: 自定义规则（覆盖全局配置）

        Returns:
            清洗后的数据项
        """
        self._stats["total_processed"] += 1
        cleaned = dict(item)

        # 遍历所有字符串字段进行清洗
        for key, value in cleaned.items():
            if not isinstance(value, str):
                continue

            # HTML 清理
            if self._strip_html and '<' in value and '>' in value:
                cleaned[key] = self.strip_html(value)
                self._stats["html_stripped"] += 1
                value = cleaned[key]

            # 编码修正
            if self._fix_encoding:
                cleaned[key] = self.fix_encoding(value)
                value = cleaned[key]

            # 空白规范化
            if self._normalize_whitespace:
                cleaned[key] = self.normalize_whitespace(value)

        # 应用自定义规则
        rules = custom_rules or self._rules
        for rule in rules:
            cleaned = self._apply_rule(cleaned, rule)

        return cleaned

    def clean_batch(
        self,
        items: List[Dict],
        quality_filter: bool = False,
        fields_to_check: Optional[List[str]] = None,
    ) -> List[Dict]:
        """批量清洗数据

        Args:
            items: 原始数据列表
            quality_filter: 是否过滤低质量数据
            fields_to_check: 质量检查的字段列表

        Returns:
            清洗后的数据列表
        """
        results = []
        for item in items:
            cleaned = self.clean_item(item)

            # 质量过滤
            if quality_filter:
                report = self.quality_score(cleaned, fields_to_check)
                if not report["passed"]:
                    self._stats["items_removed"] += 1
                    continue

            # 空数据过滤
            if self._remove_empty:
                non_empty = {k: v for k, v in cleaned.items() if v is not None and v != ""}
                if not non_empty:
                    self._stats["items_removed"] += 1
                    continue
                cleaned = non_empty

            results.append(cleaned)

        logger.info(f"🧹 清洗完成: {len(items)} → {len(results)} 条")
        return results

    def _apply_rule(self, item: Dict, rule: Dict) -> Dict:
        """应用单条自定义清洗规则

        规则格式:
        {
            "field": "title",            # 目标字段
            "action": "replace",         # 操作类型
            "pattern": "\\s+",           # 正则模式（replace 时）
            "replacement": " ",          # 替换内容
        }

        支持的 action:
        - replace: 正则替换
        - truncate: 截断（需指定 max_length）
        - lowercase / uppercase: 大小写转换
        - extract: 正则提取
        - rename: 字段重命名
        - remove: 删除字段
        """
        field = rule.get("field", "")
        action = rule.get("action", "")

        if action == "rename":
            new_name = rule.get("new_name", "")
            if field in item and new_name:
                item[new_name] = item.pop(field)
            return item

        if action == "remove":
            item.pop(field, None)
            return item

        value = item.get(field)
        if value is None or not isinstance(value, str):
            return item

        if action == "replace":
            pattern = rule.get("pattern", "")
            replacement = rule.get("replacement", "")
            if pattern:
                item[field] = re.sub(pattern, replacement, value)

        elif action == "truncate":
            max_length = rule.get("max_length", 100)
            suffix = rule.get("suffix", "...")
            if len(value) > max_length:
                item[field] = value[:max_length] + suffix

        elif action == "lowercase":
            item[field] = value.lower()

        elif action == "uppercase":
            item[field] = value.upper()

        elif action == "extract":
            pattern = rule.get("pattern", "")
            if pattern:
                matches = re.findall(pattern, value)
                item[field] = matches[0] if matches else ""

        return item

    # ──────────────────────────────
    # 统计
    # ──────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """获取清洗统计"""
        return dict(self._stats)

    def reset_stats(self):
        """重置统计"""
        for key in self._stats:
            self._stats[key] = 0
