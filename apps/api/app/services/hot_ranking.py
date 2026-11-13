"""热点聚簇与评分：纯函数，不碰数据库、不发网络请求。

从 DSH 工作台的 `tools/tracks.py` + `tools/hot-topics.py` 移植，保留其结论与标定值：

    归一化 → 相似判定（反义/异实体先否决）→ 并查集聚簇 → 100 分制评分

三条必须守住的约定：

1. **可复现**：同一批输入 + 同一个 `captured_at`，分数必须完全一致。
   所以这里不读时间、不读随机数，`captured_at` 一律由调用方传入。
2. **可解释**：每个维度的分值和证据都写进 `score_detail`，界面 hover 能看到
   「凭什么给这个分」。没有证据就不给分，也不编。
3. **误合并比漏合并更糟**：把两条对立新闻（涨/跌、开幕/闭幕、降准/降息）
   合成一个事件，会让选题直接写错且用户看不出来；漏合并只是多一张卡片。
   因此相似判定把「否决」放在「相似度」之前。

评分模型（满分 100，再减风险）：

    共振 30 + 势能 25 + 时效 20 + 赛道 15 + 可写性 10 − 风险(0~30)
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════
# ① 赛道与风险词表
# ══════════════════════════════════════════════════════════════════════
#
# 顺序即优先级。命中数相同时，靠前的赢。
# 娱乐放在最后：`官宣` `明星` `剧` 这类词跨赛道借用严重，
# 让宽泛赛道后判、精确赛道先判，误判率明显下降。

TRACKS: list[tuple[str, tuple[str, ...]]] = [
    ("教育与职场", (
        "上学", "入学", "学位", "学校", "校区", "老师", "教师", "教师节", "招生",
        "考试", "高考", "考研", "中考", "校招", "开学", "课间", "作业", "家长会",
        "职场", "上班", "加班", "裁员", "求职", "跳槽", "副业", "绩效", "面试",
        "入职", "年薪", "应届", "毕业", "简历", "远程办公",
    )),
    ("体育与健康", (
        "夺冠", "冠军", "球队", "赛季", "联赛", "国足", "中超", "中甲", "奥运",
        "全运", "亚锦赛", "世界杯", "运动员", "比赛", "球迷", "教练", "伤病",
        "健身", "养生", "健康", "体检", "医院", "就医", "疾病", "疫苗", "医生",
        "睡眠", "作息", "减肥", "跑步", "营养",
    )),
    ("AI 与科技", (
        "AI", "人工智能", "大模型", "模型", "芯片", "算力", "算法", "开源",
        "机器人", "编程", "开发者", "代码", "数码", "手机", "电脑", "显卡",
        "航天", "火箭", "量子", "科研", "论文", "Gemini", "GPT", "DeepSeek",
        "Claude", "OpenAI", "智能体", "Agent",
    )),
    ("国际与时事", (
        "美国", "日本", "韩国", "俄罗斯", "俄乌", "乌克兰", "外交", "联合国",
        "欧洲", "欧盟", "北约", "中东", "加沙", "以色列", "伊朗", "朝鲜",
        "总统", "首相", "军事", "军演", "对华", "制裁", "特朗普", "普京",
        "泽连斯基", "停火", "谈判", "汇率", "关税",
    )),
    ("财经与商业", (
        "股", "股市", "基金", "房价", "楼市", "财报", "营收", "融资", "上市",
        "消费", "税率", "工资", "薪", "利率", "存款", "贷款", "经济", "商业",
        "创业", "公司", "企业", "汽车", "新能源", "亏", "赚", "降价", "涨价",
        "双十一", "补贴",
    )),
    ("社会民生", (
        "家长", "孩子", "老人", "市民", "乘客", "邻居", "社区", "小区", "物业",
        "纠纷", "维权", "投诉", "通报", "回应", "调解", "补贴", "社保", "医保",
        "养老", "户籍", "公积金", "外卖", "快递", "电动车", "遛狗",
    )),
    ("娱乐与情感", (
        "明星", "艺人", "恋情", "结婚", "离婚", "塌房", "绯闻", "综艺", "演员",
        "歌手", "粉丝", "夫妻", "婆媳", "情感", "网红", "直播", "出轨", "复合",
        "官宣", "影视", "电视剧", "电影", "剧", "动漫", "偶像", "恋情曝光",
    )),
]

DEFAULT_TRACK = "文化与生活"
ALL_TRACKS = [name for name, _ in TRACKS] + [DEFAULT_TRACK]

# 实体名前置判：标题里只有人名、没有「明星」「运动员」这类字眼时，关键词判不出来。
# 两份名单都是实测踩过的坑（判错会让整篇按错赛道写），所以前置。
SPORTS_NAMES = (
    "王楚钦", "孙颖莎", "樊振东", "马龙", "张本", "林诗栋", "蒯曼", "陈熠",
    "早田", "雨果", "国乒", "国足", "温瑞博", "全红婵", "苏炳添",
)
SPORTS_EVENTS = ("WTT", "亚锦赛", "世界杯", "奥运", "中甲", "中超", "CBA", "NBA")
ENT_NAMES = (
    "赵丽颖", "杨幂", "杨紫", "迪丽热巴", "王一博", "肖战", "易烊千玺",
    "刘亦菲", "汪苏泷", "许飞", "厉娜", "杨颖", "黄晓明", "张若昀",
)

# AI 造谣/辟谣类标题不算科技赛道，算社会民生（实测「网传X系AI编造」）
AI_RUMOR = (r"AI 编造", r"AI编造", r"AI造谣", r"系AI", r"AI生成.*谣言", r"AI换脸")

RISK_WORDS: dict[str, tuple[str, ...]] = {
    "时政": ("总书记", "政治局", "常委会", "国务院", "人大", "政协", "外交部",
             "国防部", "讲话", "指示", "批示", "考察", "调研", "全会", "选举",
             "投票", "庄严", "任命", "免职"),
    "灾害伤亡": ("遇难", "身亡", "死亡", "伤亡", "坍塌", "爆炸", "失火", "火灾",
                 "地震", "洪水", "坠亡", "自杀", "溺亡", "车祸", "事故"),
    "未证实": ("网传", "疑似", "爆料", "传闻", "据说", "知情人", "曝料"),
    "涉未成年人": ("未成年", "小学生", "初中生", "高中生", "儿童", "女童", "男童"),
    "医疗建议": ("偏方", "神药", "包治", "秘方", "根治", "特效药", "祖传"),
}

RISK_PENALTY = {
    "时政": -25,
    "灾害伤亡": -20,
    "未证实": -10,
    "涉未成年人": -10,
    "医疗建议": -10,
}

# 赛道经验基数 0~5，用于「受众基数」的先验。
# 这是拍的，只有方向意义；等 publishes 回填真实阅读数后应重新校准。
TRACK_BASE = {
    "社会民生": 5, "娱乐与情感": 5,
    "财经与商业": 4, "AI 与科技": 4, "体育与健康": 4,
    "国际与时事": 3, "教育与职场": 3, "文化与生活": 3,
}


def _hit_count(title: str, words: tuple[str, ...]) -> int:
    return sum(1 for word in words if word in title)


def classify(title: str, platform: str = "") -> str:
    """返回赛道名。纯函数，同一输入永远同一输出。

    platform 只用于两个平台的直判（AI 聚合频道的两路天然是科技）。
    """
    text = (title or "").strip()
    if not text:
        return DEFAULT_TRACK

    if platform in ("AI·热点", "AI·资讯"):
        return "AI 与科技"
    if any(name in text for name in SPORTS_NAMES) or any(e in text for e in SPORTS_EVENTS):
        return "体育与健康"
    if any(name in text for name in ENT_NAMES):
        return "娱乐与情感"
    if any(re.search(pattern, text) for pattern in AI_RUMOR):
        return "社会民生"

    best_track, best_n = None, 0
    for name, words in TRACKS:
        n = _hit_count(text, words)
        if n > best_n:
            best_track, best_n = name, n
    return best_track or DEFAULT_TRACK


def evidence(title: str, platform: str = "") -> tuple[str, list[str]]:
    """返回 (赛道, 命中证据列表)。

    **和 classify 必须给出同一个赛道**，但额外把「怎么判出来的」一并返回。
    为什么要单独一个函数：评分按命中词给赛道分（每词 5 分，上限 15）。
    如果只调 classify 再回词表里找词，实体前置判出来的赛道
    （赵丽颖 → 娱乐与情感）会因为「赵丽颖」不在娱乐词表里而拿到 0 分——
    赛道判对了却显示 0 分，界面上看起来像判错。
    """
    text = (title or "").strip()
    track = classify(text, platform)
    if not text:
        return track, []

    hits: list[str] = []
    if track == "AI 与科技" and platform in ("AI·热点", "AI·资讯"):
        hits.append(platform)
    for name in SPORTS_NAMES:
        if name in text:
            hits.append(name)
    for name in SPORTS_EVENTS:
        if name in text:
            hits.append(name)
    for name in ENT_NAMES:
        if name in text:
            hits.append(name)
    for name, words in TRACKS:
        if name == track:
            hits.extend(word for word in words if word in text)
            break

    seen: set[str] = set()
    return track, [h for h in hits if not (h in seen or seen.add(h))]


def risk_flags(title: str, platform_count: int = 0) -> tuple[list[str], int]:
    """返回 (风险标记, 合计减分)。

    「未证实」只在**单平台**时扣分：多平台同时报道的「网传」往往已被证实，
    扣分会让真热点被压掉。
    """
    text = title or ""
    flags: list[str] = []
    penalty = 0
    for name, words in RISK_WORDS.items():
        if not any(word in text for word in words):
            continue
        if name == "未证实" and platform_count >= 2:
            continue
        flags.append(name)
        penalty += RISK_PENALTY[name]
    return flags, penalty


def track_base(track: str) -> int:
    return TRACK_BASE.get(track, 3)


# ══════════════════════════════════════════════════════════════════════
# ② 归一化
# ══════════════════════════════════════════════════════════════════════

# 平台口吻前缀：去掉它们之后，「如何看待 X」与「X」才能归到一起。
# 这些词不含实体信息，纯粹是提问/点击诱导的壳。
_STRIP_PREFIX = (
    "如何看待", "怎么评价", "如何评价", "为什么", "为何", "什么信号",
    "热搜", "冲上热搜", "登上热搜", "引热议", "引发热议", "引发关注",
    "曝光", "官宣", "最新", "刚刚", "突发", "热议", "网传",
)

# 全角→半角，只处理标点/数字/字母这类常见区段；汉字本身不动。
_FW_MAP = {ord(c): ord(c) - 0xFEE0 for c in
           "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
           "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"}
_FW_MAP[ord("　")] = ord(" ")

# 只保留「汉字 + 英数」：标点、空格、表情、全角符号一次清掉。
# 用白名单而不是黑名单——热榜标题里出现什么符号完全不可控。
_KEEP_RE = re.compile(r"[^0-9a-z一-鿿]+")

# 「X 方回应：已离婚」把「方」当主语后缀，去掉才和「X 回应离婚」对得上。
# 只处理紧跟「回应/表示/称/通报」的形态，不碰「方案」「地方」。
_PARTY_RE = re.compile(r"方(?=回应|表示|称|通报)")


def normalize_title(title: str) -> str:
    """归一化标题：全角转半角、只留汉字与英数、去平台口吻前缀、英文小写。

    **这是聚簇的判据来源，不是展示文本**。展示永远用原始 title。
    """
    text = (title or "").strip()
    if not text:
        return ""
    text = text.translate(_FW_MAP).lower()
    text = _KEEP_RE.sub("", text)
    text = _PARTY_RE.sub("", text)

    changed = True
    while changed:
        changed = False
        for prefix in _STRIP_PREFIX:
            if text.startswith(prefix) and len(text) > len(prefix) + 3:
                text = text[len(prefix):]
                changed = True
                break
    return text


def bigrams(text: str) -> set[str]:
    """字符 2-gram 集合。中文没有空格分词，2-gram 是最稳的近似。"""
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _lcs_subseq_len(a: str, b: str) -> int:
    """最长公共子序列长度（可不连续）。

    为什么不用子串：中文标题的同义改写常是插入/删除虚词——
    「X 回应离婚」vs「X 回应已离婚」只多一个「已」，子串会判成两件事。
    """
    if not a or not b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = cur[j - 1] if cur[j - 1] >= prev[j] else prev[j]
        prev = cur
    return prev[len(b)]


def _dice_lcs(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * _lcs_subseq_len(a, b) / float(len(a) + len(b))


def jaccard(a: str, b: str) -> float:
    """2-gram 集合 Jaccard。两个都为空时返回 0（否则空标题会互相合并）。"""
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / float(len(sa) + len(sb) - inter)


# 泛化模板词：出现在标题里**不构成「同一件事」的证据**。
# 中国热榜标题高度模板化（「某地通报…」「多地调整…」），
# 光看字符串相似度会把模板相同、事件完全不同的两条合并。
GENERIC_WORDS = (
    "某地", "多地", "一地", "某", "通报", "回应", "发布", "通知", "官宣",
    "调整", "中小学", "小学生", "中学生", "上学", "到校", "时间", "小区",
    "业主", "物业", "市民", "网友", "热议", "冲上", "热搜", "引", "曝",
    "最新", "今天", "刚刚", "突发", "消息", "情况", "官方",
)

# 数量/单位片段：共享「亿美元」说明两件事都涉及金额，不说明是**同一件**事。
# 实测误合并：「Anthropic 估值 1.25–1.75 万亿美元」与「黄仁勋失去 1 亿美元股权」。
# 这类片段必须从「稀有词」和「实体证据」里剔除，否则数字越多越容易粘在一起。
MEASURE_WORDS = (
    "万亿美元", "亿美元", "万美元", "亿元", "万元", "亿", "万", "千",
    "个百分点", "百分点", "百分", "同比", "环比", "美元", "港元", "欧元", "日元", "元",
    "公里", "千米", "平方米", "平米", "小时", "分钟", "个月", "周岁", "倍",
    "万股", "岁", "人", "名", "家", "辆", "架", "艘", "条", "只", "张",
)

_NOISE_WORDS = tuple(sorted(GENERIC_WORDS + MEASURE_WORDS, key=len, reverse=True))


_CJK_RE = re.compile(r"[一-鿿]")


def _residual(fragment: str) -> str:
    """去掉泛化模板词与数量单位后剩下的部分。"""
    for word in _NOISE_WORDS:
        fragment = fragment.replace(word, "")
    return fragment


def _meaningful(fragment: str) -> bool:
    """残余片段是否还能当作「同一事件」的证据。

    至少 2 个汉字（专名/术语），或是 3 个以上字母的纯英文片段（品牌/缩写）。
    只按长度判会放过「1亿美」这种残留：去掉单位后还剩「1美」，
    可它仍然只是金额，不是事件。
    """
    residual = _residual(fragment)
    if len(_CJK_RE.findall(residual)) >= 2:
        return True
    return bool(re.fullmatch(r"[a-z]{3,}", residual))

# 已知实体名（人名/机构/品牌）。命中且两边都有 → 强证据。
# 这份表不为精确，只作为「同一事件」的一条证据；漏了不会误合并。
KNOWN_ENTITIES = tuple(SPORTS_NAMES) + tuple(ENT_NAMES) + (
    "华为", "小米", "苹果", "腾讯", "阿里", "字节", "比亚迪", "特斯拉",
    "OpenAI", "DeepSeek", "微信", "支付宝", "抖音", "微博", "知乎", "百度",
)

# 反义守卫：同主体同句式、只有一对反义词不同 → 是**两条对立**的新闻，绝不能合并。
# 实测误合并：降准/降息、上涨/下跌、开幕/闭幕、伤人/抢劫、创新高/下滑。
ANTONYM_PAIRS = (
    ("涨", "跌"), ("上涨", "下跌"), ("上涨", "下滑"), ("上升", "下降"),
    ("增", "减"), ("增加", "减少"), ("增长", "下滑"), ("创新高", "下滑"),
    ("降准", "降息"), ("加息", "降息"), ("升值", "贬值"),
    ("开幕", "闭幕"), ("开始", "结束"), ("解约", "续约"), ("复婚", "离婚"),
    ("伤人", "抢劫"), ("受伤", "身亡"), ("生还", "遇难"),
    ("多", "少"), ("买", "卖"), ("进", "退"), ("赢", "输"), ("胜", "负"),
    ("通过", "否决"), ("批准", "驳回"), ("发布", "撤回"),
)

MERGE_THRESHOLD = 0.56
# 共享一个当天的稀有片段（台风名、品牌名）说明大概率同一件事，但它只加分：
# 单独当判据会让「董事会」「背后的」这类普通三字词变成万能胶水。
RARE_BONUS = 0.10


def _has_antonym_conflict(a: str, b: str) -> bool:
    """a 命中反义词的一侧、b 命中同一对词的另一侧 → 冲突。

    两边都出现同一词时不算冲突（「涨跌」同时出现的标题本来就在讲波动）。
    """
    for x, y in ANTONYM_PAIRS:
        if (x in a and y not in a and y in b and x not in b) or (
            y in a and x not in a and x in b and y not in b
        ):
            return True
    return False


def build_rare_terms(items: list[dict]) -> set[str]:
    """统计当天「稀有片段」：长度 ≥3、在全天内出现次数 ≤3 的汉字片段。

    台风「桦加沙」这类专名，两条标题 Jaccard 只有 0.12，但共享一个罕见 3 字片段，
    显然同一件事。判「罕见」靠当天词频：模板词每天出现几十次，自然被排除。

    必须按当天全量算一次再传给 `similar()`——两两调用里现算会退化成 O(n³)。
    """
    counter: Counter[str] = Counter()
    for item in items:
        norm = item.get("title_norm") or ""
        seen: set[str] = set()
        for i in range(len(norm)):
            for length in (3, 4):
                fragment = norm[i:i + length]
                if len(fragment) == length and fragment not in seen:
                    seen.add(fragment)
        for fragment in seen:
            counter[fragment] += 1
    return {frag for frag, n in counter.items() if n <= 3 and len(frag) >= 3}


def _rare_shared(a: str, b: str, rare_terms: set[str]) -> bool:
    if not rare_terms:
        return False
    for i in range(len(a)):
        fragment = a[i:i + 3]
        if len(fragment) == 3 and fragment in rare_terms and fragment in b:
            if _meaningful(fragment):
                return True
    return False


def _shared_runs(a: str, b: str, min_len: int = 3) -> set[str]:
    """两条字符串的公共子串集合（长度 ≥ min_len）。标题很短，朴素实现够用。"""
    out: set[str] = set()
    length = len(a)
    for i in range(length):
        best = 0
        j = 0
        for k in range(i, length):
            pos = b.find(a[k], j)
            if pos < 0:
                break
            best = k - i + 1
            j = pos + 1
        if best >= min_len:
            out.add(a[i:i + best])
    return out


def _entity_evidence(a: str, b: str) -> int:
    """两条标题共享的「实体证据」条数（0/1）。

    三类证据任一成立即算 1 条：
      1. 共享已知实体名（赵丽颖 / OpenAI / 华为…）
      2. 共享 ≥2 位数字串（「30%」「2026」通常指向同一事实）
      3. 共享 ≥3 字连续片段，且该片段不是泛化模板词
    """
    low_a, low_b = a.lower(), b.lower()
    for entity in KNOWN_ENTITIES:
        el = entity.lower()
        if el in low_a and el in low_b:
            return 1
    for number in re.findall(r"\d{2,}", a):
        if number in b:
            return 1
    for run in _shared_runs(a, b, min_len=3):
        if any(generic in run or run in generic for generic in GENERIC_WORDS):
            continue
        # 3 个字母的英文片段（gen / ace / ter）在标题里随处可见，不构成实体证据；
        # 英文品牌/模型名至少 4 个字母（outlook / gpt6 / nvidia）才算。
        if not _CJK_RE.search(run) and len(run) < 4:
            continue
        if _meaningful(run):
            return 1
    return 0


def evidence_score(a_norm: str, b_norm: str) -> float:
    """同事件置信度 0~1，三项加权：

        0.55 × LCS-Dice       （结构相似：改写/插入虚词仍算同题）
      + 0.25 × 2-gram Jaccard（用词重合）
      + 0.20 × 实体证据       （共享实体/数字/非模板片段）

    第三项不能省：「赵丽颖哄自己上班引热议」与「赵丽颖说自己每天哄自己上班」
    Dice 只有 0.67；而「某地通报小区物业费纠纷」与「某地通报小区火灾」
    Dice 也是 0.67，却是两件事。唯一的区分信号是共享实体。
    """
    return (
        0.55 * _dice_lcs(a_norm, b_norm)
        + 0.25 * jaccard(a_norm, b_norm)
        + 0.20 * _entity_evidence(a_norm, b_norm)
    )


def similar(a_norm: str, b_norm: str, rare_terms: set[str] | None = None) -> bool:
    """两条归一化标题是不是同一件事。

    **判定顺序有讲究：否决优先于相似度。** 合并错误比漏合并代价大得多。
    """
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True

    if _has_antonym_conflict(a_norm, b_norm):
        return False
    ea = {e for e in KNOWN_ENTITIES if e.lower() in a_norm.lower()}
    eb = {e for e in KNOWN_ENTITIES if e.lower() in b_norm.lower()}
    if ea and eb and not (ea & eb):
        return False

    la, lb = len(a_norm), len(b_norm)
    short, long_ = (a_norm, b_norm) if la <= lb else (b_norm, a_norm)
    if len(short) >= 6 and short in long_ and len(short) / float(len(long_)) >= 0.6:
        return True
    score = evidence_score(a_norm, b_norm)
    # 稀有片段是**加分项**，不是硬判据。当硬判据时，一条「董事会」「背后的」
    # 这种当天只出现两三次的普通词组就能把整片 AI 新闻连成一个簇。
    if rare_terms and _rare_shared(a_norm, b_norm, rare_terms):
        score += RARE_BONUS
    return score >= MERGE_THRESHOLD


# ══════════════════════════════════════════════════════════════════════
# ③ 聚簇
# ══════════════════════════════════════════════════════════════════════

class _DSU:
    """并查集：把当天条目按相似度连成团。"""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 小索引当根：保证同一批输入永远得到同样的分组
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def best_title(items: list[dict]) -> dict:
    """从一簇条目里挑展示标题，排序键固定，保证同一输入永远选同一条。

    优先级：平台数多 → 名次靠前 → 长度 12~30 字 → 最早出现。
    """
    def key(item: dict) -> tuple:
        length = len(item.get("title") or "")
        return (
            -int(item.get("platform_count") or 1),
            item.get("best_rank") if item.get("best_rank") is not None else 999,
            0 if 12 <= length <= 30 else 1,
            item.get("first_seen_at") or "",
            item.get("title") or "",
        )

    return sorted(items, key=key)[0]


def cluster_items(items: list[dict]) -> list[list[dict]]:
    """把一天的条目归并成事件簇。返回 [[item, ...], ...]。

    复杂度 O(n²)，n≈250 时约 3 万次比较，实测 <100ms。
    调用方**必须先排序**，否则结果会随输入顺序变化。
    """
    n = len(items)
    dsu = _DSU(n)
    norms = [item["title_norm"] for item in items]
    rare = build_rare_terms(items)
    for i in range(n):
        for j in range(i + 1, n):
            if dsu.find(i) == dsu.find(j):
                continue
            if similar(norms[i], norms[j], rare):
                dsu.union(i, j)
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(items[i])
    return [groups[key] for key in sorted(groups)]


# ══════════════════════════════════════════════════════════════════════
# ④ 评分（100 分制，五项相加后减风险）
# ══════════════════════════════════════════════════════════════════════
#
# 各平台「热度」量纲不同：微博/知乎没有真实热度（只有名次），抖音是播放量，
# 头条是推荐量。所以热度归一**只在同平台内**做，跨平台不比较。

def hot_base_by_platform(items: list[dict]) -> dict[str, int]:
    base: dict[str, int] = {}
    for item in items:
        hot, platform = item.get("hot"), item.get("platform")
        if isinstance(hot, int) and hot > 0:
            base[platform] = max(base.get(platform, 0), hot)
    return base


def _rank_points(best_rank: int | None) -> int:
    if best_rank is None:
        return 0
    if best_rank <= 3:
        return 12
    if best_rank <= 10:
        return 9
    if best_rank <= 20:
        return 6
    if best_rank <= 50:
        return 3
    return 0


def _freshness_points(first_seen_at: str, captured_at: str) -> int:
    """新鲜度。基准是采集时间，不是当前时间——否则重算分数会漂。"""
    try:
        a = datetime.strptime(first_seen_at, "%Y-%m-%d %H:%M")
        b = datetime.strptime(captured_at, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return 1
    hours = (b - a).total_seconds() / 3600.0
    if hours <= 6:
        return 12
    if hours <= 12:
        return 10
    if hours <= 24:
        return 7
    if hours <= 48:
        return 4
    return 1


def _days_points(days_seen: int) -> int:
    if days_seen <= 1:
        return 8
    if days_seen == 2:
        return 6
    if days_seen <= 5:
        return 3
    return 0


def score_cluster(
    members: list[dict],
    captured_at: str,
    hot_base: dict[str, int] | None = None,
) -> tuple[int, dict]:
    """给一个事件簇打分。返回 (总分, detail)。

    members 需要：title / title_norm / platform / rank / hot /
    first_seen_at / prev_rank / best_rank / days_seen
    """
    hot_base = hot_base or {}
    platforms = sorted({m["platform"] for m in members})
    platform_count = len(platforms)

    detail: dict = {
        "items": len(members),
        "platforms": platforms,
        # captured_at 一并存下来：时效分就是按它算的，不存则事后无法复算，
        # 界面也没法显示「这条分数是什么时候的」。
        "captured_at": captured_at,
    }

    # ── 1) 共振 30：多平台同时上榜是最强的「真热点」信号 ──
    resonance = min(30, 8 * platform_count + (6 if platform_count >= 3 else 0))
    detail["resonance"] = resonance

    # ── 2) 势能 25：名次 + 上升 + 热度 ──
    best_rank = min(
        (m.get("best_rank") if m.get("best_rank") is not None else 999) for m in members
    )
    rank_pts = _rank_points(best_rank)

    rise = 0
    for m in members:
        prev_rank, rank = m.get("prev_rank"), m.get("rank")
        if prev_rank is not None and rank is not None and prev_rank > rank:
            rise = max(rise, min(6, prev_rank - rank))
    # 冷启动时 prev_rank 全空 → rise=0，这是如实反映：第一次采集确实不知道在升还是降。

    hot_pts = 0
    hot_evidence: list[str] = []
    for m in members:
        hot, platform = m.get("hot"), m.get("platform")
        if not isinstance(hot, int) or hot <= 0:
            continue
        base = hot_base.get(platform) or 0
        if base <= 0:
            continue
        hot_pts = max(hot_pts, int(round(hot / float(base) * 7)))
        hot_evidence.append(f"{platform} {hot}")
    hot_pts = max(0, min(7, hot_pts))

    momentum = min(25, rank_pts + rise + hot_pts)
    detail.update({
        "momentum": momentum, "best_rank": best_rank,
        "rank_points": rank_pts, "rise_points": rise, "hot_points": hot_pts,
        "hot_evidence": hot_evidence[:3],
    })

    # ── 3) 时效 20：新鲜度 + 连续在榜天数 ──
    earliest = min((m.get("first_seen_at") or captured_at) for m in members)
    fresh_pts = _freshness_points(earliest, captured_at)
    days_seen = max((m.get("days_seen") or 1) for m in members)
    day_pts = _days_points(days_seen)
    detail.update({
        "freshness": min(20, fresh_pts + day_pts),
        "first_seen_at": earliest, "fresh_points": fresh_pts,
        "days_points": day_pts, "days_seen": days_seen,
    })

    # ── 4) 赛道 15：取簇内众数，证据只算判出同一赛道的那几条 ──
    track_votes: dict[str, int] = {}
    per_member_hits: list[tuple[str, list[str]]] = []
    for m in members:
        track_of_member, hits = evidence(m.get("title") or "", m.get("platform") or "")
        track_votes[track_of_member] = track_votes.get(track_of_member, 0) + 1
        per_member_hits.append((track_of_member, hits))
    track = sorted(track_votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    hits: list[str] = []
    for track_of_member, member_hits in per_member_hits:
        if track_of_member == track:
            hits.extend(member_hits)
    seen_hits: set[str] = set()
    hits = [h for h in hits if not (h in seen_hits or seen_hits.add(h))]
    track_pts = min(15, 5 * len(hits))
    if not hits and track == DEFAULT_TRACK:
        track_pts = 0
    detail.update({"track": track, "track_points": track_pts, "track_hits": hits[:6]})

    # ── 5) 可写性 10 ──
    rep = best_title(members)
    rep_title = rep.get("title") or ""
    writ = 0
    writ_evi: list[str] = []
    if re.search(r"\d", rep_title):
        writ += 3
        writ_evi.append("含数字")
    if any(k in rep_title for k in ("回应", "通报", "曝光", "反转", "罚", "禁",
                                    "涨", "跌", "曝", "争议", "承认", "否认")):
        writ += 3
        writ_evi.append("含冲突/反差词")
    if 10 <= len(rep_title) <= 35:
        writ += 2
        writ_evi.append("标题长度适中")
    common: set[str] | None = None
    for m in members:
        bg = bigrams(m.get("title_norm") or "")
        common = bg if common is None else (common & bg)
    if common and len(common) >= 2:
        writ += 2
        writ_evi.append("有共同实体词")
    writ = min(10, writ)
    detail.update({
        "writability": writ, "writability_evidence": writ_evi,
        "representative": rep_title,
    })

    # ── 6) 风险（减分，不删除）──
    flags, penalty = risk_flags(rep_title, platform_count=platform_count)
    # 簇里任何一条踩风险词都算——宁可高估风险，也不要把写不了的题材排到最前。
    for m in members:
        extra_flags, _ = risk_flags(m.get("title") or "", platform_count=platform_count)
        for flag in extra_flags:
            if flag not in flags:
                flags.append(flag)
                penalty += RISK_PENALTY.get(flag, 0)
    penalty = max(-30, penalty)
    detail.update({"risk_flags": flags, "risk_penalty": penalty})

    total = max(0, min(100, resonance + momentum + detail["freshness"] + track_pts + writ + penalty))
    detail["total"] = total
    detail["evidence"] = writ_evi
    return total, detail


def score_grade(score: int) -> str:
    """档位给界面直接用，阈值只写在这一处，前端不再自己算。"""
    if score >= 70:
        return "强烈推荐"
    if score >= 55:
        return "可做"
    if score >= 40:
        return "备用"
    return "不建议"


def fingerprint(title_norm: str) -> str:
    """簇的稳定标识。一旦写入不再改——改了会让历史稿件的来源关联漂移。"""
    return (title_norm or "")[:120]
