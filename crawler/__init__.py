"""
OpenCrawl - 合规优先的数据采集与质量保障工具集

模块化 Python 工具集，专注于公开网页数据的合规采集、质量校验与结构化输出。
不内置 HTTP 请求、反爬绕过、验证码识别或代理匿名功能——使用者自带合规 fetcher，
工具集负责解析、清洗、去重、校验和存储。

合规声明:
- 仅供学习研究和合规公开数据采集使用
- 使用者须自行确保遵守目标网站 robots.txt、服务条款和当地法律法规
- 不包含任何反爬绕过、验证码识别、签名逆向或代理匿名功能
- 禁止用于任何未经授权的数据获取或系统入侵
"""

from crawler.parser import Parser
from crawler.ai_extractor import AIExtractor
from crawler.dedup import Deduplicator
from crawler.storage import Storage
from crawler.paginator import Paginator
from crawler.scheduler import CrawlScheduler
from crawler.data_cleaner import DataCleaner
from crawler.exporter import DataExporter
from crawler.incremental import IncrementalTracker
from crawler.list_crawler import ListDetailCrawler
from crawler.request_queue import RequestQueue
from crawler.hooks import CrawlHooks
from crawler.verifier import DataVerifier
from crawler.download_decision import DownloadDecisionTree, ClassifyResult
from crawler.version_parser import parse_python_version, parse_version, VersionInfo

__version__ = "1.0.0"
__author__ = "秋山岚鸢"
__all__ = [
    "Parser",
    "AIExtractor",
    "Deduplicator",
    "Storage",
    "Paginator",
    "CrawlScheduler",
    "DataCleaner",
    "DataExporter",
    "IncrementalTracker",
    "ListDetailCrawler",
    "RequestQueue",
    "CrawlHooks",
    "DataVerifier",
    "DownloadDecisionTree",
    "ClassifyResult",
    "parse_python_version",
    "parse_version",
    "VersionInfo",
]
