"""相似度计算引擎。

使用 TF-IDF + 余弦相似度计算两段文本的相似度。
"""

from __future__ import annotations

import re
from typing import Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import jieba


def _preprocess_text(text: str) -> str:
    """文本预处理：去除特殊符号、规范空白。"""
    # 去除多余空白
    text = re.sub(r'\s+', ' ', text)
    # 去除 URL 和 HTML 标签
    text = re.sub(r'http[s]?://\S+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def calculate_tfidf_similarity(text1: str, text2: str) -> float:
    """使用 TF-IDF + 余弦相似度计算两段文本的相似度。
    
    Args:
        text1: 第一段文本
        text2: 第二段文本
    
    Returns:
        相似度分数 [0, 1]
    """
    if not text1 or not text2:
        return 0.0
    
    # 预处理
    text1 = _preprocess_text(text1)
    text2 = _preprocess_text(text2)
    
    if not text1 or not text2:
        return 0.0
    
    try:
        # 中文分词
        tokens1 = ' '.join(jieba.cut(text1))
        tokens2 = ' '.join(jieba.cut(text2))
        
        # TF-IDF 向量化（1-3 元 N-gram）
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 3),
            max_features=1000,
            lowercase=True,
            token_pattern=r'\S+'
        )
        
        # 拟合并转换
        tfidf_matrix = vectorizer.fit_transform([tokens1, tokens2])
        
        # 余弦相似度
        similarity = float(cosine_similarity(tfidf_matrix)[0][1])
        
        return max(0.0, min(1.0, similarity))
    except Exception:
        return 0.0


def convert_similarity_to_score(similarity: float) -> int:
    """将相似度 [0, 1] 转换为评分 [0, 100]。
    
    映射规则：
    - 相似度 0-30% → 得分 90-100（独特）
    - 相似度 30-50% → 得分 70-90（部分重合）
    - 相似度 50-70% → 得分 40-70（中度重合）
    - 相似度 70-100% → 得分 0-40（高度重合/抄袭）
    
    Args:
        similarity: 相似度 [0, 1]
    
    Returns:
        评分 [0, 100]
    """
    if similarity < 0.3:
        # 0-30% → 90-100
        return int(90 + (1 - similarity / 0.3) * 10)
    elif similarity < 0.5:
        # 30-50% → 70-90
        return int(90 - ((similarity - 0.3) / 0.2) * 20)
    elif similarity < 0.7:
        # 50-70% → 40-70
        return int(70 - ((similarity - 0.5) / 0.2) * 30)
    else:
        # 70-100% → 0-40
        return int(40 - ((similarity - 0.7) / 0.3) * 40)


def extract_similar_segments(text1: str, text2: str, threshold: float = 0.6) -> list[dict]:
    """提取两段文本中的相似片段。
    
    Args:
        text1: 源文本
        text2: 目标文本
        threshold: 相似度阈值 [0, 1]
    
    Returns:
        相似片段列表，每项包含位置和文本
    """
    if not text1 or not text2:
        return []
    
    text1 = _preprocess_text(text1)
    text2 = _preprocess_text(text2)
    
    segments = []
    
    # 以句号、问号、感叹号为分割
    sentences1 = re.split(r'[。！？\n]', text1)
    sentences2 = re.split(r'[。！？\n]', text2)
    
    for i, s1 in enumerate(sentences1):
        s1 = s1.strip()
        if not s1 or len(s1) < 10:
            continue
        
        for j, s2 in enumerate(sentences2):
            s2 = s2.strip()
            if not s2 or len(s2) < 10:
                continue
            
            sim = calculate_tfidf_similarity(s1, s2)
            if sim >= threshold:
                segments.append({
                    'position': f'段落 {i + 1}-{j + 1}',
                    'source_text': s1[:100],
                    'target_text': s2[:100],
                    'similarity': round(sim, 2)
                })
    
    # 按相似度降序排列，取前 5 个
    segments = sorted(segments, key=lambda x: x['similarity'], reverse=True)[:5]
    
    return segments
