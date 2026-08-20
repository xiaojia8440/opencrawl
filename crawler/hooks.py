"""生命周期Hooks+路由系统

支持功能:
- 生命周期钩子: before_crawl / after_crawl / on_error / on_start / on_finish
- URL路由: 按URL模式匹配不同处理器
- 中间件链: 请求前/后的处理管道
- 统计收集: 自动记录每个阶段耗时和结果

用法:
    hooks = CrawlHooks(config)

    @hooks.before_crawl
    def my_before_crawl(url, context):
        print(f"准备爬取: {url}")
        context["start_time"] = time.time()

    @hooks.after_crawl
    def my_after_crawl(url, result, context):
        elapsed = time.time() - context.get("start_time", 0)
        print(f"完成: {url} ({elapsed:.2f}s)")

    @hooks.route("*.example.com/article/*")
    def handle_article(url, result):
        result["type"] = "article"
        return result

    hooks.run_before_crawl(url, context)
    hooks.run_after_crawl(url, result, context)
    routed = hooks.route_url(url, result)
"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional
from fnmatch import fnmatch

logger = logging.getLogger(__name__)


class CrawlHooks:
    """生命周期钩子 + URL路由系统

    Args:
        config: 配置字典
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        hooks_cfg = self.config.get("hooks", {})
        self.enabled: bool = hooks_cfg.get("enabled", False)

        # 生命周期钩子注册表
        self._before_crawl: List[Callable] = []
        self._after_crawl: List[Callable] = []
        self._on_error: List[Callable] = []
        self._on_start: List[Callable] = []
        self._on_finish: List[Callable] = []

        # URL路由规则: [(pattern, handler)]
        self._routes: List[tuple] = []

        # 中间件链
        self._request_middleware: List[Callable] = []
        self._response_middleware: List[Callable] = []

        # 统计
        self._stats = {
            "total_urls": 0,
            "success": 0,
            "failed": 0,
            "total_time": 0.0,
            "hook_errors": 0,
        }

        if self.enabled:
            logger.info("🪝 生命周期Hooks已启用")

    # ==================== 钩子注册 ====================

    def before_crawl(self, func: Callable) -> Callable:
        """注册 before_crawl 钩子（URL爬取前调用）"""
        self._before_crawl.append(func)
        return func

    def after_crawl(self, func: Callable) -> Callable:
        """注册 after_crawl 钩子（URL爬取成功后调用）"""
        self._after_crawl.append(func)
        return func

    def on_error(self, func: Callable) -> Callable:
        """注册 on_error 钩子（URL爬取失败时调用）"""
        self._on_error.append(func)
        return func

    def on_start(self, func: Callable) -> Callable:
        """注册 on_start 钩子（爬取任务开始时调用）"""
        self._on_start.append(func)
        return func

    def on_finish(self, func: Callable) -> Callable:
        """注册 on_finish 钩子（所有爬取完成后调用）"""
        self._on_finish.append(func)
        return func

    # ==================== 中间件注册 ====================

    def request_middleware(self, func: Callable) -> Callable:
        """注册请求中间件（在发送请求前对headers/params等进行修改）"""
        self._request_middleware.append(func)
        return func

    def response_middleware(self, func: Callable) -> Callable:
        """注册响应中间件（在收到响应后对数据进行预处理）"""
        self._response_middleware.append(func)
        return func

    # ==================== 路由注册 ====================

    def route(self, pattern: str) -> Callable:
        """注册URL路由处理器

        Args:
            pattern: URL匹配模式，支持通配符
                     如 "*.example.com/article/*" 或 "https://news.site.com/*"

        用法:
            @hooks.route("*/article/*")
            def handle_article(url, result):
                result["type"] = "article"
                return result
        """
        def decorator(func: Callable) -> Callable:
            self._routes.append((pattern, func))
            logger.debug(f"🪝 路由注册: {pattern} -> {func.__name__}")
            return func
        return decorator

    def add_route(self, pattern: str, handler: Callable):
        """直接添加路由（非装饰器方式）"""
        self._routes.append((pattern, handler))

    # ==================== 钩子执行 ====================

    def run_before_crawl(self, url: str, context: Optional[Dict] = None) -> Dict:
        """执行所有 before_crawl 钩子"""
        context = context or {}
        for func in self._before_crawl:
            try:
                result = func(url, context)
                if isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                logger.warning(f"🪝 before_crawl 钩子异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1
        return context

    def run_after_crawl(self, url: str, result: Any, context: Optional[Dict] = None) -> Any:
        """执行所有 after_crawl 钩子"""
        context = context or {}
        for func in self._after_crawl:
            try:
                processed = func(url, result, context)
                if processed is not None:
                    result = processed
            except Exception as e:
                logger.warning(f"🪝 after_crawl 钩子异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1
        return result

    def run_on_error(self, url: str, error: Exception, context: Optional[Dict] = None):
        """执行所有 on_error 钩子"""
        context = context or {}
        for func in self._on_error:
            try:
                func(url, error, context)
            except Exception as e:
                logger.warning(f"🪝 on_error 钩子异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1

    def run_on_start(self, urls: List[str]):
        """执行所有 on_start 钩子"""
        for func in self._on_start:
            try:
                func(urls)
            except Exception as e:
                logger.warning(f"🪝 on_start 钩子异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1

    def run_on_finish(self, stats: Dict):
        """执行所有 on_finish 钩子"""
        for func in self._on_finish:
            try:
                func(stats)
            except Exception as e:
                logger.warning(f"🪝 on_finish 钩子异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1

    # ==================== 中间件执行 ====================

    def run_request_middleware(self, url: str, headers: Dict, **kwargs) -> Dict:
        """执行请求中间件，返回修改后的headers"""
        for func in self._request_middleware:
            try:
                result = func(url, headers, **kwargs)
                if isinstance(result, dict):
                    headers.update(result)
            except Exception as e:
                logger.warning(f"🪝 请求中间件异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1
        return headers

    def run_response_middleware(self, url: str, response, **kwargs):
        """执行响应中间件，返回处理后的response"""
        for func in self._response_middleware:
            try:
                result = func(url, response, **kwargs)
                if result is not None:
                    response = result
            except Exception as e:
                logger.warning(f"🪝 响应中间件异常: {func.__name__}: {e}")
                self._stats["hook_errors"] += 1
        return response

    # ==================== 路由匹配 ====================

    def route_url(self, url: str, result: Any) -> Any:
        """按URL匹配路由处理器，执行匹配的处理器

        Args:
            url: 被爬取的URL
            result: 爬取结果

        Returns:
            处理后的结果（可能被路由处理器修改）
        """
        for pattern, handler in self._routes:
            if self._match_url(url, pattern):
                try:
                    processed = handler(url, result)
                    if processed is not None:
                        result = processed
                    logger.debug(f"🪝 路由匹配: {url} -> {handler.__name__}")
                except Exception as e:
                    logger.warning(f"🪝 路由处理异常: {handler.__name__}: {e}")
                    self._stats["hook_errors"] += 1
        return result

    def _match_url(self, url: str, pattern: str) -> bool:
        """URL模式匹配（支持通配符和正则）"""
        # 先尝试通配符匹配
        if fnmatch(url, pattern):
            return True
        # 再尝试正则匹配
        try:
            if re.search(pattern, url):
                return True
        except re.error:
            pass
        # 再尝试子串匹配
        if pattern in url:
            return True
        return False

    # ==================== 统计 ====================

    def record_success(self, url: str, elapsed: float):
        """记录成功爬取"""
        self._stats["total_urls"] += 1
        self._stats["success"] += 1
        self._stats["total_time"] += elapsed

    def record_failure(self, url: str, elapsed: float):
        """记录失败爬取"""
        self._stats["total_urls"] += 1
        self._stats["failed"] += 1
        self._stats["total_time"] += elapsed

    def get_stats(self) -> Dict:
        """获取统计"""
        stats = dict(self._stats)
        if stats["total_urls"] > 0:
            stats["success_rate"] = stats["success"] / stats["total_urls"]
            stats["avg_time"] = stats["total_time"] / stats["total_urls"]
        else:
            stats["success_rate"] = 0
            stats["avg_time"] = 0
        return stats
