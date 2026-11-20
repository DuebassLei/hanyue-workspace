"""结构对比引擎 — 逻辑框架分析与创新度评估 (Phase 2)"""

from typing import Any
import networkx as nx
from collections import defaultdict
import re


class StructureAnalyzer:
    """结构对比分析引擎"""

    def __init__(self):
        """初始化结构分析器"""
        self.heading_patterns = [
            r"^#{1,6}\s+",  # Markdown 标题
            r"^\d+\.\s+",  # 有序列表标题
            r"^[一二三四五六七八九十]、",  # 中文序号
        ]

    def extract_outline(self, text: str) -> list[dict[str, Any]]:
        """
        提取文本大纲结构
        
        Args:
            text: 输入文本
            
        Returns:
            大纲列表，包含层级和内容
        """
        lines = text.split("\n")
        outline = []
        current_level = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检测标题级别
            level = self._detect_heading_level(line)
            if level > 0:
                outline.append({
                    "level": level,
                    "text": line,
                    "index": len(outline),
                })

        return outline

    def _detect_heading_level(self, line: str) -> int:
        """
        检测行的标题级别
        
        Returns:
            0 表示非标题，1-6 表示标题级别
        """
        # Markdown 标题
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            return len(match.group(1))

        # 有序列表标题
        if re.match(r"^\d+\.\s+", line):
            return 1

        # 中文序号
        if re.match(r"^[一二三四五六七八九十]、", line):
            return 1

        return 0

    def build_structure_graph(self, outline: list[dict]) -> nx.DiGraph:
        """
        从大纲构建结构图
        
        Args:
            outline: 大纲列表
            
        Returns:
            NetworkX 有向图
        """
        graph = nx.DiGraph()

        # 添加节点
        for item in outline:
            node_id = f"section_{item['index']}"
            graph.add_node(node_id, level=item["level"], text=item["text"])

        # 添加边（父子关系）
        level_stack = []
        for item in outline:
            node_id = f"section_{item['index']}"
            current_level = item["level"]

            # 弹出栈中大于等于当前级别的元素
            while level_stack and level_stack[-1]["level"] >= current_level:
                level_stack.pop()

            # 连接到上一个较小级别的节点
            if level_stack:
                parent_id = f"section_{level_stack[-1]['index']}"
                graph.add_edge(parent_id, node_id)

            level_stack.append(item)

        return graph

    def calculate_structure_diversity(self, outline: list[dict]) -> float:
        """
        计算结构多样性评分
        
        Args:
            outline: 大纲列表
            
        Returns:
            多样性评分（0-100）
        """
        if not outline:
            return 0.0

        # 统计各级别标题数量
        level_counts = defaultdict(int)
        for item in outline:
            level_counts[item["level"]] += 1

        # 级别越丰富，多样性越高
        unique_levels = len(level_counts)
        max_diversity = 100 * (unique_levels / 6)  # 最多 6 级

        # 标题数量因素（太少或太多都不理想）
        total_headings = len(outline)
        heading_factor = 0.0
        if 5 <= total_headings <= 20:  # 理想范围
            heading_factor = 100
        elif 3 <= total_headings < 5:
            heading_factor = 70
        elif 20 < total_headings <= 30:
            heading_factor = 70
        else:
            heading_factor = 40

        # 结合计算
        diversity = (max_diversity * 0.6) + (heading_factor * 0.4)
        return round(min(diversity, 100), 2)

    def calculate_structure_balance(self, graph: nx.DiGraph) -> float:
        """
        计算结构平衡度（子章节数量是否均匀）
        
        Args:
            graph: 结构图
            
        Returns:
            平衡度评分（0-100）
        """
        if len(graph) == 0:
            return 50.0

        # 计算各节点的子节点数量
        child_counts = []
        for node in graph.nodes():
            children = list(graph.successors(node))
            if children:
                child_counts.append(len(children))

        if not child_counts:
            return 50.0

        # 计算标准差（越小越平衡）
        avg_children = sum(child_counts) / len(child_counts)
        variance = sum((x - avg_children) ** 2 for x in child_counts) / len(child_counts)
        std_dev = variance ** 0.5

        # 将标准差转换为平衡度评分
        # std_dev 越小，平衡度越高
        balance = max(0, 100 - (std_dev * 10))
        return round(min(balance, 100), 2)

    def detect_structure_patterns(self, outline: list[dict]) -> dict[str, Any]:
        """
        检测大纲结构的特殊模式
        
        Args:
            outline: 大纲列表
            
        Returns:
            模式检测结果
        """
        patterns = {
            "has_multilevel": False,
            "has_balanced_structure": False,
            "structure_type": "simple",
            "common_patterns": [],
        }

        if not outline:
            return patterns

        # 检查是否有多级结构
        levels = set(item["level"] for item in outline)
        patterns["has_multilevel"] = len(levels) > 1

        # 检查是否为平衡结构
        level_groups = defaultdict(list)
        for item in outline:
            level_groups[item["level"]].append(item)

        # 平衡结构：同级标题数量大致相同
        counts = [len(items) for items in level_groups.values()]
        if counts and max(counts) > 0:
            balance_ratio = min(counts) / max(counts)
            patterns["has_balanced_structure"] = balance_ratio > 0.5

        # 识别结构类型
        if len(levels) == 1:
            patterns["structure_type"] = "flat"
        elif len(levels) == 2:
            patterns["structure_type"] = "two_level"
        else:
            patterns["structure_type"] = "hierarchical"

        # 检测常见模式
        if patterns["has_multilevel"] and patterns["has_balanced_structure"]:
            patterns["common_patterns"].append("standard_hierarchical")

        if all(item["level"] == 1 for item in outline):
            patterns["common_patterns"].append("parallel_sections")

        # 检测递进式结构（逐层深入）
        level_sequence = [item["level"] for item in outline]
        if level_sequence == sorted(set(level_sequence)):
            patterns["common_patterns"].append("progressive_deepening")

        return patterns

    def compare_structures(
        self,
        outline1: list[dict],
        outline2: list[dict],
    ) -> float:
        """
        比较两个文本的结构相似度
        
        Args:
            outline1: 第一个文本的大纲
            outline2: 第二个文本的大纲
            
        Returns:
            结构相似度（0-1）
        """
        if not outline1 or not outline2:
            return 0.0

        # 构建结构签名（关键标题的序列）
        sig1 = [
            item["text"][:20]  # 取前 20 个字符作为签名
            for item in outline1
            if item["level"] <= 2  # 只看一二级标题
        ]
        sig2 = [
            item["text"][:20] for item in outline2 if item["level"] <= 2
        ]

        # 计算编辑距离（简化版）
        if not sig1 or not sig2:
            return 0.0

        # 计算相同元素的比例
        common = len(set(sig1) & set(sig2))
        total = max(len(sig1), len(sig2))

        similarity = common / total if total > 0 else 0
        return round(min(similarity, 1.0), 4)

    def suggest_structure_improvements(
        self,
        outline: list[dict],
        target_diversity: float = 70.0,
    ) -> list[str]:
        """
        基于分析结果提出结构改进建议
        
        Args:
            outline: 大纲列表
            target_diversity: 目标多样性评分
            
        Returns:
            改进建议列表
        """
        suggestions = []

        if not outline:
            suggestions.append("请添加至少 3 个主要章节来组织内容")
            return suggestions

        # 检查标题数量
        if len(outline) < 3:
            suggestions.append("建议至少添加 3 个主要标题来组织内容结构")
        elif len(outline) > 30:
            suggestions.append("标题过多（>30 个），建议合并相近内容以简化结构")

        # 检查级别多样性
        levels = set(item["level"] for item in outline)
        if len(levels) == 1:
            suggestions.append("建议使用多级标题来创建层级结构")
        elif len(levels) > 4:
            suggestions.append("标题级别过多（>4 层），建议简化为 2-3 层")

        # 检查子节点平衡性
        level_groups = defaultdict(list)
        for item in outline:
            level_groups[item["level"]].append(item)

        level_counts = [len(items) for items in level_groups.values()]
        if level_counts and len(level_counts) > 1:
            if max(level_counts) > min(level_counts) * 2:
                suggestions.append("同级标题数量不均衡，建议调整以保持结构对称")

        # 检查结构多样性
        diversity = self.calculate_structure_diversity(outline)
        if diversity < target_diversity:
            suggestions.append(
                f"结构多样性还有提升空间（当前 {diversity:.0f}，建议 {target_diversity:.0f}），"
                f"可尝试增加更多不同层级的小节"
            )

        return suggestions if suggestions else ["当前结构合理，无明显改进建议"]
