#!/usr/bin/env python3
"""
OpenCrawl 基础用法示例

演示 Parser / DataCleaner / Storage / Deduplicator / Paginator 的基本使用。
所有 HTTP 请求由使用者自行实现，确保遵守目标网站规则。
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import requests
from crawler import Parser, DataCleaner, Deduplicator, Storage, Paginator


def fetch(url: str, timeout: int = 15) -> str:
    """
    简单的合规 HTTP 获取函数。
    
    使用者应根据目标网站要求：
    - 遵守 robots.txt
    - 设置合理的 User-Agent
    - 添加适当的请求延迟
    - 不进行高频访问
    """
    headers = {
        "User-Agent": "OpenCrawl/1.0 (Educational Project; +https://github.com/yourname/opencrawl)"
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def example_parse():
    """示例1：解析公开网页"""
    print("=" * 60)
    print("示例1：解析网页")
    print("=" * 60)
    
    # 使用 httpbin.org 作为公开测试靶场
    url = "https://httpbin.org/html"
    html = fetch(url)
    
    parser = Parser(config={})
    result = parser.parse(html, url)
    
    print(f"标题: {result['title']}")
    print(f"正文长度: {len(result['text'])} 字符")
    print(f"提取链接: {len(result['links'])} 个")
    print()


def example_clean_and_store():
    """示例2：清洗并存储数据"""
    print("=" * 60)
    print("示例2：数据清洗与存储")
    print("=" * 60)
    
    raw_items = [
        {"title": "  Hello World  ", "content": "<p>这是<b>测试</b>内容</p>", "url": "https://example.com/1"},
        {"title": "Hello World", "content": "<p>这是<b>测试</b>内容</p>", "url": "https://example.com/2"},
        {"title": "另一个页面", "content": "<p>完全不同的内容</p>", "url": "https://example.com/3"},
    ]
    
    # 清洗
    cleaner = DataCleaner(config={"data_cleaner": {}})
    cleaned = [cleaner.clean_item(item) for item in raw_items]
    
    # 去重
    dedup = Deduplicator(config={"dedup": {}})
    unique = dedup.dedup_all(cleaned)
    stats = dedup.get_stats()
    
    print(f"原始: {len(raw_items)} 条")
    print(f"去重后: {len(unique)} 条")
    print(f"去除重复: {stats['total_duplicates']} 条")
    
    # 存储
    storage = Storage(config={"storage": {"default": "json"}})
    output_path = storage.save(unique, "example_output.json")
    print(f"已保存到: {output_path}")
    print()


def example_paginate():
    """示例3：智能翻页（使用 books.toscrape.com 公开靶场）"""
    print("=" * 60)
    print("示例3：智能翻页")
    print("=" * 60)
    print("注：books.toscrape.com 是专门用于爬虫练习的公开靶场")
    print()
    
    paginator = Paginator(
        config={"paginator": {"max_pages": 3, "delay": 2.0}},
        fetcher=None  # 实际使用时传入 fetch 函数
    )
    # 仅演示翻页URL生成逻辑
    urls = paginator.build_url_list(
        template="https://books.toscrape.com/catalogue/page-{page}.html",
        start=1,
        end=3,
    )
    for i, u in enumerate(urls, 1):
        print(f"  第{i}页: {u}")
    print()


if __name__ == "__main__":
    example_parse()
    example_clean_and_store()
    example_paginate()
    print("演示完成！")
