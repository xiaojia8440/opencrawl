"""增量采集模块

支持功能:
- 爬取进度持久化（JSON 格式，记录已爬 URL + 内容哈希）
- 断点续爬（下次运行从上次中断处继续）
- 内容去重（URL 去重 + 内容哈希去重）
- 爬取历史记录查询
- 自动清理过期历史

用法:
    tracker = IncrementalTracker(config)
    tracker.load()  # 加载历史
    if tracker.is_visited(url):  # 检查是否已爬
        skip()
    tracker.mark_visited(url, content_hash)  # 标记已爬
    tracker.save()  # 保存进度
"""

import hashlib
import json
import logging
import os
import time
from typing import Dict, Optional, Set
from datetime import datetime

logger = logging.getLogger(__name__)


class IncrementalTracker:
    """增量采集追踪器

    Args:
        config: 配置字典
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        inc_cfg = self.config.get("incremental", {})
        self.enabled: bool = inc_cfg.get("enabled", False)
        self.history_path: str = inc_cfg.get("history_path", ".crawl_history.json")
        self.change_detection: str = inc_cfg.get("change_detection", "content_hash")  # content_hash / length
        self.max_history: int = inc_cfg.get("max_history", 10000)  # 最大历史记录数
        self.expire_days: int = inc_cfg.get("expire_days", 30)  # 历史过期天数

        # 内存中的历史记录
        self._visited: Dict[str, dict] = {}  # url -> {hash, timestamp, title}
        self._content_hashes: Set[str] = set()  # 内容哈希集合（用于内容去重）

        if self.enabled:
            logger.info(f"📊 增量采集已启用，历史文件: {self.history_path}")

    def load(self):
        """加载历史记录"""
        if not self.enabled:
            return

        if not os.path.exists(self.history_path):
            logger.debug("📊 无历史记录文件，从头开始")
            return

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._visited = data.get("visited", {})
            self._content_hashes = set(data.get("content_hashes", []))

            # 清理过期记录
            self._cleanup_expired()

            logger.info(f"📊 加载历史记录: {len(self._visited)} 个URL, {len(self._content_hashes)} 个内容哈希")
        except Exception as e:
            logger.warning(f"📊 加载历史记录失败: {e}，从头开始")

    def save(self):
        """保存历史记录"""
        if not self.enabled:
            return

        try:
            data = {
                "visited": self._visited,
                "content_hashes": list(self._content_hashes),
                "saved_at": datetime.now().isoformat(),
            }
            os.makedirs(os.path.dirname(os.path.abspath(self.history_path)), exist_ok=True)
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"📊 历史记录已保存: {len(self._visited)} 个URL")
        except Exception as e:
            logger.error(f"📊 保存历史记录失败: {e}")

    def is_visited(self, url: str) -> bool:
        """检查 URL 是否已爬取过"""
        if not self.enabled:
            return False
        return url in self._visited

    def is_content_duplicate(self, content: str) -> bool:
        """检查内容是否重复（基于哈希）"""
        if not self.enabled:
            return False
        content_hash = self._compute_hash(content)
        return content_hash in self._content_hashes

    def mark_visited(self, url: str, content: str = "", title: str = ""):
        """标记 URL 为已访问"""
        if not self.enabled:
            return

        content_hash = self._compute_hash(content) if content else ""
        self._visited[url] = {
            "hash": content_hash,
            "timestamp": datetime.now().isoformat(),
            "title": title,
        }
        if content_hash:
            self._content_hashes.add(content_hash)

        # 超过最大记录数时自动清理最旧的
        if len(self._visited) > self.max_history:
            self._cleanup_oldest()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_visited": len(self._visited),
            "total_hashes": len(self._content_hashes),
            "history_file": self.history_path if self.enabled else None,
        }

    def clear(self):
        """清空历史记录"""
        self._visited.clear()
        self._content_hashes.clear()
        if os.path.exists(self.history_path):
            os.remove(self.history_path)
        logger.info("📊 历史记录已清空")

    def _compute_hash(self, content: str) -> str:
        """计算内容哈希"""
        if self.change_detection == "length":
            return f"len:{len(content)}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _cleanup_expired(self):
        """清理过期记录"""
        if self.expire_days <= 0:
            return

        now = time.time()
        expire_seconds = self.expire_days * 86400
        expired_urls = []

        for url, info in self._visited.items():
            try:
                ts = datetime.fromisoformat(info["timestamp"]).timestamp()
                if now - ts > expire_seconds:
                    expired_urls.append(url)
            except (KeyError, ValueError):
                continue

        for url in expired_urls:
            info = self._visited.pop(url, {})
            h = info.get("hash", "")
            self._content_hashes.discard(h)

        if expired_urls:
            logger.info(f"📊 清理过期记录: {len(expired_urls)} 条")

    def _cleanup_oldest(self):
        """清理最旧的记录（超过 max_history 时）"""
        if len(self._visited) <= self.max_history:
            return

        # 按时间排序，删除最旧的
        sorted_urls = sorted(
            self._visited.items(),
            key=lambda x: x[1].get("timestamp", "")
        )
        remove_count = len(self._visited) - self.max_history
        for url, info in sorted_urls[:remove_count]:
            self._visited.pop(url, None)
            self._content_hashes.discard(info.get("hash", ""))

        logger.debug(f"📊 清理最旧记录: {remove_count} 条")
