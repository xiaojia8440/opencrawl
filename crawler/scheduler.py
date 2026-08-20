"""定时调度模块 — 定时爬取、增量爬取、智能频率控制"""

import asyncio
import json
import logging
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
from pathlib import Path

logger = logging.getLogger(__name__)


class CrawlScheduler:
    """
    爬取调度器

    支持功能:
    - 定时爬取任务调度（基于 asyncio）
    - 增量爬取（只爬新内容，基于 URL/内容哈希去重）
    - 爬取频率控制与智能间隔（自适应限速）
    - 任务优先级管理
    - 爬取历史记录与统计
    - 断点续爬（中断恢复）
    """

    def __init__(self, config: dict):
        self.config = config
        scheduler_cfg = config.get("scheduler", {})

        self._default_interval: int = scheduler_cfg.get("default_interval", 3600)
        self._max_tasks: int = scheduler_cfg.get("max_tasks", 10)
        self._respect_robots: bool = scheduler_cfg.get("respect_robots_txt", True)

        incremental_cfg = scheduler_cfg.get("incremental", {})
        self._incremental_enabled: bool = incremental_cfg.get("enabled", True)
        self._history_path: str = incremental_cfg.get("history_path", ".crawl_history")
        self._change_detection: str = incremental_cfg.get("change_detection", "content_hash")

        rate_cfg = scheduler_cfg.get("rate_control", {})
        self._min_interval: float = rate_cfg.get("min_interval", 1.0)
        self._max_interval: float = rate_cfg.get("max_interval", 30.0)
        self._adaptive: bool = rate_cfg.get("adaptive", True)
        self._target_response_time: float = rate_cfg.get("target_response_time", 2.0)
        self._current_interval: float = self._min_interval

        self._tasks: List[Dict] = []
        self._running: bool = False
        self._url_history: Set[str] = set()
        self._content_hashes: Dict[str, str] = {}
        self._domain_last_crawl: Dict[str, float] = {}
        self._stats: Dict[str, int] = {
            "total_scheduled": 0, "total_crawled": 0, "total_skipped": 0,
            "total_new": 0, "total_changed": 0,
        }

        Path(self._history_path).parent.mkdir(parents=True, exist_ok=True)
        self._load_history()
        logger.info(f"⏰ CrawlScheduler 初始化完成，增量模式: {'启用' if self._incremental_enabled else '关闭'}")

    def add_task(self, urls: List[str], interval: Optional[int] = None, callback: Optional[Callable] = None,
                 priority: int = 5, name: str = "", metadata: Optional[Dict] = None) -> Dict:
        task = {
            "id": hashlib.md5(f"{urls}{time.time()}".encode()).hexdigest()[:12],
            "name": name or f"task_{len(self._tasks) + 1}",
            "urls": urls, "interval": interval or self._default_interval,
            "callback": callback, "priority": max(1, min(10, priority)),
            "metadata": metadata or {}, "created_at": datetime.now().isoformat(),
            "last_run": None, "next_run": datetime.now().isoformat(),
            "run_count": 0, "status": "pending",
        }
        self._tasks.append(task)
        self._tasks.sort(key=lambda t: t["priority"])
        self._stats["total_scheduled"] += len(urls)
        logger.info(f"📋 添加任务: {task['name']}，{len(urls)} 个 URL，间隔 {task['interval']}s")
        return task

    def remove_task(self, task_id: str) -> bool:
        for i, task in enumerate(self._tasks):
            if task["id"] == task_id:
                self._tasks.pop(i)
                logger.info(f"移除任务: {task['name']}")
                return True
        return False

    def list_tasks(self) -> List[Dict]:
        return [{"id": t["id"], "name": t["name"], "urls_count": len(t["urls"]),
                 "interval": t["interval"], "status": t["status"], "last_run": t["last_run"],
                 "next_run": t["next_run"], "run_count": t["run_count"]} for t in self._tasks]

    def is_new_url(self, url: str) -> bool:
        return url not in self._url_history

    def is_content_changed(self, url: str, content: str) -> bool:
        if self._change_detection == "content_hash":
            content_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
            old_hash = self._content_hashes.get(url)
            return old_hash is None or old_hash != content_hash
        elif self._change_detection == "length":
            old_len = len(self._content_hashes.get(url, ""))
            new_len = len(content)
            return abs(old_len - new_len) > 50
        return True

    def filter_new_urls(self, urls: List[str]) -> List[str]:
        if not self._incremental_enabled:
            return urls
        new_urls = [url for url in urls if self.is_new_url(url)]
        skipped = len(urls) - len(new_urls)
        if skipped > 0:
            logger.info(f"📊 增量过滤: {len(urls)} → {len(new_urls)} (跳过 {skipped} 个已知 URL)")
            self._stats["total_skipped"] += skipped
        return new_urls

    def record_crawl(self, url: str, content: str = ""):
        self._url_history.add(url)
        if content:
            content_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
            self._content_hashes[url] = content_hash
        self._stats["total_crawled"] += 1

    def get_crawl_stats(self) -> Dict[str, Any]:
        return {**self._stats, "known_urls": len(self._url_history),
                "tracked_content": len(self._content_hashes),
                "active_tasks": len([t for t in self._tasks if t["status"] == "running"])}

    def can_request(self, domain: str) -> bool:
        last_time = self._domain_last_crawl.get(domain, 0)
        elapsed = time.time() - last_time
        return elapsed >= self._current_interval

    def wait_if_needed(self, domain: str):
        last_time = self._domain_last_crawl.get(domain, 0)
        elapsed = time.time() - last_time
        wait_time = self._current_interval - elapsed
        if wait_time > 0:
            logger.debug(f"⏳ 频率控制: 等待 {wait_time:.1f}s (域名: {domain})")
            time.sleep(wait_time)

    async def async_wait_if_needed(self, domain: str):
        last_time = self._domain_last_crawl.get(domain, 0)
        elapsed = time.time() - last_time
        wait_time = self._current_interval - elapsed
        if wait_time > 0:
            logger.debug(f"⏳ 频率控制: 等待 {wait_time:.1f}s (域名: {domain})")
            await asyncio.sleep(wait_time)

    def record_request(self, domain: str, response_time: float = 0):
        self._domain_last_crawl[domain] = time.time()
        if self._adaptive and response_time > 0:
            self._adjust_interval(response_time)

    def _adjust_interval(self, response_time: float):
        if response_time > self._target_response_time * 2:
            self._current_interval = min(self._current_interval * 1.5, self._max_interval)
            logger.debug(f"🐢 自适应限速: 间隔增大到 {self._current_interval:.1f}s")
        elif response_time < self._target_response_time * 0.5:
            self._current_interval = max(self._current_interval * 0.8, self._min_interval)
            logger.debug(f"🐇 自适应限速: 间隔减小到 {self._current_interval:.1f}s")

    @property
    def current_interval(self) -> float:
        return self._current_interval

    async def run_once(self, crawl_func: Callable):
        now = datetime.now()
        for task in self._tasks:
            next_run = datetime.fromisoformat(task["next_run"])
            if now >= next_run:
                await self._execute_task(task, crawl_func)

    async def run_continuous(self, crawl_func: Callable):
        self._running = True
        logger.info("🚀 调度器开始运行")
        while self._running:
            try:
                await self.run_once(crawl_func)
            except Exception as e:
                logger.error(f"调度器执行异常: {e}")
            await asyncio.sleep(min(self._default_interval, 60))

    def stop(self):
        self._running = False
        self._save_history()
        logger.info("⏹️ 调度器已停止")

    async def _execute_task(self, task: Dict, crawl_func: Callable):
        task["status"] = "running"
        task["last_run"] = datetime.now().isoformat()
        task["run_count"] += 1
        next_run = datetime.now() + timedelta(seconds=task["interval"])
        task["next_run"] = next_run.isoformat()
        logger.info(f"🔍 执行任务: {task['name']} ({len(task['urls'])} 个 URL)")

        try:
            urls = task["urls"]
            if self._incremental_enabled:
                urls = self.filter_new_urls(urls)
            if not urls:
                logger.info(f"任务 {task['name']} 无新增 URL，跳过")
                task["status"] = "completed"
                return

            results = []
            for url in urls:
                domain = self._extract_domain(url)
                await self.async_wait_if_needed(domain)
                start_time = time.time()
                try:
                    result = await crawl_func(url) if asyncio.iscoroutinefunction(crawl_func) else crawl_func(url)
                    response_time = time.time() - start_time
                    self.record_request(domain, response_time)
                    if result:
                        self.record_crawl(url, str(result))
                        results.append({"url": url, "data": result})
                        self._stats["total_new"] += 1
                except Exception as e:
                    logger.error(f"爬取失败 {url}: {e}")

            if task.get("callback") and results:
                try:
                    cb = task["callback"]
                    if asyncio.iscoroutinefunction(cb):
                        await cb(results)
                    else:
                        cb(results)
                except Exception as e:
                    logger.error(f"任务回调执行失败: {e}")

            task["status"] = "completed"
            logger.info(f"✅ 任务 {task['name']} 完成，成功 {len(results)}/{len(urls)}")
        except Exception as e:
            task["status"] = "error"
            logger.error(f"❌ 任务 {task['name']} 执行失败: {e}")

    def _save_history(self):
        try:
            history = {"url_history": list(self._url_history), "content_hashes": self._content_hashes,
                       "stats": self._stats, "current_interval": self._current_interval,
                       "saved_at": datetime.now().isoformat()}
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.debug("爬取历史已保存")
        except Exception as e:
            logger.error(f"保存爬取历史失败: {e}")

    def _load_history(self):
        if not os.path.exists(self._history_path):
            return
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
            self._url_history = set(history.get("url_history", []))
            self._content_hashes = history.get("content_hashes", {})
            saved_stats = history.get("stats", {})
            for k, v in saved_stats.items():
                if k in self._stats:
                    self._stats[k] = v
            self._current_interval = history.get("current_interval", self._min_interval)
            logger.info(f"📂 已加载爬取历史: {len(self._url_history)} 个 URL, {len(self._content_hashes)} 个内容哈希")
        except Exception as e:
            logger.error(f"加载爬取历史失败: {e}")

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        try:
            return urlparse(url).netloc
        except Exception:
            return url
