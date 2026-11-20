"""NLP 分析引擎 — 关键词提取与观点独特性分析 (Phase 2)"""

from typing import Any
import jieba
import jieba.analyse
from collections import Counter
import re


class NLPAnalyzer:
    """NLP 分析引擎"""

    def __init__(self, model_type: str = "tfidf"):
        """
        初始化 NLP 分析器
        
        Args:
            model_type: 模型类型，当前支持 'tfidf'（快速）和 'bert'（精准，Phase 3+）
        """
        self.model_type = model_type
        # 停用词列表
        self.stop_words = self._load_stop_words()

    def _load_stop_words(self) -> set:
        """加载中文停用词"""
        stop_words = {
            "的", "了", "和", "是", "在", "有", "人", "这", "中", "大",
            "为", "上", "个", "国", "我", "以", "要", "他", "时", "来",
            "用", "们", "生", "到", "作", "地", "于", "出", "就", "分",
            "对", "成", "会", "可", "主", "发", "年", "动", "同", "工",
            "也", "能", "下", "过", "民", "前", "面", "手", "然", "其",
            "多", "经", "做", "去", "制", "情", "火", "法", "水", "本",
            "着", "过", "而", "一", "二", "三", "四", "五", "六", "七",
            "八", "九", "十", "更", "最", "很", "都", "或", "还", "又",
            "与", "到", "里", "了", "么", "只", "但", "都", "至", "则",
        }
        return stop_words

    def extract_keywords(self, text: str, top_k: int = 20) -> list[dict[str, Any]]:
        """
        提取文本关键词（使用 TF-IDF）
        
        Args:
            text: 输入文本
            top_k: 返回前 K 个关键词
            
        Returns:
            关键词列表，包含词和权重
        """
        if not text or len(text) < 10:
            return []

        # 使用 jieba.analyse.extract_tags 提取关键词
        try:
            keywords = jieba.analyse.extract_tags(text, topK=top_k, withWeight=True)
            return [
                {
                    "keyword": word,
                    "weight": round(weight, 4),
                    "frequency": text.count(word),
                }
                for word, weight in keywords
                if word not in self.stop_words
            ]
        except Exception as e:
            print(f"关键词提取失败: {e}")
            return []

    def analyze_originality_score(self, text: str, reference_keywords: list[str]) -> float:
        """
        基于关键词匹配计算观点独特性评分
        
        Args:
            text: 当前文章文本
            reference_keywords: 参考文献关键词列表
            
        Returns:
            观点独特性评分（0-100）
        """
        if not text or not reference_keywords:
            return 50.0  # 默认分数

        # 提取当前文本关键词
        current_keywords = set([kw["keyword"] for kw in self.extract_keywords(text, top_k=50)])

        # 计算重合关键词比例
        reference_set = set(reference_keywords)
        overlap = len(current_keywords & reference_set)
        total_current = len(current_keywords) if current_keywords else 1

        # 重合度越低，独特性越高
        overlap_ratio = overlap / total_current if total_current > 0 else 0
        originality = 100 * (1 - min(overlap_ratio, 1))

        return round(max(originality, 20.0), 2)  # 最低 20 分

    def extract_entities(self, text: str) -> dict[str, list[str]]:
        """
        简单命名实体识别（提取人名、地名、数字等）
        
        Args:
            text: 输入文本
            
        Returns:
            实体字典 {"persons": [...], "locations": [...], "numbers": [...]}
        """
        entities = {"persons": [], "locations": [], "numbers": [], "organizations": []}

        # 提取数字
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        entities["numbers"] = list(set(numbers))[:10]  # 最多 10 个

        # 提取可能的人名（中文名字通常是 2-4 个汉字）
        # 这是简化实现，生产环境应使用专业 NER 模型
        potential_names = re.findall(r"[\u4e00-\u9fa5]{2,4}(?:[说、讲、提到|指出])", text)
        entities["persons"] = list(set(potential_names))[:5]

        return entities

    def analyze_text_structure(self, text: str) -> dict[str, Any]:
        """
        分析文本结构（段落、句子、长度等）
        
        Args:
            text: 输入文本
            
        Returns:
            结构分析结果
        """
        # 按段落分割
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        # 按句子分割（简化版，仅按标点符号）
        sentences = re.split(r"[。！？，；\n]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        # 词汇多样性分析
        words = jieba.cut(text)
        word_list = [w for w in words if len(w) > 1 and w not in self.stop_words]
        word_counter = Counter(word_list)
        unique_words = len(word_counter)
        total_words = len(word_list)
        vocabulary_richness = unique_words / total_words if total_words > 0 else 0

        return {
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "word_count": total_words,
            "unique_words": unique_words,
            "vocabulary_richness": round(vocabulary_richness, 4),
            "avg_paragraph_length": round(len(text) / len(paragraphs), 2) if paragraphs else 0,
            "avg_sentence_length": round(total_words / len(sentences), 2) if sentences else 0,
        }

    def calculate_text_complexity(self, text: str) -> float:
        """
        计算文本复杂度（词汇多样性、句子长度等）
        
        Args:
            text: 输入文本
            
        Returns:
            复杂度评分（0-100）
        """
        structure = self.analyze_text_structure(text)

        # 结合多个因素计算复杂度
        vocabulary_factor = structure["vocabulary_richness"] * 40  # 词汇多样性占 40%
        length_factor = min(structure["avg_sentence_length"] / 20, 1) * 30  # 句子长度占 30%
        paragraph_factor = min(structure["paragraph_count"] / 10, 1) * 30  # 段落数量占 30%

        complexity = vocabulary_factor + length_factor + paragraph_factor
        return round(min(complexity, 100), 2)
