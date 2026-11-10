from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Query, Response
from sqlalchemy import or_, select

from .. import models, schemas
from ..core import pagination
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import agent_policy, agent_runtime, agent_status

router = APIRouter(tags=["agent"])

# 公众号成文的统一输出契约。所有写作风格共用，只替换 {tone} 与风格规则。
ARTICLE_PROMPT_TEMPLATE = (
    "你是资深公众号主笔。严格只输出一个 JSON 对象，不要 Markdown 代码块、不要解释文字。"
    "JSON 结构：{\"title\":\"标题\",\"markdown\":\"正文（公众号排版语法）\","
    "\"summary\":\"摘要（≤120字）\",\"coverPrompt\":\"封面图英文提示词\","
    "\"inlinePrompts\":[\"插图1英文提示词\",\"插图2英文提示词\"]}。"
    "正文使用中文，{tone}，不用 emoji，不用绝对化承诺。"
    "封面图提示词用英文，描述画面主体、风格、色调。"
    "插图提示词数量与正文中 {{imageN}} 标记数量一致。"
)

# 拆自 wechat-mp-article 技能的 10 类品类方法论，作为可选写作风格
_WRITING_STYLE_SPECS = [
    {
        "key": "tech-article",
        "name": "科技数码·实测派",
        "one_liner": "好选题加事实讲透，作者在场却不表演",
        "tone": "第一人称在场，语气克制，多写实测细节，重判断后置，不喊口号",
        "structure": [
            "开头用亲切自述，抛出一个具体真问题",
            "主体层层剥开：现象、解释、追问、洞察",
            "论据以事实为主，数据、故事、引语交织",
            "结尾升维到文化人性，或白话收束",
        ],
        "rules": [
            "禁冒号破折号感叹号，引号用「」",
            "数据密度不超过全篇四成",
            "禁用赋能、抓手、闭环等商业黑话",
            "少用段末钩子，不写「接下来我们看」",
            "产品名首次出现后保持英文",
            "禁「重大突破」等无证据断言",
        ],
        "theme": "deep-blue",
    },
    {
        "key": "news-and-gov",
        "name": "资讯政务·核实派",
        "one_liner": "准优先于快，作者隐身，事实即威信",
        "tone": "作者退到记录员位置，冷静陈述，多用时间、地点、数字与引语，不抒情不评判",
        "structure": [
            "开头用事实卡，三句话说清五要素",
            "主体用时间戳串珠推进，不用「首先其次」",
            "多方原话并列：官方、当事人、专家",
            "结尾在未完成时收束，配一个具体动作",
            "政策类改文件线，结尾给行动清单",
        ],
        "rules": [
            "事实段与评论段分层，不混写",
            "禁「惊天动地」「令人痛心」等形容",
            "禁「让我们一起祈祷」式编辑腔",
            "未核实信息注明出处并标明未定",
            "感叹号仅限引用官方通报原文",
            "敏感人脸与遗体画面必须处理",
        ],
        "theme": "deep-blue",
    },
    {
        "key": "culture-emotion",
        "name": "文化情感·说破派",
        "one_liner": "一个具体的人，一句想被截图的话",
        "tone": "以同代朋友的口吻叙述，情绪克制，最重的一句写得最短，不评价不说教",
        "structure": [
            "开头对准一个具体的人：名字、年龄、场景一句话",
            "中段用事件、对话、转折推进，穿插短句高点",
            "关键句独占一段，前后留白",
            "结尾只给一个姿态，不给结论",
        ],
        "rules": [
            "禁「亲爱的」「家人们」「集美们」",
            "禁「干货」「方法论」「五步法」",
            "不写「让我们一起」「共勉」口号",
            "第二人称「你」全篇不超三四次",
            "不虚构「我的一位朋友」骗共情",
            "当事人须化名并做面部处理",
        ],
        "theme": "warm-orange",
    },
    {
        "key": "finance-analysis",
        "name": "财经财富·判断派",
        "one_liner": "一手数据，敢下判断，明说前提",
        "tone": "像懂行分析师在饭桌聊天，结论前置、敢下判断，但每一步都给来源与前提",
        "structure": [
            "开头三句给一个反直觉的数据反差",
            "开头四百字内给出明确结论",
            "中段用数据交叉印证，拼出独家判断",
            "给一个可复用的分析框架",
            "结尾给一个动作，或明确免责",
        ],
        "rules": [
            "每个数字标注公开数据来源",
            "结论后明说前提假设与失效条件",
            "禁荐股荐基与稳赚不赔承诺",
            "禁改数据轴或掐头去尾造错觉",
            "不写「未来可期」「值得关注」空话",
        ],
        "theme": "deep-blue",
    },
    {
        "key": "knowledge-popsci",
        "name": "知识科普·讲懂派",
        "one_liner": "用类比把复杂讲清，再明说边界",
        "tone": "像耐心的老师在你面前讲，术语先解释，多用类比，不端着不炫技",
        "structure": [
            "开头从读者遇到过的具体场景切入",
            "全篇用一个新鲜准确的类比贯穿",
            "讲清后再追问一层，追问三到四层",
            "结尾给一句能复述的总结",
            "末尾分层说清该自己做还是找专业",
        ],
        "rules": [
            "禁把单一小样本写成科学定论",
            "预印本与论文引用须标出处",
            "不做超出资质的具体诊疗建议",
            "类比要说明哪里不精确",
            "禁「XX 五大好处」式罗列",
        ],
        "theme": "ink-green",
    },
    {
        "key": "fashion-lifestyle",
        "name": "时尚生活·审美派",
        "one_liner": "一个可复述的主张，每件挂具体场景",
        "tone": "像比你时髦一点的朋友翻衣橱，有主张、有细节、有节制，不夸大功效",
        "structure": [
            "开头先立一句可复述的生活方式主张",
            "每件单品明确挂一个具体使用场景",
            "按预算、体型、户型主动分层推荐",
            "结尾从单品升到审美与活法",
        ],
        "rules": [
            "用材质、剪裁、成分、克重代替「高级」",
            "主动说一件测过但不推荐的东西",
            "禁疗效承诺与「立竿见影」表述",
            "禁虚构素人推荐包装商务合作",
            "AI 图不伪造上身与试色",
        ],
        "theme": "warm-orange",
    },
    {
        "key": "food-travel",
        "name": "美食旅行·到场派",
        "one_liner": "到场才可信，一个暗知识加信息卡",
        "tone": "像刚从现场回来的朋友讲见闻，多用感官细节与取舍判断，落到实处",
        "structure": [
            "开头给一个只有到场才写出的感官细节",
            "每店每站紧跟一张实用信息卡",
            "中段埋一个只有到过才懂的暗知识",
            "主动说一家去过但不推荐的地方",
            "结尾给一句关于吃或旅行的私人观察",
        ],
        "rules": [
            "禁「好吃」「香」「层次丰富」等词",
            "禁用图库图与 AI 图伪造现场",
            "人均与营业时间如实，不夸大",
            "商务合作须开头或结尾明示",
            "食安指控须基于可验证事实",
        ],
        "theme": "warm-orange",
    },
    {
        "key": "people-profile",
        "name": "人物特稿·立人派",
        "one_liner": "一个动作立一个人，镜头稳而克制",
        "tone": "镜头始终对准人物，语气安静克制，只用看得见听得到的细节，不代言",
        "structure": [
            "开头落在此人某个具体动作上",
            "前三段说清这是谁、为何值得读",
            "全篇找一个只属于他的私人细节",
            "至少给一个公允的对立面声音",
            "结尾用动作、物件或原话收束",
        ],
        "rules": [
            "禁编造对话、细节与心理活动",
            "引语只留最锋利几句并给出处",
            "大段贴采访原话视为偷懒",
            "不消费遗容病榻与家属崩溃",
            "不俯视不仰视，不做人格审判",
        ],
        "theme": "mono",
    },
    {
        "key": "film-tv-review",
        "name": "影视剧评·拆镜派",
        "one_liner": "反共识视角，拆到镜头级细节",
        "tone": "懂行但不装的评论人，先给反共识判断，再用场景细节验证，尊重剧透边界",
        "structure": [
            "开头一行明示剧透范围",
            "先复述主流两派，再抛反共识观点",
            "挑一场关键戏拆到镜头与秒级",
            "用三四个平行场景交叉印证",
            "结尾从这一部升到这一类",
        ],
        "rules": [
            "用动作代替剧情复述",
            "禁大段剧情复述当评论",
            "主动承认这种手法的代价",
            "批评作品不攻击演员容貌私生活",
            "不炒 CP，不放大饭圈骂战",
        ],
        "theme": "mono",
    },
    {
        "key": "activity-marketing",
        "name": "活动营销·转化派",
        "one_liner": "前三行讲清是什么，一个明确 CTA",
        "tone": "像会推荐给朋友的人说话，利益前置、门槛透明、有取舍，不吆喝不空喊",
        "structure": [
            "前三行说清是什么、何时、为何点",
            "中段给三到五个具体亮点",
            "门槛信息前置，不藏不含糊",
            "明说这个活动不适合谁",
            "结尾只给一个具体 CTA",
        ],
        "rules": [
            "用名词、数字、名字代替形容词",
            "禁「最」「第一」「国家级」等词",
            "禁疗效、收益、中奖承诺",
            "不虚构参与人数与用户评价",
            "抽奖规则必须公示",
        ],
        "theme": "warm-orange",
    },
]


def _style_skill(spec: dict) -> dict:
    """把品类规格转成内置写作风格 Skill。"""
    return {
        "skill_key": f"style-{spec['key']}",
        "display_name": spec["name"],
        "description": spec["one_liner"],
        "risk_level": agent_policy.REVERSIBLE_WRITE,
        "allowed_tool_ids": ["generate_article"],
        "manifest": {
            "input": {"goal": "string"},
            "output": {"draft": "object"},
            "riskLevel": agent_policy.REVERSIBLE_WRITE,
            "promptTemplate": ARTICLE_PROMPT_TEMPLATE,
            "tone": spec["tone"],
            "styleRules": [f"结构：{item}" for item in spec["structure"]] + spec["rules"],
            "defaultTheme": spec["theme"],
        },
    }


BUILTIN_SKILLS = [
    {
        "skill_key": "topic-research",
        "display_name": "选题调研",
        "description": "检索工作区稿件、扩展角度并给出候选标题。",
        "risk_level": agent_policy.READ_ONLY,
        "allowed_tool_ids": ["read_content"],
        "manifest": {
            "input": {"goal": "string"},
            "output": {"draft": "object"},
            "riskLevel": agent_policy.READ_ONLY,
        },
    },
    {
        "skill_key": "article-drafting",
        "display_name": "公众号热点成文",
        "description": "基于选题生成公众号文章（Markdown + 排版 + 配图提示词）。可自定义语气风格。",
        "risk_level": agent_policy.REVERSIBLE_WRITE,
        "allowed_tool_ids": ["create_outline", "generate_article"],
        "manifest": {
            "input": {"goal": "string"},
            "output": {"draft": "object"},
            "riskLevel": agent_policy.REVERSIBLE_WRITE,
            # 写作风格配置：可被自定义 skill 覆盖
            "promptTemplate": ARTICLE_PROMPT_TEMPLATE,
            "tone": "语气平实有深度，结构清晰，善用小标题和引用",
            "styleRules": [
                "不用 emoji",
                "不用绝对化承诺（如「100%有效」）",
                "善用 ## 小标题拆分段落",
                "关键信息用 > 引用块突出",
            ],
            "defaultTheme": "ink-green",
        },
    },
    {
        "skill_key": "publish-prep",
        "display_name": "发布准备",
        "description": "生成平台版本与发布预览，不直接发布。",
        "risk_level": agent_policy.REVERSIBLE_WRITE,
        "allowed_tool_ids": ["prepare_platform_variant", "create_publish_preview"],
        "manifest": {
            "input": {"goal": "string"},
            "output": {"draft": "object"},
            "riskLevel": agent_policy.REVERSIBLE_WRITE,
        },
    },
    *[_style_skill(spec) for spec in _WRITING_STYLE_SPECS],
]


def seed_skills(db: DbSession) -> None:
    for spec in BUILTIN_SKILLS:
        exists = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == spec["skill_key"]
            )
        )
        if exists:
            # 内置 skill 已存在：同步 manifest（prompt 模板、风格规则），避免新旧版本漂移
            exists.display_name = spec["display_name"]
            exists.description = spec["description"]
            exists.risk_level = spec["risk_level"]
            exists.allowed_tool_ids = spec["allowed_tool_ids"]
            exists.manifest = spec["manifest"]
            exists.checksum = hashlib.sha256(
                json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            continue
        db.add(
            models.SkillRegistryEntry(
                source="builtin",
                skill_key=spec["skill_key"],
                version="1.0.0",
                display_name=spec["display_name"],
                description=spec["description"],
                risk_level=spec["risk_level"],
                allowed_tool_ids=spec["allowed_tool_ids"],
                trust_status="trusted",
                manifest=spec["manifest"],
                checksum=hashlib.sha256(
                    json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            )
        )
    db.commit()


@router.get("/agent/policy")
def get_agent_policy(user: CurrentUser) -> dict:
    """返回服务端实际生效的工具风险策略，供设置页直接渲染。"""
    return agent_policy.policy_summary()


@router.get("/agent/status")
def get_agent_status(user: CurrentUser, db: DbSession) -> dict:
    """Agent 当前运行模式与数据落地路径，供设置页展示真实状态。"""
    return agent_status.status_snapshot(db, user)


@router.get("/agent/providers")
def list_providers(user: CurrentUser, db: DbSession) -> list[dict]:
    rows = db.scalars(
        select(models.AgentProviderConnection).where(
            models.AgentProviderConnection.owner_user_id == user.id
        )
    ).all()
    providers = [
        {
            "id": "native",
            "label": "工作台原生 Agent",
            "configured": True,
            "description": "仅使用工作台内受控工具，无本机文件与命令权限",
        }
    ]
    for row in rows:
        providers.append(
            {
                "id": row.id,
                "label": row.provider,
                "configured": row.status == "configured",
                "description": "外部 Provider（可选）",
            }
        )
    return providers


@router.get("/skills", response_model=list[schemas.SkillOut])
def list_skills(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.SkillOut]:
    seed_skills(db)
    workspace = default_workspace(db, user)
    # 当前用户已定制的内置 skill_key：列表里用个人副本替换内置原版
    builtin_keys = {spec["skill_key"] for spec in BUILTIN_SKILLS}
    forked_keys = set(
        db.scalars(
            select(models.SkillRegistryEntry.skill_key).where(
                models.SkillRegistryEntry.owner_user_id == user.id,
                models.SkillRegistryEntry.source == "custom",
                models.SkillRegistryEntry.skill_key.in_(builtin_keys),
            )
        ).all()
    )
    entries, next_cursor = pagination.paginate(
        db,
        # 内置 Skill（owner 为空）全员可见，但被个人定制覆盖的不重复展示；自定义 Skill 仅创建者可见
        select(models.SkillRegistryEntry).where(
            or_(
                models.SkillRegistryEntry.owner_user_id.is_(None),
                models.SkillRegistryEntry.owner_user_id == user.id,
            )
        ),
        models.SkillRegistryEntry,
        sort_column=models.SkillRegistryEntry.created_at,
        cursor=cursor,
        limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    grants = {
        g.skill_entry_id: g
        for g in db.scalars(
            select(models.WorkspaceSkill).where(models.WorkspaceSkill.workspace_id == workspace.id)
        ).all()
    }
    result: list[schemas.SkillOut] = []
    for entry in entries:
        # 被个人定制覆盖的内置原版不再展示
        if entry.owner_user_id is None and entry.skill_key in forked_keys:
            continue
        grant = grants.get(entry.id)
        result.append(
            schemas.SkillOut(
                id=entry.id,
                skill_key=entry.skill_key,
                version=entry.version,
                display_name=entry.display_name,
                description=entry.description,
                risk_level=entry.risk_level,
                allowed_tool_ids=entry.allowed_tool_ids or [],
                trust_status=entry.trust_status,
                enabled=bool(grant and grant.enabled),
                granted_tool_ids=grant.granted_tool_ids if grant else [],
                checksum=entry.checksum,
                manifest=entry.manifest or {},
                source=entry.source or "builtin",
                customized=(
                    entry.source == "custom" and entry.skill_key in builtin_keys
                ),
            )
        )
    return result


@router.post("/skills", response_model=schemas.SkillOut, status_code=201)
def create_skill(
    payload: schemas.SkillCreateIn, user: CurrentUser, db: DbSession
) -> schemas.SkillOut:
    """创建自定义 skill（写作风格模板）。

    manifest 可包含：
    - promptTemplate: 写作 prompt 模板，支持 {tone} 占位符
    - tone: 语气风格描述
    - styleRules: 风格规则列表
    - defaultTheme: 默认排版主题
    """
    # skill_key 在同一用户范围内唯一；内置 Skill 的 key 也不允许占用
    existing = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == payload.skill_key,
            or_(
                models.SkillRegistryEntry.owner_user_id.is_(None),
                models.SkillRegistryEntry.owner_user_id == user.id,
            ),
        )
    )
    if existing:
        raise ApiError(
            "skill_key_exists",
            f"skill_key「{payload.skill_key}」已存在",
            status_code=409,
        )
    manifest = dict(payload.manifest or {})
    # 自定义 skill 默认信任，风险等级按工具推断
    entry = models.SkillRegistryEntry(
        source="custom",
        owner_user_id=user.id,
        skill_key=payload.skill_key,
        version=payload.version,
        display_name=payload.display_name,
        description=payload.description,
        risk_level="reversible_write",
        allowed_tool_ids=payload.allowed_tool_ids,
        trust_status="trusted",
        manifest=manifest,
        checksum=hashlib.sha256(
            json.dumps(
                {
                    "skill_key": payload.skill_key,
                    "manifest": manifest,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=False,
        granted_tool_ids=[],
        checksum=entry.checksum,
        manifest=entry.manifest or {},
        source=entry.source or "custom",
    )


def _owned_custom_skill(
    db: DbSession, user, skill_id: str
) -> models.SkillRegistryEntry:
    """取当前用户拥有的自定义 Skill；内置 Skill 拒绝编辑，他人的 Skill 按 404 处理避免泄露存在性。"""
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    if entry.source != "custom":
        raise ApiError(
            "skill_not_editable",
            "内置 Skill 不可直接编辑；如需调整请使用「定制内置风格」生成个人副本",
            status_code=403,
        )
    if entry.owner_user_id != user.id:
        # 他人的自定义 Skill 不暴露存在性
        raise not_found("Skill")
    return entry


@router.put("/skills/{skill_id}", response_model=schemas.SkillOut)
def update_skill(
    skill_id: str,
    payload: schemas.SkillUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    """更新自定义写作风格（内置 Skill 不可改）。"""
    entry = _owned_custom_skill(db, user, skill_id)
    if payload.display_name is not None:
        entry.display_name = payload.display_name
    if payload.description is not None:
        entry.description = payload.description
    if payload.version is not None:
        entry.version = payload.version
    if payload.manifest is not None:
        entry.manifest = dict(payload.manifest)
    # checksum 随内容变化，保证「内容变了」可被审计识别
    entry.checksum = hashlib.sha256(
        json.dumps(
            {"skill_key": entry.skill_key, "manifest": entry.manifest or {}},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    db.commit()
    db.refresh(entry)
    workspace = default_workspace(db, user)
    grant = db.scalar(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.workspace_id == workspace.id,
            models.WorkspaceSkill.skill_entry_id == entry.id,
        )
    )
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=bool(grant and grant.enabled),
        granted_tool_ids=grant.granted_tool_ids if grant else [],
        checksum=entry.checksum,
        manifest=entry.manifest or {},
        source=entry.source or "custom",
    )


@router.post("/skills/{skill_id}/customize", response_model=schemas.SkillOut, status_code=201)
def customize_skill(
    skill_id: str,
    payload: schemas.SkillUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    """把内置写作风格复制为当前用户的个人定制副本。

    内置 Skill 本体不可改（全员共享、随版本同步）；定制后：
    - 个人副本（source=custom，owner=当前用户）可继续通过 PUT /skills/{id} 修改
    - 列表中内置原版被个人副本替换展示
    - 删除副本即恢复内置默认
    """
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    if entry.source != "builtin":
        raise ApiError(
            "skill_not_builtin",
            "仅内置写作风格支持定制；自定义风格请直接编辑",
            status_code=409,
        )
    existing_fork = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == entry.skill_key,
            models.SkillRegistryEntry.owner_user_id == user.id,
        )
    )
    if existing_fork:
        raise ApiError(
            "skill_key_exists",
            "该内置风格已有个人定制副本，请直接编辑副本",
            status_code=409,
        )
    manifest = dict(entry.manifest or {})
    if payload.manifest is not None:
        manifest = dict(payload.manifest)
    fork = models.SkillRegistryEntry(
        source="custom",
        owner_user_id=user.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=payload.display_name or entry.display_name,
        description=payload.description if payload.description is not None else entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids,
        trust_status="trusted",
        manifest=manifest,
        checksum=hashlib.sha256(
            json.dumps(
                {"skill_key": entry.skill_key, "manifest": manifest},
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    )
    db.add(fork)
    db.commit()
    db.refresh(fork)
    return schemas.SkillOut(
        id=fork.id,
        skill_key=fork.skill_key,
        version=fork.version,
        display_name=fork.display_name,
        description=fork.description,
        risk_level=fork.risk_level,
        allowed_tool_ids=fork.allowed_tool_ids or [],
        trust_status=fork.trust_status,
        enabled=False,
        granted_tool_ids=[],
        checksum=fork.checksum,
        manifest=fork.manifest or {},
        source=fork.source,
        customized=True,
    )


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, user: CurrentUser, db: DbSession) -> None:
    """删除自定义写作风格，并清理工作区授权。内置 Skill 不可删。"""
    entry = _owned_custom_skill(db, user, skill_id)
    for grant in db.scalars(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.skill_entry_id == entry.id
        )
    ).all():
        db.delete(grant)
    db.delete(entry)
    db.commit()


@router.patch("/skills/{skill_id}", response_model=schemas.SkillOut)
def toggle_skill(
    skill_id: str, payload: schemas.SkillToggleIn, user: CurrentUser, db: DbSession
) -> schemas.SkillOut:
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    if entry.source == "custom" and entry.owner_user_id != user.id:
        raise ApiError("skill_not_editable", "无权操作他人的自定义 Skill", status_code=403)
    if entry.trust_status != "trusted":
        raise ApiError("untrusted_skill", "来源未校验的 Skill 不允许启用", status_code=409)
    workspace = default_workspace(db, user)
    grant = db.scalar(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.workspace_id == workspace.id,
            models.WorkspaceSkill.skill_entry_id == entry.id,
        )
    )
    if grant is None:
        grant = models.WorkspaceSkill(
            workspace_id=workspace.id, skill_entry_id=entry.id, enabled=False
        )
        db.add(grant)
        db.flush()

    if payload.enabled:
        requested = payload.granted_tool_ids or (entry.allowed_tool_ids or [])
        # 授权只能收窄，不能超出 Skill 声明的工具范围
        over = [t for t in requested if t not in (entry.allowed_tool_ids or [])]
        if over:
            raise ApiError(
                "tool_not_in_skill", f"工具超出 Skill 声明范围：{', '.join(over)}", status_code=409
            )
        grant.granted_tool_ids = requested
    grant.enabled = payload.enabled
    grant.approval_policy = payload.approval_policy
    db.commit()
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=grant.enabled,
        granted_tool_ids=grant.granted_tool_ids or [],
        checksum=entry.checksum,
        manifest=entry.manifest or {},
        source=entry.source or "builtin",
    )


@router.post("/agent/sessions", response_model=schemas.AgentRunOut | dict, status_code=201)
def create_session(
    payload: schemas.AgentSessionIn, user: CurrentUser, db: DbSession
) -> dict:
    workspace = default_workspace(db, user)
    session = models.AgentSession(
        owner_user_id=user.id, workspace_id=workspace.id, title=payload.title
    )
    db.add(session)
    db.commit()
    return {"id": session.id, "title": session.title, "status": session.status}


@router.get("/agent/sessions")
def list_sessions(user: CurrentUser, db: DbSession) -> list[dict]:
    rows = db.scalars(
        select(models.AgentSession)
        .where(models.AgentSession.owner_user_id == user.id)
        .order_by(models.AgentSession.created_at.desc())
        .limit(30)
    ).all()
    return [{"id": r.id, "title": r.title, "status": r.status} for r in rows]


@router.post("/agent/sessions/{session_id}/messages", response_model=schemas.AgentRunOut)
async def send_message(
    session_id: str, payload: schemas.AgentMessageIn, user: CurrentUser, db: DbSession
) -> schemas.AgentRunOut:
    session = db.get(models.AgentSession, session_id)
    if not session or session.owner_user_id != user.id:
        raise not_found("Agent 会话")
    if not payload.skill_ids:
        raise ApiError("skill_required", "请先选择已启用的 Skill")

    run = models.AgentRun(
        session_id=session.id,
        owner_user_id=user.id,
        workspace_id=session.workspace_id,
        goal=payload.goal,
        status="queued",
        permission_snapshot={"skillIds": payload.skill_ids},
        budget_limit={"maxRounds": payload.max_rounds},
    )
    db.add(run)
    db.flush()
    await agent_runtime.start_run(db, run)
    db.commit()
    return _run_out(run)


@router.get("/agent/runs/{run_id}", response_model=schemas.AgentRunOut)
def get_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    return _run_out(run)


@router.get("/agent/runs/{run_id}/events", response_model=list[schemas.AgentEventOut])
def get_events(
    run_id: str, user: CurrentUser, db: DbSession, after_seq: int = 0
) -> list[schemas.AgentEventOut]:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    rows = db.scalars(
        select(models.AgentEvent)
        .where(models.AgentEvent.run_id == run.id, models.AgentEvent.seq > after_seq)
        .order_by(models.AgentEvent.seq)
    ).all()
    return [
        schemas.AgentEventOut(
            seq=row.seq,
            event_type=row.event_type,
            payload=row.safe_payload or {},
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/agent/runs/{run_id}/pause", response_model=schemas.AgentRunOut)
def pause_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    if run.status in {"succeeded", "failed", "cancelled"}:
        raise ApiError("invalid_state", "该运行已结束", status_code=409)
    run.status = "paused"
    agent_runtime.emit(db, run, "run.paused", {})
    db.commit()
    return _run_out(run)


@router.post("/agent/runs/{run_id}/resume", response_model=schemas.AgentRunOut)
def resume_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    if run.status != "paused":
        raise ApiError("invalid_state", "只有已暂停的运行可以继续", status_code=409)
    run.status = "running"
    agent_runtime.emit(
        db, run, "run.resumed_manually", {"allowedTools": agent_runtime.resolve_run_permissions(db, run)}
    )
    db.commit()
    return _run_out(run)


@router.post("/agent/runs/{run_id}/cancel", response_model=schemas.AgentRunOut)
def cancel_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    run.status = "cancelled"
    agent_runtime.emit(db, run, "run.cancelled", {})
    db.commit()
    return _run_out(run)


@router.get("/agent/runs/{run_id}/approvals", response_model=list[schemas.ApprovalOut])
def list_approvals(run_id: str, user: CurrentUser, db: DbSession) -> list[schemas.ApprovalOut]:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    rows = db.scalars(
        select(models.AgentApproval).where(models.AgentApproval.run_id == run.id)
    ).all()
    return [
        schemas.ApprovalOut(
            id=row.id,
            run_id=row.run_id,
            requested_action=row.requested_action,
            risk_level=row.risk_level,
            status=row.status,
            params=row.params or {},
            expires_at=row.expires_at,
        )
        for row in rows
    ]


@router.post("/agent/approvals/{approval_id}/decision", response_model=schemas.AgentRunOut)
async def decide(
    approval_id: str, payload: schemas.ApprovalDecisionIn, user: CurrentUser, db: DbSession
) -> schemas.AgentRunOut:
    approval = db.get(models.AgentApproval, approval_id)
    if not approval:
        raise not_found("审批")
    run = db.get(models.AgentRun, approval.run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("审批")
    try:
        agent_runtime.decide_approval(db, approval, run, payload.decision, user.id)
    except agent_runtime.AgentRuntimeError as exc:
        raise ApiError("approval_failed", str(exc), status_code=409) from exc
    db.commit()
    return _run_out(run)


def _run_out(run: models.AgentRun) -> schemas.AgentRunOut:
    return schemas.AgentRunOut(
        id=run.id,
        session_id=run.session_id,
        status=run.status,
        goal=run.goal,
        rounds_used=run.rounds_used,
        error_code=run.error_code,
        created_at=run.created_at,
    )
