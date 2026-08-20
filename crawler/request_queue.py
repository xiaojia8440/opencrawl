"""RequestQueue 持久化URL队列

支持功能:
- URL队列持久化（JSON格式，重启后恢复）
- URL去重（已处理的URL不重复入队）
- 优先级队列（高优先级URL先处理）
- 重试管理（失败URL自动重试，最多N次）
- 队列状态查询（待处理/已完成/失败数量）
- 断点续爬（从上次中断处继续）

用法:
    rq = RequestQueue(config)
    rq.load()
    rq.add("https://example.com/page1")
    rq.add(["https://example.com/page2", "https://example.com/page3"], priority=1)
    url = rq.next()  # 取下一个待处理URL
    rq.mark_done(url, result_hash="abc123")
    rq.mark_failed(url, reason="timeout")
    rq.save()
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Set
from datetime import datetime

logger = logging.getLogger(__name__)


class RequestQueue:
    """持久化URL队列

    Args:
        config: 配置字典
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        rq_cfg = self.config.get("request_queue", {})
        self.enabled: bool = rq_cfg.get("enabled", False)
        self.queue_path: str = rq_cfg.get("queue_path", ".request_queue.json")
        self.max_retries: int = rq_cfg.get("max_retries", 3)
        self.max_queue_size: int = rq_cfg.get("max_queue_size", 50000)

        # 队列状态
        self._pending: List[Dict] = []       # [{url, priority, retries, added_at}]
        self._processing: Dict[str, Dict] = {}  # {url: {started_at, retries}}
        self._done: Dict[str, Dict] = {}     # {url: {completed_at, result_hash, title}}
        self._failed: Dict[str, Dict] = {}   # {url: {failed_at, reason, retries}}
        self._all_urls: Set[str] = set()     # 所有曾入队的URL（用于去重）

        if self.enabled:
            logger.info(f"📋 RequestQueue 已启用，队列文件: {self.queue_path}")

    def load(self):
        """从文件加载队列状态"""
        if not self.enabled or not os.path.exists(self.queue_path):
            return

        try:
            with open(self.queue_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._pending = data.get("pending", [])
            self._done = data.get("done", {})
            self._failed = data.get("failed", {})
            self._all_urls = set(data.get("all_urls", []))

            # 处理中的URL重新入队（断点恢复）
            processing = data.get("processing", {})
            recovered = 0
            for url, info in processing.items():
                retries = info.get("retries", 0)
                self._pending.append({
                    "url": url,
                    "priority": 0,
                    "retries": retries,
                    "added_at": info.get("started_at", ""),
                })
                recovered += 1

            self._processing = {}

            logger.info(f"📋 队列已加载: 待处理 {len(self._pending)}, "
                        f"已完成 {len(self._done)}, 失败 {len(self._failed)}, "
                        f"恢复中断 {recovered}")

        except Exception as e:
            logger.warning(f"📋 加载队列失败: {e}，从头开始")

    def save(self):
        """保存队列状态到文件"""
        if not self.enabled:
            return

        try:
            data = {
                "pending": self._pending,
                "processing": self._processing,
                "done": self._done,
                "failed": self._failed,
                "all_urls": list(self._all_urls),
                "saved_at": datetime.now().isoformat(),
            }
            os.makedirs(os.path.dirname(os.path.abspath(self.queue_path)), exist_ok=True)
            with open(self.queue_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"📋 队列已保存: 待处理 {len(self._pending)}, 已完成 {len(self._done)}")
        except Exception as e:
            logger.error(f"📋 保存队列失败: {e}")

    def add(self, url, priority: int = 0) -> bool:
        """添加URL到队列

        Args:
            url: URL字符串或列表
            priority: 优先级（数字越小越先处理，0=普通，-1=高优先级）

        Returns:
            是否成功添加（已存在的URL返回False）
        """
        if isinstance(url, list):
            added = 0
            for u in url:
                if self.add(u, priority):
                    added += 1
            return added > 0

        if url in self._all_urls:
            return False

        if len(self._pending) >= self.max_queue_size:
            logger.warning(f"📋 队列已满 ({self.max_queue_size})，URL未入队: {url}")
            return False

        self._pending.append({
            "url": url,
            "priority": priority,
            "retries": 0,
            "added_at": datetime.now().isoformat(),
        })
        self._all_urls.add(url)

        # 按优先级排序（priority小的在前）
        self._pending.sort(key=lambda x: (x["priority"], x["added_at"]))
        return True

    def next(self) -> Optional[str]:
        """取下一个待处理URL"""
        if not self._pending:
            return None

        item = self._pending.pop(0)
        url = item["url"]
        self._processing[url] = {
            "started_at": datetime.now().isoformat(),
            "retries": item.get("retries", 0),
        }
        return url

    def mark_done(self, url: str, result_hash: str = "", title: str = ""):
        """标记URL为已完成"""
        if url in self._processing:
            self._processing.pop(url)
        self._done[url] = {
            "completed_at": datetime.now().isoformat(),
            "result_hash": result_hash,
            "title": title,
        }

    def mark_failed(self, url: str, reason: str = ""):
        """标记URL为失败，自动重试（如果未超过最大重试次数）"""
        retries = 0
        if url in self._processing:
            retries = self._processing[url].get("retries", 0)
            self._processing.pop(url)

        if retries < self.max_retries:
            # 重新入队等待重试
            self._pending.append({
                "url": url,
                "priority": retries + 1,  # 重试的优先级降低
                "retries": retries + 1,
                "added_at": datetime.now().isoformat(),
            })
            logger.debug(f"📋 URL重试 ({retries + 1}/{self.max_retries}): {url} - {reason}")
        else:
            # 超过重试上限，标记为失败
            self._failed[url] = {
                "failed_at": datetime.now().isoformat(),
                "reason": reason,
                "retries": retries,
            }
            logger.warning(f"📋 URL失败（超过重试上限）: {url} - {reason}")

    def get_stats(self) -> Dict:
        """获取队列统计"""
        return {
            "pending": len(self._pending),
            "processing": len(self._processing),
            "done": len(self._done),
            "failed": len(self._failed),
            "total": len(self._all_urls),
        }

    def is_empty(self) -> bool:
        """队列是否为空"""
        return len(self._pending) == 0 and len(self._processing) == 0

    def get_failed_urls(self) -> List[str]:
        """获取所有失败的URL"""
        return list(self._failed.keys())

    def get_done_urls(self) -> List[str]:
        """获取所有已完成的URL"""
        return list(self._done.keys())

    def clear(self):
        """清空队列"""
        self._pending.clear()
        self._processing.clear()
        self._done.clear()
        self._failed.clear()
        self._all_urls.clear()

    def requeue_failed(self) -> int:
        """将所有失败的URL重新入队"""
        count = 0
        for url in list(self._failed.keys()):
            info = self._failed.pop(url)
            self._pending.append({
                "url": url,
                "priority": 0,
                "retries": 0,
                "added_at": datetime.now().isoformat(),
            })
            count += 1
        logger.info(f"📋 重新入队 {count} 个失败URL")
        return count
