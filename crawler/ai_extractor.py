"""AI 提取与清洗模块 — 使用 LLM 智能提取、清洗、分析页面内容"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


class AIExtractor:
    """AI 提取器，支持 OpenAI API 和 Ollama 本地模型"""

    def __init__(self, config: dict):
        self.config = config
        ai_cfg = config.get("ai", {})
        self.backend = ai_cfg.get("backend", "openai")
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 AI 客户端（支持环境变量覆盖配置）"""
        ai_cfg = self.config.get("ai", {})

        if self.backend == "openai":
            openai_cfg = ai_cfg.get("openai", {})
            # 环境变量优先覆盖配置文件
            api_key = os.environ.get("OPENAI_API_KEY", "") or openai_cfg.get("api_key", "")
            base_url = os.environ.get("OPENAI_BASE_URL", "") or openai_cfg.get("base_url", None) or None

            if not api_key:
                logger.warning("⚠️ OpenAI API Key 未配置，AI 功能将不可用")

            try:
                from openai import OpenAI
                kwargs = {"api_key": api_key}
                if base_url:
                    kwargs["base_url"] = base_url
                self._client = OpenAI(**kwargs)
                logger.info(f"OpenAI 客户端初始化完成 (model={self._model})")
            except ImportError:
                logger.error("openai 包未安装，请运行 pip install openai")
                self._client = None

        elif self.backend == "ollama":
            ollama_cfg = ai_cfg.get("ollama", {})
            base_url = ollama_cfg.get("base_url", "http://localhost:11434")
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=f"{base_url}/v1",
                    api_key="ollama",
                )
                logger.info(f"Ollama 客户端初始化完成 ({base_url})")
            except ImportError:
                logger.error("openai 包未安装")
                self._client = None

        else:
            logger.error(f"不支持的 AI 后端: {self.backend}")

    @property
    def _model(self) -> str:
        # 环境变量优先
        env_model = os.environ.get("AI_MODEL", "").strip()
        if env_model:
            return env_model
        ai_cfg = self.config.get("ai", {})
        if self.backend == "openai":
            return ai_cfg.get("openai", {}).get("model", "gpt-4o")
        elif self.backend == "ollama":
            return ai_cfg.get("ollama", {}).get("model", "llama3")
        return "gpt-4o"

    @property
    def _temperature(self) -> float:
        ai_cfg = self.config.get("ai", {})
        if self.backend == "openai":
            return ai_cfg.get("openai", {}).get("temperature", 0.3)
        return ai_cfg.get("ollama", {}).get("temperature", 0.3)

    @property
    def _max_tokens(self) -> int:
        ai_cfg = self.config.get("ai", {})
        if self.backend == "openai":
            return ai_cfg.get("openai", {}).get("max_tokens", 4096)
        return ai_cfg.get("ollama", {}).get("max_tokens", 4096)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """调用 LLM"""
        if not self._client:
            logger.error("AI 客户端未初始化")
            return None

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return None

    def extract(self, text: str, url: str) -> Dict[str, Any]:
        """使用 AI 从页面文本中提取结构化数据"""
        system_prompt = """你是一个专业的网页内容提取助手。
请从用户提供的网页文本中提取关键信息，返回 JSON 格式。

提取内容包括：
1. title: 文章标题
2. summary: 文章摘要（100字以内）
3. author: 作者
4. publish_date: 发布日期
5. main_content: 主要内容
6. key_points: 关键要点列表
7. categories: 文章分类标签列表
8. entities: 提到的关键实体

请只返回 JSON，不要包含其他文字。"""
        user_prompt = f"URL: {url}\n\n网页内容:\n{text[:8000]}"

        result = self._call_llm(system_prompt, user_prompt)
        if not result:
            return {"url": url, "error": "AI 提取失败"}

        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
            result = result.rsplit("```", 1)[0]
        result = result.strip()

        try:
            parsed = json.loads(result)
            parsed["url"] = url
            return parsed
        except json.JSONDecodeError:
            logger.warning("AI 返回非 JSON 格式，返回原始文本")
            return {"url": url, "raw_extraction": result}

    def extract_batch(self, items: List[Dict]) -> List[Dict]:
        """批量提取 - AI结果与原始数据合并，不丢失原始爬取内容"""
        results = []
        for item in items:
            try:
                text = item.get("text", "")
                url = item.get("url", "")
                ai_result = self.extract(text, url)
                # 合并：原始数据为基础，AI提取字段叠加上去
                merged = dict(item)  # 保留原始数据
                # 添加AI提取的字段（不覆盖原始的非空字段）
                for key, value in ai_result.items():
                    if key == "url":
                        continue  # url已在原始数据中
                    if key == "main_content" and merged.get("text"):
                        merged["ai_main_content"] = value  # 避免和原始text冲突
                    else:
                        merged[f"ai_{key}"] = value
                results.append(merged)
            except Exception as e:
                logger.error(f"AI 提取异常: {item.get('url', '')} - {e}")
                # 出错时保留原始数据
                merged = dict(item)
                merged["ai_error"] = str(e)
                results.append(merged)
        return results

    def clean_data(self, data: List[Dict]) -> List[Dict]:
        """使用 AI 清洗数据"""
        if not data:
            return data

        system_prompt = """你是一个数据清洗助手。
请对用户提供的爬虫数据进行清洗和标准化处理，返回 JSON 数组。

清洗规则：
1. 去除明显无意义的内容
2. 标准化日期格式为 YYYY-MM-DD
3. 去除特殊字符和多余空白
4. 保留所有原始字段，不要删除或遗漏任何字段
5. 对 title、text、content、summary 等文本字段进行优化
6. 对 links、images、metadata 等非文本字段原样保留
7. 如果某个文本字段内容为空或无意义，保留原值不要删除

请只返回 JSON 数组，每个条目必须包含原始的所有字段。"""

        user_prompt = f"请清洗以下数据:\n{json.dumps(data, ensure_ascii=False)[:8000]}"

        result = self._call_llm(system_prompt, user_prompt)
        if not result:
            return data

        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
            result = result.rsplit("```", 1)[0]
        result = result.strip()

        try:
            cleaned = json.loads(result)
            if isinstance(cleaned, list):
                # 合并：以原始数据为基础，用AI清洗结果覆盖文本字段
                merged_results = []
                for i, original in enumerate(data):
                    if i < len(cleaned):
                        merged = dict(original)  # 保留所有原始字段
                        ai_cleaned = cleaned[i]
                        # 只更新文本类字段
                        for key in ['title', 'text', 'content', 'summary',
                                    'ai_summary', 'ai_main_content', 'ai_title']:
                            if key in ai_cleaned and ai_cleaned[key]:
                                merged[key] = ai_cleaned[key]
                        merged_results.append(merged)
                    else:
                        merged_results.append(original)
                logger.info(f"AI 清洗完成: {len(data)} → {len(merged_results)} 条")
                return merged_results
            return data
        except json.JSONDecodeError:
            logger.warning("AI 清洗返回非 JSON 格式")
            return data

    def analyze_structure(self, html: str) -> Dict[str, Any]:
        """使用 AI 分析页面结构"""
        system_prompt = """你是一个网页结构分析专家。
请分析以下 HTML 结构，识别主要内容区域和提取规则。

返回 JSON 格式：
{
    "content_selectors": ["推荐的内容CSS选择器"],
    "list_selectors": ["推荐列表CSS选择器"],
    "title_selector": "标题选择器",
    "summary_selector": "摘要选择器",
    "date_selector": "日期选择器",
    "author_selector": "作者选择器",
    "page_type": "article | list | portal | other",
    "encoding_hints": ["编码提示"]
}

请只返回 JSON。"""

        html_preview = html[:15000]
        user_prompt = f"请分析以下 HTML 结构:\n\n{html_preview}"

        result = self._call_llm(system_prompt, user_prompt)
        if not result:
            return {"page_type": "unknown"}

        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
            result = result.rsplit("```", 1)[0]
        result = result.strip()

        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"page_type": "unknown", "raw": result}
