"""去重模块 — 基于 URL、内容哈希、SimHash 近似去重和感知哈希图片去重"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_IMAGEHASH_AVAILABLE = False
try:
    import imagehash
    from PIL import Image
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    pass


class SimHash:
    """SimHash 文本近似去重实现"""

    def __init__(self, hash_bits: int = 64):
        self.hash_bits = hash_bits

    def compute(self, text: str) -> int:
        """计算文本的 SimHash 值"""
        tokens = self._tokenize(text)
        if not tokens:
            return 0
        vector = [0] * self.hash_bits
        for token in tokens:
            token_hash = self._hash_token(token)
            for i in range(self.hash_bits):
                if token_hash & (1 << i):
                    vector[i] += 1
                else:
                    vector[i] -= 1
        simhash = 0
        for i in range(self.hash_bits):
            if vector[i] > 0:
                simhash |= (1 << i)
        return simhash

    def distance(self, hash1: int, hash2: int) -> int:
        xor = hash1 ^ hash2
        return bin(xor).count("1")

    def similarity(self, hash1: int, hash2: int) -> float:
        dist = self.distance(hash1, hash2)
        return 1.0 - (dist / self.hash_bits)

    def _tokenize(self, text: str) -> List[str]:
        text = re.sub(r'\s+', ' ', text.strip())
        if not text:
            return []
        tokens = []
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        for segment in chinese_chars:
            for i in range(len(segment) - 1):
                tokens.append(segment[i:i + 2])
            if len(segment) == 1:
                tokens.append(segment)
        english_words = re.findall(r'[a-zA-Z0-9]+', text)
        tokens.extend(w.lower() for w in english_words if len(w) > 1)
        return tokens if tokens else [text[:10]]

    def _hash_token(self, token: str) -> int:
        h = hashlib.md5(token.encode("utf-8")).hexdigest()
        return int(h[:16], 16)


class Deduplicator:
    """数据去重器，支持多层去重策略"""

    def __init__(self, config: dict):
        self.config = config
        dedup_cfg = config.get("dedup", {})
        self._urls_seen: Set[str] = set()
        self._content_hashes: Set[str] = set()
        simhash_cfg = dedup_cfg.get("simhash", {})
        self.simhash_enabled: bool = simhash_cfg.get("enabled", False)
        self.simhash_threshold: int = simhash_cfg.get("threshold", 10)
        self._simhash_values: List[int] = []
        self._simhash_texts: List[str] = []
        phash_cfg = dedup_cfg.get("perceptual", {})
        self.phash_enabled: bool = phash_cfg.get("enabled", False)
        self.phash_threshold: int = phash_cfg.get("threshold", 8)
        self._phash_values: List[str] = []
        self._simhash = SimHash() if self.simhash_enabled else None
        self.url_duplicates: int = 0
        self.content_duplicates: int = 0
        self.simhash_duplicates: int = 0
        self.phash_duplicates: int = 0

    def reset(self):
        self._urls_seen.clear()
        self._content_hashes.clear()
        self._simhash_values.clear()
        self._simhash_texts.clear()
        self._phash_values.clear()
        self.url_duplicates = 0
        self.content_duplicates = 0
        self.simhash_duplicates = 0
        self.phash_duplicates = 0

    def _content_hash(self, text: str) -> str:
        normalized = "".join(text.split())
        normalized = "".join(c for c in normalized if c.isalnum())
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    def is_duplicate_url(self, url: str) -> bool:
        return url in self._urls_seen

    def is_duplicate_content(self, text: str) -> bool:
        h = self._content_hash(text)
        return h in self._content_hashes

    def is_duplicate_simhash(self, text: str) -> bool:
        if not self.simhash_enabled or self._simhash is None:
            return False
        current_hash = self._simhash.compute(text)
        for existing_hash in self._simhash_values:
            distance = self._simhash.distance(current_hash, existing_hash)
            if distance <= self.simhash_threshold:
                return True
        return False

    def is_duplicate_image(self, image_path: str) -> bool:
        if not self.phash_enabled or not _IMAGEHASH_AVAILABLE:
            return False
        try:
            img = Image.open(image_path)
            ph = str(imagehash.phash(img))
            for existing_ph in self._phash_values:
                distance = abs(
                    imagehash.hex_to_hash(ph) - imagehash.hex_to_hash(existing_ph)
                )
                if distance <= self.phash_threshold:
                    return True
            self._phash_values.append(ph)
            return False
        except Exception as e:
            logger.debug(f"图片哈希计算失败: {image_path} - {e}")
            return False

    def mark_seen(self, url: str, text: str):
        self._urls_seen.add(url)
        self._content_hashes.add(self._content_hash(text))
        if self.simhash_enabled and self._simhash:
            self._simhash_values.append(self._simhash.compute(text))
            self._simhash_texts.append(text[:100])

    def dedup_by_url(self, items: List[Dict]) -> List[Dict]:
        seen: Set[str] = set()
        result = []
        for item in items:
            url = item.get("url", "")
            if url and url in seen:
                self.url_duplicates += 1
                continue
            if url:
                seen.add(url)
            result.append(item)
        logger.info(f"🔗 URL 去重: {len(items)} → {len(result)} (+{self.url_duplicates} 重复)")
        return result

    def dedup_by_content(self, items: List[Dict], text_field: str = "text") -> List[Dict]:
        seen_hashes: Set[str] = set()
        result = []
        for item in items:
            text = item.get(text_field, "")
            if not text:
                result.append(item)
                continue
            h = self._content_hash(text)
            if h in seen_hashes:
                self.content_duplicates += 1
                continue
            seen_hashes.add(h)
            result.append(item)
        logger.info(f"📝 内容哈希去重: {len(items)} → {len(result)} (+{self.content_duplicates} 重复)")
        return result

    def dedup_by_simhash(self, items: List[Dict], text_field: str = "text") -> List[Dict]:
        if not self.simhash_enabled:
            logger.debug("SimHash 去重未启用，跳过")
            return items
        result = []
        for item in items:
            text = item.get(text_field, "")
            if not text or len(text) < 20:
                result.append(item)
                continue
            if self.is_duplicate_simhash(text):
                self.simhash_duplicates += 1
                continue
            if self._simhash:
                self._simhash_values.append(self._simhash.compute(text))
                self._simhash_texts.append(text[:100])
            result.append(item)
        logger.info(
            f"🧠 SimHash 近似去重: {len(items)} → {len(result)} "
            f"(+{self.simhash_duplicates} 近似重复, 阈值={self.simhash_threshold})"
        )
        return result

    def dedup_all(self, items: List[Dict], text_field: str = "text") -> List[Dict]:
        result = self.dedup_by_url(items)
        result = self.dedup_by_content(result, text_field)
        if self.simhash_enabled:
            result = self.dedup_by_simhash(result, text_field)
        return result

    def get_stats(self) -> Dict[str, int]:
        return {
            "url_duplicates": self.url_duplicates,
            "content_duplicates": self.content_duplicates,
            "simhash_duplicates": self.simhash_duplicates,
            "phash_duplicates": self.phash_duplicates,
            "total_duplicates": (
                self.url_duplicates + self.content_duplicates +
                self.simhash_duplicates + self.phash_duplicates
            ),
            "urls_seen": len(self._urls_seen),
            "content_hashes_seen": len(self._content_hashes),
            "simhash_cache_size": len(self._simhash_values),
            "phash_cache_size": len(self._phash_values),
        }
