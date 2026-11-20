"""向量化引擎 — 语义相似度分析 (Phase 3)"""

from typing import Any
import json
from datetime import datetime, timedelta


class VectorEmbedding:
    """向量嵌入引擎 — 使用轻量级语义相似度模型"""

    def __init__(self, model_name: str = "sentence-transformers/distilbert-base-multilingual-mean-tokens"):
        """
        初始化向量化引擎
        
        Args:
            model_name: 模型名称（国际化版本，支持中文）
        """
        self.model_name = model_name
        self.embedding_dim = 768  # DistilBERT 向量维度
        self.model = None
        self._load_model()

    def _load_model(self):
        """延迟加载模型（避免启动时阻塞）"""
        try:
            from sentence_transformers import SentenceTransformer
            # 轻量级模型，推荐生产环境使用
            self.model = SentenceTransformer(self.model_name)
            print(f"✓ 向量模型加载成功: {self.model_name}")
        except Exception as e:
            print(f"⚠️ 向量模型加载失败，将退回到 TF-IDF 模式: {e}")
            self.model = None

    def embed_text(self, text: str) -> list[float]:
        """
        将文本转换为向量
        
        Args:
            text: 输入文本
            
        Returns:
            向量列表（浮点数）
        """
        if not text or len(text.strip()) == 0:
            return [0.0] * self.embedding_dim

        try:
            if self.model is None:
                self._load_model()
            
            if self.model:
                # 使用预训练模型生成向量
                embedding = self.model.encode(text, convert_to_tensor=False)
                return embedding.tolist()
            else:
                # 退回到简单的哈希向量
                return self._fallback_embedding(text)
        except Exception as e:
            print(f"向量生成失败: {e}")
            return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> list[float]:
        """
        备用向量生成（基于哈希）
        
        Args:
            text: 输入文本
            
        Returns:
            向量列表
        """
        # 简单的基于字符哈希的向量生成
        embedding = []
        for i in range(self.embedding_dim):
            hash_val = hash(text + str(i)) % 1000
            embedding.append((hash_val - 500) / 500.0)  # 归一化到 [-1, 1]
        return embedding

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """
        批量将文本转换为向量
        
        Args:
            texts: 文本列表
            batch_size: 批处理大小
            
        Returns:
            向量列表
        """
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            for text in batch:
                embeddings.append(self.embed_text(text))
        return embeddings

    def semantic_similarity(self, text1: str, text2: str) -> float:
        """
        计算两个文本的语义相似度（余弦相似度）
        
        Args:
            text1: 第一个文本
            text2: 第二个文本
            
        Returns:
            相似度分数（0-1）
        """
        if not text1 or not text2:
            return 0.0

        try:
            if self.model is None:
                self._load_model()
            
            if self.model:
                # 使用预训练模型计算相似度
                similarity = self.model.similarity(
                    self.model.encode(text1, convert_to_tensor=True),
                    self.model.encode(text2, convert_to_tensor=True),
                )
                return float(similarity[0][0])
            else:
                # 退回到向量方式的余弦相似度
                return self._cosine_similarity(
                    self.embed_text(text1), self.embed_text(text2)
                )
        except Exception as e:
            print(f"语义相似度计算失败: {e}")
            return 0.0

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        计算两个向量的余弦相似度
        
        Args:
            vec1: 向量 1
            vec2: 向量 2
            
        Returns:
            余弦相似度（0-1）
        """
        if len(vec1) != len(vec2) or len(vec1) == 0:
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a**2 for a in vec1) ** 0.5
        norm2 = sum(b**2 for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)
        return max(0.0, min(similarity, 1.0))  # 限制在 [0, 1]

    def find_similar_chunks(
        self,
        query_text: str,
        candidate_texts: list[str],
        threshold: float = 0.5,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        查找与查询文本相似的文本块
        
        Args:
            query_text: 查询文本
            candidate_texts: 候选文本列表
            threshold: 相似度阈值
            top_k: 返回前 K 个结果
            
        Returns:
            相似文本块列表
        """
        if not candidate_texts:
            return []

        similarities = []
        for idx, candidate in enumerate(candidate_texts):
            sim = self.semantic_similarity(query_text, candidate)
            if sim >= threshold:
                similarities.append({
                    "index": idx,
                    "text": candidate[:100],  # 截断显示
                    "similarity": round(sim, 4),
                })

        # 按相似度排序，返回前 K 个
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        return similarities[:top_k]

    def serialize_embedding(self, embedding: list[float]) -> str:
        """
        序列化向量为 JSON 字符串
        
        Args:
            embedding: 向量列表
            
        Returns:
            JSON 字符串
        """
        # 为了节省空间，保留 4 位小数
        compact = [round(x, 4) for x in embedding]
        return json.dumps(compact)

    def deserialize_embedding(self, embedding_json: str) -> list[float]:
        """
        反序列化向量
        
        Args:
            embedding_json: JSON 字符串
            
        Returns:
            向量列表
        """
        try:
            return json.loads(embedding_json)
        except Exception as e:
            print(f"向量反序列化失败: {e}")
            return [0.0] * self.embedding_dim
