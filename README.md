# OpenCrawl — 合规优先的数据采集与质量保障工具集

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

> 你不需要又一个"什么都能爬"的框架。你需要的是**敢写进论文附录、敢提交给法务审查、敢放进生产管线**的数据采集工具。

OpenCrawl 是一个模块化的 Python 工具集，专注于**公开网页数据的合规采集、质量校验与结构化输出**。
它不替你发 HTTP 请求——你自带合规的 fetcher，它负责把数据**解析对、清洗干净、去重、校验、存好**。

## 🎯 这是什么，不是什么

| | OpenCrawl | 传统爬虫框架 |
|---|---|---|
| **定位** | 数据采集后处理 + 质量保障工具集 | 端到端爬取框架 |
| **HTTP 请求** | 你自带（requests / httpx / 合规客户端） | 框架内置 |
| **反爬/验证码** | ❌ 不含，也不计划包含 | 多数内置或提供插件 |
| **代理池/指纹** | ❌ 不含 | 部分内置 |
| **数据质量校验** | ✅ 完整性评分 + 去重 + diff | 通常只做管道存储 |
| **合规审查层** | ✅ URL 分类门禁 + robots 提示 | 无 |
| **LLM 提取** | ✅ OpenAI 兼容 / Ollama 本地 | 部分支持 |
| **模块独立使用** | ✅ 每个模块可单独 import | 通常需框架上下文 |
| **JS 渲染** | ❌ 由你的 fetcher 决定 | 部分内置 Playwright |

**适合谁：**
- 📚 学术研究者：需要可复现、合规的数据采集流程，要写进论文方法论
- 📰 数据新闻记者：采集公开信息，需要数据来源可追溯
- 🏢 企业合规团队：有法务审查要求，不能引入灰产工具链
- 🤖 RAG / AI 管线工程师：需要干净、去重、经过质量校验的训练/检索语料
- 🔧 工具开发者：需要一套可独立拼装的解析/清洗/存储组件

**不适合谁：**
- 需要绕过反爬、验证码、登录限制的场景——请用别的工具
- 期待开箱即用地"输入一个网址就爬完整站"的用户——目前没有 CLI 入口

## ⚠️ 合规声明

- 本工具集**仅**用于采集**公开可见**的网页内容
- 使用者须自行确保遵守目标网站 `robots.txt`、服务条款和当地法律法规
- 本工具集**不包含且不会集成**任何反爬绕过、验证码识别、签名逆向、代理匿名或加密破解功能
- `download_decision` 模块提供 URL 分类门禁，帮助使用者在采集前过滤不应访问的链接
- 禁止用于任何未经授权的数据获取、系统入侵或干扰第三方服务正常运行的行为
- 使用者因不当使用产生的法律责任由其自行承担

## ✨ 功能模块

### 数据质量管线

| 模块 | 功能 |
|------|------|
| `data_cleaner` | HTML 清理、编码修正、字段格式化、**数据质量评分** |
| `verifier` | 完整性校验、字段覆盖率报告、**重复检测、采集 diff** |
| `dedup` | URL 去重、MD5 精确去重、**SimHash 近似去重**、感知哈希图片去重 |

### 解析与提取

| 模块 | 功能 |
|------|------|
| `parser` | HTML 解析、链接/图片/表格/Schema.org/OpenGraph 提取 |
| `ai_extractor` | LLM 智能内容结构化（OpenAI 兼容 API / Ollama 本地模型），API Key 经环境变量传入 |

### 采集调度

| 模块 | 功能 |
|------|------|
| `request_queue` | 持久化 URL 队列，优先级 / 重试 / 断点恢复 |
| `scheduler` | 定时调度、增量爬取、**自适应限速**、任务优先级 |
| `incremental` | 断点续爬、URL 去重、内容变更检测、历史管理 |
| `paginator` | 智能翻页（URL 参数 / 路径 / 下一页按钮 / 加载更多） |
| `list_crawler` | 列表页 → 详情页父子爬取模式 |
| `download_decision` | **URL 分类门禁**：直链 / 中间页 / 网盘 / 下载页 / 黑名单，采集前自动拦截 |

### 存储与导出

| 模块 | 功能 |
|------|------|
| `storage` | JSON / CSV / SQLite / Markdown / TXT / DOCX 多格式存储，自动格式选择 |
| `exporter` | CSV / JSON / JSONL / Excel / SQLite / Markdown 导出，支持分批大数据量 |

### 扩展机制

| 模块 | 功能 |
|------|------|
| `hooks` | 生命周期钩子、URL 路由、中间件链 |
| `version_parser` | 通用版本号解析与数值化比较 |

## 🚀 快速开始

### 安装

```bash
pip install -r requirements.txt
```

### 设计理念：自带 Fetcher

OpenCrawl 不替你发 HTTP 请求。你用自己信任的客户端（`requests`、`httpx`、或公司内部的合规 HTTP 库）获取页面内容，再交给 OpenCrawl 处理。

这不是缺失——这是**刻意设计**：
- 请求行为完全由你控制，方便通过法务/安全审查
- 不会因为框架内置了指纹模拟、代理轮换等功能而引入合规风险
- 你可以精确控制 User-Agent、请求间隔、重试策略、缓存策略

```python
import requests

def fetch(url: str, timeout: int = 15) -> str:
    """你的合规 HTTP 获取函数"""
    headers = {
        "User-Agent": "YourBot/1.0 (contact: your-email@example.com)"
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text
```

### 三步完成一次合规采集

```python
from crawler import Parser, DataCleaner, Storage, Deduplicator, DataVerifier

# 1. 用你自己的 fetcher 获取页面
html = fetch("https://example.com/public-page")

# 2. 解析 + 清洗 + 去重
parser = Parser(config={})
result = parser.parse(html, url="https://example.com/public-page")

cleaner = DataCleaner(config={"data_cleaner": {}})
cleaned = cleaner.clean_item(result)

dedup = Deduplicator(config={"dedup": {}})
if dedup.is_duplicate_content(cleaned.get("text", "")):
    print("重复数据，跳过")
else:
    dedup.mark_seen(cleaned.get("url", ""), cleaned.get("text", ""))

    # 3. 存储
    storage = Storage(config={"storage": {"default": "json"}})
    storage.save([cleaned], "output.json")
```

> 数据校验通过 `DataVerifier.verify(data_path)` 对**已保存的结果文件**（JSON/JSONL/CSV）
> 进行完整性评分、重复检测、反爬页面识别和分页对账，并可与上一次结果做 diff：
>
> ```python
> verifier = DataVerifier(config={"verifier": {}})
> report = verifier.verify("output.json", baseline_path="previous.json")
> verifier.print_report(report)
> # report["status"]: ok / warning / error
> # report["summary"], report["details"]["quality"] 等字段供程序判断
> ```

### URL 门禁：采集前先审查

```python
from crawler import DownloadDecisionTree

# config 段为 "downloader"（同时兼容旧版 "file_download"）
decision = DownloadDecisionTree(config={
    "downloader": {
        "blocked_domains": ["admin.example.com", "private-site.net"],
        "cross_domain": "allow_list",
        "cross_domain_allow": ["example.com", "public-data.org"],
    }
})

url = "https://private-site.net/api/users"
result = decision.classify_url(url)
# result.category == "blocked" —— 自动拦截，不会发起请求
# result.category: direct_file / html_middle / netdisk_share / download_page / blocked
# result.confidence、result.reason 给出判断依据
if result.category == "blocked":
    print(f"跳过 {url}: {result.reason}")
```

### AI 结构化提取

```python
from crawler import AIExtractor

extractor = AIExtractor(config={
    "ai": {
        "backend": "openai",
        "openai": {
            "api_key": "",        # 留空时从环境变量 OPENAI_API_KEY 读取
            "base_url": "",       # 可选：自定义 API 端点（可指向 Ollama 等）
            "model": "gpt-4o",    # 或通过环境变量 AI_MODEL 设置
        }
    }
})

# extract(text, url) 返回结构化字典（title/summary/author/main_content 等）
result = extractor.extract(page_text, url="https://example.com/page")
print(result.get("title"), result.get("summary"))
```

### 增量爬取与断点续爬

```python
from crawler import IncrementalTracker, RequestQueue

# RequestQueue 默认不启用持久化，需显式 enabled=True
queue = RequestQueue(config={
    "request_queue": {"enabled": True, "queue_path": ".request_queue.json"}
})
queue.load()
queue.add("https://example.com/page1")

tracker = IncrementalTracker(config={
    "incremental": {"enabled": True, "history_path": ".crawl_history.json"}
})
tracker.load()

while not queue.is_empty():
    url = queue.next()
    if tracker.is_visited(url):
        continue
    html = fetch(url)
    # ... 解析处理 ...
    tracker.mark_visited(url, content=html, title="...")
    tracker.save()
    queue.mark_done(url)
    queue.save()
```

## 📁 项目结构

```
opencrawl/
├── crawler/
│   ├── __init__.py
│   ├── parser.py              # HTML 解析
│   ├── ai_extractor.py        # AI 内容提取
│   ├── data_cleaner.py        # 数据清洗 + 质量评分
│   ├── verifier.py            # 数据完整性校验 + diff
│   ├── dedup.py               # 精确/近似/感知哈希去重
│   ├── storage.py             # 多格式存储
│   ├── exporter.py            # 多格式导出
│   ├── request_queue.py       # 持久化 URL 队列
│   ├── scheduler.py           # 调度 + 自适应限速
│   ├── incremental.py         # 断点续爬 + 变更检测
│   ├── paginator.py           # 智能翻页
│   ├── list_crawler.py        # 列表→详情爬取
│   ├── download_decision.py   # URL 分类门禁
│   ├── hooks.py               # 生命周期钩子
│   └── version_parser.py      # 版本号解析
├── examples/
│   └── basic_usage.py
├── requirements.txt
├── LICENSE
└── README.md
```

## 🔧 配置说明

各模块通过统一的 `config` 字典配置，详见各模块文档字符串。
AI 功能支持通过环境变量配置：
- `OPENAI_API_KEY`：API 密钥
- `OPENAI_BASE_URL`：自定义 API 端点（可指向 Ollama 等本地服务）
- `AI_MODEL`：模型名称

## 🗺️ 路线图

- [ ] `SimpleFetcher`：一个可选的、纯 `requests` 的基础 fetcher（不含任何反爬逻辑，仅做重试 + 限速 + robots.txt 检查）
- [ ] 命令行入口 `opencrawl`：从配置文件启动一次采集任务
- [ ] `config.example.yaml`：完整配置模板
- [ ] 数据血缘追踪：每条采集数据记录来源 URL、采集时间、采集者标识
- [ ] 更多导出格式：Parquet、JSON Schema 校验

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件。
