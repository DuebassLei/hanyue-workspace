"""宠物对话意图识别模块：NLU 实现

意图分类：
- create_article: 根据热点/链接/描述生成初稿
- refine_paragraph: 段落润色、改写、去AI味
- generate_svg_card: 根据文本生成动态卡
- select_skill: 选择或推荐Skill
- query_help: 查询帮助或功能说明
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

import logging

logger = logging.getLogger(__name__)


class IntentClassifier:
    """基于关键词的意图识别分类器"""

    # 意图关键词映射
    INTENT_KEYWORDS = {
        "create_article": [
            "写文章", "创作", "生成文章", "根据", "写一篇", "创建", "新增文章",
            "帮我写", "写作", "内容创作", "文章", "稿子",
        ],
        "refine_paragraph": [
            "润色", "改写", "去AI味", "优化", "修改", "调整", "改进", "改一下",
            "修饰", "改善", "美化", "调整语气", "去掉AI感",
        ],
        "generate_svg_card": [
            "卡", "卡片", "动态卡", "SVG", "生成卡", "制作卡", "插卡", "配卡",
            "视觉卡", "展示卡",
        ],
        "select_skill": [
            "技能", "风格", "选择风格", "换风格", "推荐", "suggest", "skill",
            "切换风格", "更换",
        ],
        "query_help": [
            "帮助", "怎么", "如何", "什么是", "介绍", "说明", "帮我了解",
            "help", "how to",
        ],
    }

    @classmethod
    def classify(cls, text: str) -> tuple[str, float]:
        """
        分类用户意图

        Args:
            text: 用户输入文本

        Returns:
            (intent, confidence): 意图类型 和 置信度 (0-1)
        """
        text_lower = text.lower()
        # 功能咨询与内容问答不能误触发生成；命令式请求即使包含疑问词仍是任务。
        command = bool(re.search(r"^(?:请|帮我|麻烦|给我|为我|替我|根据|把|将|用|写|生成|创作|润色|改写|去ai味|制作|插入)", text_lower.strip()))
        if not command:
            if re.search(r"你能做什么|怎么用|如何使用|使用说明|怎么使用|^帮助$|^help$", text_lower):
                return "query_help", 0.9
            if re.search(r"[？?]|^(?:如何|怎么|为什么|什么是|哪些|是否|能否|可以吗)", text_lower):
                return "query_article", 0.85
        # 明确动作优先，避免“根据文章生成卡片”被泛化的“文章”吞掉。
        for intent, terms in (
            ("generate_svg_card", ("动态卡", "卡片", "svg", "生成卡", "插卡")),
            ("refine_paragraph", ("润色", "改写", "去ai味", "去掉ai", "改一下", "压短", "拆段")),
            ("select_skill", ("选择风格", "推荐风格", "切换风格", "选择技能")),
        ):
            if any(term in text_lower for term in terms):
                return intent, 0.9
        if "推荐" in text_lower and any(word in text_lower for word in ("风格", "技能", "skill")):
            return "select_skill", 0.9
        # 只有明确的求助句式才算 query_help；「用一句话说明…」这类是在给创作需求，
        # 不能因为含「说明」就当成功能咨询打发掉。
        if not command and ("帮助" in text_lower or re.search(r"你能做什么|怎么用|如何使用|使用说明|怎么使用|help", text_lower)):
            return "query_help", 0.8
        # 宠物场景的默认意图是创作：绝大多数输入都是「写点什么」。
        return "create_article", 0.8

    @classmethod
    def extract_entities(cls, text: str, intent: str) -> dict[str, Any]:
        """
        从文本中提取实体（URL、标题、段落等）

        Args:
            text: 用户输入
            intent: 意图类型

        Returns:
            实体字典 {url, title, content, ...}
        """
        entities = {}

        # 提取 URL
        url_pattern = r"https?://[^\s，。；！？、<>\"'（）]+"
        urls = [url.rstrip(".,;!?)]}") for url in re.findall(url_pattern, text, flags=re.IGNORECASE)]
        if urls:
            entities["url"] = urls[0]
            entities["urls"] = list(dict.fromkeys(urls))

        # 提取标题（通常在引号内或首行）
        title_pattern = r'["\'"]([^"\'"\n]+)["\'"]|^(.+?)[:：]'
        title_matches = re.findall(title_pattern, text)
        if title_matches:
            entities["title"] = title_matches[0][0] or title_matches[0][1]

        # 段落内容（如果包含多行）
        lines = text.strip().split("\n")
        if len(lines) > 1:
            entities["paragraph"] = "\n".join(lines[1:]).strip()
        else:
            entities["paragraph"] = text

        return entities


class PetConversationStateMachine:
    """宠物对话状态机"""

    # 对话状态
    STATE_IDLE = "idle"
    STATE_LISTENING = "listening"
    STATE_PROCESSING = "processing"
    STATE_RESPONDING = "responding"
    STATE_COMPLETED = "completed"
    STATE_ERROR = "error"

    def __init__(self):
        self.state = self.STATE_IDLE
        self.context: dict[str, Any] = {}
        self.history: list[dict] = []

    def add_user_message(self, message: str) -> None:
        """添加用户消息并转移状态"""
        self.history.append({"role": "user", "content": message})
        self.state = self.STATE_PROCESSING
        self.context["last_message"] = message

    def add_pet_response(self, response: str, metadata: Optional[dict] = None) -> None:
        """添加宠物响应并转移状态"""
        self.history.append({
            "role": "pet",
            "content": response,
            "metadata": metadata or {},
        })
        self.state = self.STATE_COMPLETED

    def set_error(self, error_msg: str) -> None:
        """标记错误状态"""
        self.state = self.STATE_ERROR
        self.context["error"] = error_msg

    def get_context(self) -> dict[str, Any]:
        """获取当前对话上下文"""
        return {
            "state": self.state,
            "history_length": len(self.history),
            "context": self.context,
        }


class PetIntentHandler:
    """意图处理器：根据意图调用对应的业务逻辑"""

    def __init__(self):
        self.classifier = IntentClassifier()

    async def handle_intent(
        self,
        intent: str,
        entities: dict[str, Any],
        conversation_context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        处理识别的意图

        Args:
            intent: 意图类型
            entities: 提取的实体
            conversation_context: 对话上下文

        Returns:
            处理结果 {action, params, response}
        """
        handler_name = f"_handle_{intent}"
        if hasattr(self, handler_name):
            return await getattr(self, handler_name)(entities, conversation_context)
        else:
            return await self._handle_query_help(entities, conversation_context)

    async def _handle_create_article(
        self, entities: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """处理文章创作意图"""
        return {
            "action": "create_article",
            "params": {
                "url": entities.get("url"),
                "title": entities.get("title"),
                "description": entities.get("paragraph"),
                "active_skills": context.get("active_skill_ids", []),
            },
            "response": "我来帮你根据内容生成初稿，请稍候...",
        }

    async def _handle_refine_paragraph(
        self, entities: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """处理段落改稿意图"""
        return {
            "action": "refine_paragraph",
            "params": {
                "paragraph": entities.get("paragraph"),
                "refine_type": self._detect_refine_type(entities),
                "active_skills": context.get("active_skill_ids", []),
            },
            "response": "我来帮你改进这段内容...",
        }

    async def _handle_generate_svg_card(
        self, entities: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """处理卡片生成意图"""
        return {
            "action": "generate_svg_card",
            "params": {
                "text": entities.get("paragraph"),
                "title": entities.get("title"),
            },
            "response": "我来为你生成一张动态卡片...",
        }

    async def _handle_select_skill(
        self, entities: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """处理技能选择意图"""
        return {
            "action": "select_skill",
            "params": {
                "skill_name": entities.get("title"),
            },
            "response": "我来帮你选择或推荐合适的写作风格...",
        }

    async def _handle_query_help(
        self, entities: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """处理帮助查询"""
        return {
            "action": "query_help",
            "params": {},
            "response": (
                "我是你的AI创作助手宠物！我可以帮你：\n"
                "• 根据热点/链接创建文章初稿\n"
                "• 润色、改写或去掉AI味\n"
                "• 生成动态卡片\n"
                "• 推荐合适的写作风格\n"
                "请告诉我你需要什么帮助～"
            ),
        }

    @staticmethod
    def _detect_refine_type(entities: dict) -> str:
        """检测改稿类型"""
        text = entities.get("paragraph", "").lower()
        if "AI味" in text or "去AI" in text:
            return "remove_ai_taste"
        elif "改写" in text:
            return "rewrite"
        else:
            return "polish"
