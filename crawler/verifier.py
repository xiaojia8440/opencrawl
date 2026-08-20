"""
数据完整性校验模块 — 爬取结果的全量验证与质量报告

功能:
- 基本统计：总条数、字段覆盖率/空值率、字段长度分布
- 重复检测：URL重复、内容哈希重复、SimHash近似重复
- 分页完整性：断页检测、每页条数分布
- 内容质量：过短内容、乱码、截断、HTML残留检测
- 反爬识别：验证码页、错误页、空壳页标记
- 两次爬取diff：新增/消失/变化条目对比
- 输出报告：控制台摘要 + JSON详细报告

用法:
    from crawler.verifier import DataVerifier
    v = DataVerifier(config)
    report = v.verify("result.json")
    v.print_report(report)
"""

import json
import hashlib
import logging
import re
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from collections import Counter, defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# 常见验证码/反爬页面特征关键词
_ANTI_BOT_KEYWORDS = [
    "验证码", "人机验证", "访问受限", "access denied", "forbidden",
    "captcha", "robot check", "please verify", "安全验证",
    "请求过于频繁", "rate limit", "too many requests", "blocked",
    "403 forbidden", "403", "429",
]

# 错误页面特征
_ERROR_PATTERNS = [
    r"not\s+found", r"页面不存在", r"404",
    r"server\s+error", r"服务器错误", r"50[023]",
    r"service\s+unavailable", r"服务不可用",
]

# HTML标签残留检测
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")
# URL 格式校验
_URL_RE = re.compile(r"^https?://[^\s<>'\"]+$")
# 中文/日文/韩文字符（用于判断是否有实际文字内容）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")


class DataVerifier:
    """数据完整性校验器"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        verify_cfg = self.config.get("verifier", {})
        # 过短内容阈值（字符数）
        self.min_content_length: int = verify_cfg.get("min_content_length", 50)
        # 字段空值率告警阈值（0-1）
        self.null_rate_threshold: float = verify_cfg.get("null_rate_threshold", 0.3)
        # SimHash 近似去重汉明距离阈值
        self.simhash_threshold: int = verify_cfg.get("simhash_threshold", 5)
        # 是否检查反爬特征
        self.check_anti_bot: bool = verify_cfg.get("check_anti_bot", True)

    def verify(
        self,
        data_path: str,
        baseline_path: Optional[str] = None,
        expected_total: Optional[int] = None,
        required_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        校验爬取结果文件。

        Args:
            data_path: 爬取结果文件路径（JSON/JSONL/CSV）
            baseline_path: 上一次爬取结果（可选，用于diff对比）
            expected_total: 目标站声明的总条数（可选，用于数量对账）
            required_fields: 必填字段列表（可选，自动推断时不填）

        Returns:
            校验报告字典
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "file": data_path,
            "file_size": 0,
            "status": "ok",  # ok / warning / error
            "warnings": [],
            "errors": [],
            "summary": {},
            "details": {},
        }

        # 1. 加载数据
        data = self._load_data(data_path)
        if data is None:
            report["status"] = "error"
            report["errors"].append(f"无法加载数据文件: {data_path}")
            return report

        file_size = os.path.getsize(data_path) if os.path.exists(data_path) else 0
        report["file_size"] = file_size
        total = len(data)
        report["summary"]["total_records"] = total

        if total == 0:
            report["status"] = "error"
            report["errors"].append("数据为空，0条记录")
            return report

        # 2. 基本统计
        field_stats = self._field_statistics(data, required_fields)
        report["details"]["field_stats"] = field_stats
        report["summary"]["fields"] = list(field_stats.keys())

        # 字段空值率检查
        for field, stats in field_stats.items():
            null_rate = stats.get("null_rate", 0)
            if null_rate > self.null_rate_threshold:
                report["warnings"].append(
                    f"字段 '{field}' 空值率 {null_rate:.1%}，超过阈值 {self.null_rate_threshold:.0%}"
                )

        # 3. 重复检测
        dup_report = self._check_duplicates(data)
        report["details"]["duplicates"] = dup_report
        if dup_report["url_duplicates"] > 0:
            report["warnings"].append(
                f"URL重复: {dup_report['url_duplicates']} 条"
            )
        if dup_report["content_duplicates"] > 0:
            report["warnings"].append(
                f"内容完全重复: {dup_report['content_duplicates']} 条"
            )
        if dup_report["near_duplicates"] > 0:
            report["warnings"].append(
                f"近似重复(SimHash距离≤{self.simhash_threshold}): {dup_report['near_duplicates']} 对"
            )

        # 4. 内容质量检查
        quality = self._check_quality(data)
        report["details"]["quality"] = quality
        if quality["too_short"] > 0:
            report["warnings"].append(
                f"内容过短(<{self.min_content_length}字符): {quality['too_short']} 条"
            )
        if quality["has_html_residue"] > 0:
            report["warnings"].append(
                f"HTML标签残留: {quality['has_html_residue']} 条"
            )
        if quality["has_garbled"] > 0:
            report["warnings"].append(
                f"疑似乱码: {quality['has_garbled']} 条"
            )
        if quality["suspected_truncated"] > 0:
            report["warnings"].append(
                f"疑似截断( abrupt ending ): {quality['suspected_truncated']} 条"
            )

        # 5. 反爬检测
        if self.check_anti_bot:
            anti_bot = self._check_anti_bot(data)
            report["details"]["anti_bot"] = anti_bot
            if anti_bot["suspected_pages"] > 0:
                report["errors"].append(
                    f"疑似反爬/错误页面: {anti_bot['suspected_pages']} 条"
                )
                report["status"] = "error"

        # 6. 分页完整性（如果有分页信息）
        pagination = self._check_pagination(data)
        if pagination:
            report["details"]["pagination"] = pagination
            if pagination.get("gaps"):
                report["warnings"].append(
                    f"分页断档: 第 {pagination['gaps']} 页缺失"
                )

        # 7. 总数对账
        if expected_total is not None:
            diff = expected_total - total
            report["summary"]["expected_total"] = expected_total
            report["summary"]["total_diff"] = diff
            if diff > 0:
                report["warnings"].append(
                    f"数量不一致: 期望 {expected_total} 条，实际 {total} 条，少 {diff} 条"
                )
                if report["status"] == "ok":
                    report["status"] = "warning"
            elif diff < 0:
                report["warnings"].append(
                    f"数量超出: 期望 {expected_total} 条，实际 {total} 条，多 {-diff} 条"
                )

        # 8. 与基线diff
        if baseline_path and os.path.exists(baseline_path):
            baseline = self._load_data(baseline_path)
            if baseline is not None:
                diff_result = self._diff_data(baseline, data)
                report["details"]["diff"] = diff_result
                report["summary"]["diff_added"] = diff_result["added"]
                report["summary"]["diff_removed"] = diff_result["removed"]
                report["summary"]["diff_changed"] = diff_result["changed"]
                if diff_result["removed"] > 0:
                    report["warnings"].append(
                        f"对比基线，{diff_result['removed']} 条旧数据消失"
                    )

        # 9. 汇总状态
        if report["errors"]:
            report["status"] = "error"
        elif report["warnings"] and report["status"] == "ok":
            report["status"] = "warning"

        return report

    def print_report(self, report: Dict[str, Any]):
        """在控制台打印可读的校验报告"""
        status_icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}
        icon = status_icon.get(report["status"], "❓")

        print("\n" + "=" * 60)
        print(f"  数据校验报告  {icon}  {report['status'].upper()}")
        print("=" * 60)
        print(f"  文件: {report['file']}")
        print(f"  时间: {report['timestamp']}")
        size_kb = report["file_size"] / 1024
        print(f"  大小: {size_kb:.1f} KB")
        print("-" * 60)

        s = report.get("summary", {})
        print(f"  📊 总记录数: {s.get('total_records', 0)}")
        if "expected_total" in s:
            diff = s.get("total_diff", 0)
            marker = "✅" if diff == 0 else "⚠️"
            print(f"     期望总数: {s['expected_total']}  {marker} 差异: {diff}")
        if "diff_added" in s:
            print(f"  🔄 对比基线: +{s['diff_added']}新增  -{s['diff_removed']}消失  ~{s['diff_changed']}变化")

        # 字段统计
        fs = report.get("details", {}).get("field_stats", {})
        if fs:
            print("-" * 60)
            print("  📋 字段覆盖情况:")
            for field, stats in fs.items():
                rate = stats.get("fill_rate", 0)
                bar_len = 20
                filled = int(rate * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                avg_len = stats.get("avg_length", 0)
                print(f"     {field:<16} {bar} {rate:>5.1%}  (均长{avg_len:.0f})")

        # 重复
        dup = report.get("details", {}).get("duplicates", {})
        if dup:
            print("-" * 60)
            print("  🔁 重复检测:")
            print(f"     URL重复:     {dup.get('url_duplicates', 0)}")
            print(f"     内容重复:    {dup.get('content_duplicates', 0)}")
            print(f"     近似重复对:  {dup.get('near_duplicates', 0)}")
            total = s.get("total_records", 1)
            unique = dup.get("unique_count", total)
            print(f"     去重后:      {unique}  (重复率 {(1-unique/total):.1%})")

        # 质量
        q = report.get("details", {}).get("quality", {})
        if q:
            print("-" * 60)
            print("  🧹 内容质量:")
            print(f"     过短内容:    {q.get('too_short', 0)}")
            print(f"     HTML残留:    {q.get('has_html_residue', 0)}")
            print(f"     疑似乱码:    {q.get('has_garbled', 0)}")
            print(f"     疑似截断:    {q.get('suspected_truncated', 0)}")

        # 反爬
        ab = report.get("details", {}).get("anti_bot", {})
        if ab:
            print("-" * 60)
            print("  🛡️  反爬检测:")
            if ab.get("suspected_pages", 0) > 0:
                print(f"     ❌ 疑似被拦截页面: {ab['suspected_pages']}")
                for sample in ab.get("samples", [])[:3]:
                    print(f"        - {sample[:80]}")
            else:
                print("     ✅ 未发现反爬/错误页面特征")

        # 分页
        pg = report.get("details", {}).get("pagination", {})
        if pg:
            print("-" * 60)
            print("  📄 分页完整性:")
            print(f"     总页数: {pg.get('total_pages', '?')}")
            if pg.get("gaps"):
                print(f"     ⚠️  断页: {pg['gaps']}")
            per_page = pg.get("per_page_counts", {})
            if per_page:
                counts = list(per_page.values())
                if counts:
                    print(f"     每页条数: 最少{min(counts)} 最多{max(counts)} 平均{sum(counts)/len(counts):.1f}")

        # 警告和错误
        if report["warnings"]:
            print("-" * 60)
            print("  ⚠️  警告:")
            for w in report["warnings"]:
                print(f"     • {w}")

        if report["errors"]:
            print("-" * 60)
            print("  ❌ 错误:")
            for e in report["errors"]:
                print(f"     • {e}")

        print("=" * 60)

    def save_report(self, report: Dict[str, Any], output_path: str):
        """保存校验报告为JSON文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"📋 校验报告已保存: {output_path}")

    # ── 内部方法 ──────────────────────────────────────────

    def _load_data(self, path: str) -> Optional[List[Dict[str, Any]]]:
        """加载 JSON / JSONL / CSV 数据"""
        p = Path(path)
        if not p.exists():
            return None

        suffix = p.suffix.lower()
        try:
            if suffix == ".jsonl":
                data = []
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            data.append(json.loads(line))
                return data
            elif suffix == ".json":
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # 可能是 {"data": [...]} 或 {"items": [...]} 等包装
                    for key in ("data", "items", "results", "records", "list"):
                        if key in data and isinstance(data[key], list):
                            return data[key]
                    return [data]
                return data
            elif suffix == ".csv":
                import csv
                data = []
                with open(p, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        data.append(dict(row))
                return data
            else:
                # 尝试按 JSON 读
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content.startswith("["):
                        return json.loads(content)
                    elif content.startswith("{"):
                        return [json.loads(content)]
        except Exception as e:
            logger.error(f"加载数据失败: {e}")
            return None
        return None

    def _field_statistics(
        self, data: List[Dict], required_fields: Optional[List[str]] = None
    ) -> Dict[str, Dict]:
        """统计每个字段的覆盖率、空值率、长度分布"""
        if not data:
            return {}

        # 收集所有字段
        all_fields: Set[str] = set()
        for item in data:
            if isinstance(item, dict):
                all_fields.update(item.keys())

        # 如果未指定必填字段，用出现率 > 50% 的字段作为"核心字段"
        if required_fields is None:
            field_presence = Counter()
            for item in data:
                if isinstance(item, dict):
                    for k in item.keys():
                        field_presence[k] += 1
            required_fields = [
                f for f, count in field_presence.items()
                if count / len(data) > 0.5
            ]

        stats = {}
        for field in sorted(all_fields):
            values = []
            null_count = 0
            lengths = []

            for item in data:
                if not isinstance(item, dict):
                    null_count += 1
                    continue
                val = item.get(field)
                if val is None or val == "" or (isinstance(val, (list, dict)) and len(val) == 0):
                    null_count += 1
                else:
                    values.append(val)
                    if isinstance(val, str):
                        lengths.append(len(val))
                    elif isinstance(val, (list, dict)):
                        lengths.append(len(json.dumps(val, ensure_ascii=False)))
                    else:
                        lengths.append(len(str(val)))

            fill_count = len(data) - null_count
            fill_rate = fill_count / len(data)
            avg_len = sum(lengths) / len(lengths) if lengths else 0
            min_len = min(lengths) if lengths else 0
            max_len = max(lengths) if lengths else 0

            stats[field] = {
                "fill_count": fill_count,
                "null_count": null_count,
                "fill_rate": round(fill_rate, 4),
                "null_rate": round(1 - fill_rate, 4),
                "avg_length": round(avg_len, 1),
                "min_length": min_len,
                "max_length": max_len,
                "is_required": field in (required_fields or []),
            }

        return stats

    def _check_duplicates(self, data: List[Dict]) -> Dict:
        """检测URL重复、内容重复、近似重复"""
        url_seen: Set[str] = set()
        url_dupes = 0
        content_hashes: Set[str] = set()
        content_dupes = 0
        near_dupe_pairs = 0

        # SimHash 相关
        simhash_values: List[int] = []
        try:
            from crawler.dedup import SimHash
            simhash_calc = SimHash(hash_bits=64)
            has_simhash = True
        except ImportError:
            has_simhash = False

        for item in data:
            if not isinstance(item, dict):
                continue

            # URL 去重
            url = item.get("url") or item.get("link") or item.get("href", "")
            if url:
                url = str(url).strip()
                if url in url_seen:
                    url_dupes += 1
                else:
                    url_seen.add(url)

            # 内容哈希
            content = item.get("content") or item.get("text") or item.get("body") or item.get("title", "")
            if content:
                content_str = str(content)
                h = hashlib.md5(content_str.encode("utf-8")).hexdigest()
                if h in content_hashes:
                    content_dupes += 1
                else:
                    content_hashes.add(h)

                # SimHash
                if has_simhash and len(content_str) > 20:
                    sh = simhash_calc.compute(content_str)
                    # 与之前所有比较（数据量大时这里会慢，限制比较范围）
                    for prev_sh in simhash_values[-200:]:  # 只比最近200条，控制复杂度
                        dist = simhash_calc.distance(sh, prev_sh)
                        if 0 < dist <= self.simhash_threshold:
                            near_dupe_pairs += 1
                            break
                    simhash_values.append(sh)

        unique_count = len(data) - url_dupes - content_dupes
        # 避免负数
        unique_count = max(unique_count, len(data) - max(url_dupes, content_dupes))

        return {
            "url_duplicates": url_dupes,
            "content_duplicates": content_dupes,
            "near_duplicates": near_dupe_pairs,
            "unique_count": unique_count,
            "total": len(data),
        }

    def _check_quality(self, data: List[Dict]) -> Dict:
        """内容质量检查"""
        too_short = 0
        has_html = 0
        has_garbled = 0
        suspected_truncated = 0

        # 乱码特征：大量替换字符或异常控制字符
        garbled_re = re.compile(r"[\ufffd]{2,}|[\x00-\x08\x0b\x0c\x0e-\x1f]{3,}")

        # 截断特征：末尾不完整的标点或单词
        truncate_endings = re.compile(r"[a-zA-Z\u4e00-\u9fff][,;:，、；：]$|[\u2026]$|\\$")

        for item in data:
            if not isinstance(item, dict):
                continue
            content = (
                item.get("content") or item.get("text")
                or item.get("body") or item.get("description")
                or item.get("summary") or ""
            )
            content = str(content) if content else ""

            if 0 < len(content) < self.min_content_length:
                too_short += 1

            if _HTML_TAG_RE.search(content):
                has_html += 1

            if garbled_re.search(content):
                has_garbled += 1

            # 截断检测：内容较长（>200字）但末尾有截断特征
            if len(content) > 200 and truncate_endings.search(content[-5:]):
                suspected_truncated += 1

        return {
            "too_short": too_short,
            "has_html_residue": has_html,
            "has_garbled": has_garbled,
            "suspected_truncated": suspected_truncated,
            "total": len(data),
        }

    def _check_anti_bot(self, data: List[Dict]) -> Dict:
        """检测疑似反爬/错误页面"""
        suspected = 0
        samples = []

        for item in data:
            if not isinstance(item, dict):
                suspected += 1
                samples.append(str(item)[:80])
                continue

            # 检查标题和内容
            title = str(item.get("title", "")).lower()
            content = str(
                item.get("content") or item.get("text") or item.get("body", "")
            ).lower()
            combined = title + " " + content[:500]

            is_suspect = False
            for kw in _ANTI_BOT_KEYWORDS:
                if kw in combined:
                    is_suspect = True
                    break

            if not is_suspect:
                for pat in _ERROR_PATTERNS:
                    if re.search(pat, combined, re.IGNORECASE):
                        is_suspect = True
                        break

            # 内容极短且没有CJK字符（可能是空白页/拦截页）
            if not is_suspect and len(content) < 30:
                if not _CJK_RE.search(content) and len(content.strip()) < 10:
                    is_suspect = True

            if is_suspect:
                suspected += 1
                if len(samples) < 5:
                    sample_text = title or content[:80] or "(空内容)"
                    samples.append(sample_text[:80])

        return {
            "suspected_pages": suspected,
            "samples": samples,
            "total": len(data),
        }

    def _check_pagination(self, data: List[Dict]) -> Optional[Dict]:
        """检查分页完整性（数据中需要有 page 字段或可从URL推断页码）"""
        page_items = defaultdict(list)

        for item in data:
            if not isinstance(item, dict):
                continue
            page = item.get("page") or item.get("page_num") or item.get("pageNum")
            if page is not None:
                try:
                    page_items[int(page)].append(item)
                    continue
                except (ValueError, TypeError):
                    pass

            # 从URL推断页码
            url = str(item.get("url") or item.get("link", ""))
            m = re.search(r"[?&](?:page|p|pn|pagenum|pg)=(\d+)", url, re.IGNORECASE)
            if not m:
                m = re.search(r"/page/(\d+)", url, re.IGNORECASE)
            if not m:
                m = re.search(r"-(\d+)\.html?", url)
            if m:
                page_items[int(m.group(1))].append(item)

        if not page_items:
            return None

        pages = sorted(page_items.keys())
        if not pages:
            return None

        # 检查断页
        gaps = []
        for i in range(pages[0], pages[-1] + 1):
            if i not in page_items:
                gaps.append(i)

        per_page_counts = {str(p): len(items) for p, items in sorted(page_items.items())}

        return {
            "total_pages": len(pages),
            "first_page": pages[0],
            "last_page": pages[-1],
            "gaps": gaps,
            "per_page_counts": per_page_counts,
        }

    def _diff_data(
        self, baseline: List[Dict], current: List[Dict]
    ) -> Dict[str, Any]:
        """对比两次爬取结果"""
        def _key(item):
            if not isinstance(item, dict):
                return str(item)
            return str(item.get("url") or item.get("id") or item.get("link", ""))

        def _hash(item):
            if not isinstance(item, dict):
                return hashlib.md5(str(item).encode()).hexdigest()
            content = json.dumps(item, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(content.encode()).hexdigest()

        baseline_map = {_key(item): _hash(item) for item in baseline if _key(item)}
        current_map = {_key(item): _hash(item) for item in current if _key(item)}

        added = len(set(current_map.keys()) - set(baseline_map.keys()))
        removed = len(set(baseline_map.keys()) - set(current_map.keys()))

        changed = 0
        for k in set(baseline_map.keys()) & set(current_map.keys()):
            if baseline_map[k] != current_map[k]:
                changed += 1

        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "baseline_count": len(baseline),
            "current_count": len(current),
        }
