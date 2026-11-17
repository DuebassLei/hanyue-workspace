# 积分、会员与权限体系设计

**文档版本：** 1.0
**状态：** 设计定稿（待实现）
**日期：** 2026-11-17
**关联：** `docs/design.md`（产品基线）、`apps/api/app/services/ai_gateway.py`（模型出口）、`apps/api/app/core/db.py`（迁移机制）

---

## 1. 范围与前提

本设计回答一个问题：**多用户实例上，谁的什么操作要花多少积分，谁能改这个数字，谁有权限用哪个模型。**

三条前提来自现有代码，不可假设推翻：

| 现状 | 位置 | 对本设计的影响 |
|---|---|---|
| 模型连接按 `owner_user_id` 隔离，密钥加密存储 | `models.ModelConnection` | 需引入**平台级连接**，用户不再自建 |
| 网关不采集真实 token，只上报 `promptChars` | `ai_gateway.stream_text()` | **按 token 计费不可行**，改为按次固定价 |
| 无 role 字段，鉴权只到「是否本人数据」 | `models.User` / `core/deps.py` | 权限体系从零新增 |
| 迁移靠 `create_all` + `_ADDED_COLUMNS` 补列，无 Alembic | `core/db.py` | 只加列/加表；**不可改已有列的可空性** |

---

## 2. 核心公式

```
实扣积分   = round(动作基础分 × 模型倍率 × 会员折扣系数)
充值到账   = 充值面额 × 会员加成系数
预估展示额 = ceil(实扣积分)          # 前端只展示，不参与计算
```

| 档位 | `discount_factor` | `recharge_bonus` | 模型门槛 |
|---|---|---|---|
| normal（默认） | 1.00 | 1.00 | 经济型 / 标准型 |
| silver | 0.85 | 1.15 | + 旗舰型 |
| gold | 0.70 | 1.30 | 全部 |

> **让利警告**：gold 的实际单价为 `0.70 / 1.30 = 0.538`，即真实让利 **46.2%**，不是 30%。定价时不可按「打七折」估算毛利。详见 §11。

### 2.1 动作基础分（管理员可配）

| action | 含义 | 默认基础分 | 单次输出上限 |
|---|---|---|---|
| `idea_expand` | 选题扩展 | 5 | 800 字 |
| `outline` | 大纲 | 8 | 1500 字 |
| `article` | 初稿 | 20 | 2000 字 |
| `rewrite` | 改写 | 12 | 2000 字 |
| `polish` | 润色 | 6 | 见 §11.1 |
| `summarize` | 摘要 | 4 | 1000 字 |
| `platform_variant` | 平台版本 | 10 | 1500 字 |
| `image_prompt` | 配图提示词 | 3 | 600 字 |
| `novel` | 小说文本处理 | 15 | 2000 字 |
| `image_generate` | AI 生图 | 15 | 1 张 |
| `card_render` | 图文卡片生成 | 0 | 本地渲染，不计分 |

超出单次上限时**后端自动分段**多次调用，每段独立冻结与结算。价目表里的分永远等于「一段」。

### 2.2 模型倍率

写在 `model_catalog_entries.credit_multiplier`（float，默认 1.0）。参考值：

| 模型档位 | 示例 | `credit_multiplier` |
|---|---|---|
| 经济型 | deepseek-chat、qwen-plus | 1.0 |
| 标准型 | gpt-4o-mini、claude-haiku | 2.0 |
| 旗舰型 | gpt-4o、claude-sonnet | 6.0 |

---

## 3. 数据模型

### 3.1 新增表

```python
class CreditWallet(Base, TimestampMixin):
    """积分钱包：余额快照。真相在 credit_transactions，本表只做快速读取与并发控制。"""
    __tablename__ = "credit_wallets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    frozen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_recharged: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_consumed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```

不变式（应用层 + 唯一索引兜底，**不加 CHECK 约束**，因为 SQLite 补约束必须重建表）：

- `balance >= 0`
- `frozen >= 0`
- `frozen <= balance`

```python
class CreditTransaction(Base):
    """只追加流水。任何余额变动必须同时写一条，同事务。"""
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    # hold / consume / release / recharge / grant / admin_adjust / membership_bonus
    kind: Mapped[str] = mapped_column(String(24), index=True)
    balance_delta: Mapped[int] = mapped_column(Integer, default=0)
    frozen_delta: Mapped[int] = mapped_column(Integer, default=0)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    frozen_after: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(200), default="")
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 扣费时的价格快照：动作/基础分/倍率/折扣/最终分，改价不追溯的依据
    price_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # 平台真实成本（元），仅内部核算，不下发给用户
    platform_cost: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

```python
class ActionPrice(Base, TimestampMixin):
    """动作基础分（管理员可改）。tiers 用于按输入长度分档，见 §11.1。"""
    __tablename__ = "action_prices"

    action: Mapped[str] = mapped_column(String(48), primary_key=True)
    label: Mapped[str] = mapped_column(String(48))
    base_points: Mapped[int] = mapped_column(Integer, default=0)
    max_output_chars: Mapped[int] = mapped_column(Integer, default=2000)
    tiers: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

```python
class MembershipTier(Base, TimestampMixin):
    """三档会员参数。档位是产品核心配置，必须可查可审计，不放 app_settings。"""
    __tablename__ = "membership_tiers"

    tier: Mapped[str] = mapped_column(String(16), primary_key=True)  # normal/silver/gold
    label: Mapped[str] = mapped_column(String(32))
    discount_factor: Mapped[float] = mapped_column(Float, default=1.0)
    recharge_bonus: Mapped[float] = mapped_column(Float, default=1.0)
    price_month_cents: Mapped[int] = mapped_column(Integer, default=0)
    price_year_cents: Mapped[int] = mapped_column(Integer, default=0)
    sort: Mapped[int] = mapped_column(Integer, default=0)
```

```python
class RedeemCode(Base):
    __tablename__ = "redeem_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(32), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    tier_grant: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_by: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 兑换码自身有效期（不是积分有效期——积分永不过期）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
```

```python
class MembershipLog(Base):
    """会员变更留痕：升级/续费/降级/管理员调整，全部可回溯。"""
    __tablename__ = "membership_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    tier_from: Mapped[str] = mapped_column(String(16))
    tier_to: Mapped[str] = mapped_column(String(16))
    days: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

### 3.2 改动已有表（走 `_ADDED_COLUMNS`）

| 表 | 新列 | DDL |
|---|---|---|
| `users` | `role` | `VARCHAR(16) DEFAULT 'user'` |
| `users` | `membership_tier` | `VARCHAR(16) DEFAULT 'normal'` |
| `users` | `membership_expires_at` | `TIMESTAMP` |
| `model_catalog_entries` | `credit_multiplier` | `FLOAT DEFAULT 1.0` |
| `model_catalog_entries` | `min_tier` | `VARCHAR(16) DEFAULT 'normal'` |
| `model_connections` | `scope` | `VARCHAR(16) DEFAULT 'user'` |

**新增唯一索引**（写进 `core/db.py::_INDEXES`）：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_idem ON credit_transactions (idempotency_key)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_user ON credit_wallets (user_id)
```

### 3.3 平台级模型连接：用系统用户占位

`model_connections.owner_user_id` 现在是 `NOT NULL`，而 SQLite **无法通过 `ALTER TABLE` 去掉非空约束**（要重建表）。因此不引入可空 owner，改为：

1. 启动时幂等插入一个系统用户：`id='system'`、`auth_subject='system:platform'`、`display_name='平台'`、`is_active=false`
2. 平台模型连接的 `owner_user_id = 'system'`、`scope='platform'`
3. 所有「按 owner 查连接」的查询改为查询平台池，**用户连接仅在 `billing.byok_enabled=true` 时参与候选**

好处：零列约束改动，迁移脚本只需 `INSERT OR IGNORE`。代价：`users` 表多一行系统账号，所有「统计用户数」的地方需排除 `id='system'`。

---

## 4. 扣费生命周期

```
点「初稿」
  └─► ① 冻结：原子扣减可用额度，写 hold 流水，余额不足返回 402
        └─► ② 调用上游（流式，单次封顶）
              ├─► 正文落库 ──► ③ 确认：balance -= n，写 consume 流水
              └─► 失败/取消/空产出 ──► ④ 释放：frozen -= n，写 release 流水
```

### 4.1 原子性与并发

冻结用单条 SQL 完成「检查 + 扣减」，不做先查后改：

```sql
UPDATE credit_wallets
   SET frozen = frozen + :n,
       version = version + 1,
       updated_at = :now
 WHERE user_id = :uid
   AND balance - frozen >= :n
```

- 影响行数 = 0 → `402 insufficient_credits`，响应体带 `{required, available}`，**不发上游请求**
- 确认 / 释放同理，各自一条 `UPDATE ... WHERE frozen >= :n`，0 行则说明状态已变，记警告但不重试

### 4.2 幂等键规范

| 场景 | `idempotency_key` |
|---|---|
| 单次动作冻结 | `hold:{generation_id}:{segment_index}` |
| 确认扣减 | `consume:{generation_id}:{segment_index}` |
| 释放 | `release:{generation_id}:{segment_index}` |
| Agent 单步 | `hold:agent:{run_id}:{step_index}` |
| 管理员充值 | `recharge:{admin_action_id}` |
| 兑换码 | `redeem:{code}` |

唯一索引冲突即视为「已处理」，直接返回上次结果，不重复变动余额。

### 4.3 「成功」的判定标准

**必须是「正文已落库」，不是「HTTP 200」。** 现有网关在 429 降级与部分失败时会先吐若干 `generation.delta` 再吐 `generation.error`；若按状态码判定，用户会为半句话付全款。

---

## 5. 价格解析与报价接口

统一入口 `services/billing.py`：

```python
@dataclass(frozen=True)
class Quote:
    action: str
    base_points: int
    multiplier: float
    discount: float
    points: int              # round(base * multiplier * discount)
    snapshot: dict           # 落进流水的 price_snapshot

def quote(db, user, action: str, model_id: str | None) -> Quote: ...
```

规则：

1. `action_prices` 无该 action 或 `enabled=false` → `400 action_not_billable`
2. 模型不存在 / 非文本能力 → `400 invalid_model`
3. `effective_tier(user) < model.min_tier` → `403 model_requires_tier`，响应带 `{requiredTier, currentTier}`
4. `points = 0` 的动作（如卡片渲染）不写流水，只写 `ai_generations`
5. **报价仅供展示**：真正的扣费在服务端重新 `quote()`，不信任前端传值

---

## 6. 会员体系

### 6.1 惰性降级（不用定时任务）

```python
def effective_tier(user, now) -> str:
    tier = user.membership_tier or "normal"
    if tier == "normal":
        return "normal"
    exp = user.membership_expires_at
    if exp is None or exp <= now:
        return "normal"
    return tier
```

所有权益判定都走这个函数，**不依赖调度器**。读取时可顺手把过期字段修正回 `normal`（写一条 `membership_logs`），但不修正也绝不出错——这是关键：调度器挂了不会出现「过期半年还在享 7 折」。

### 6.2 有效期计算

- 购买/授予 N 天：`new_exp = max(now, old_exp or now) + N days`
- 同档续费 → 累加；**跨档变更 → 以新档为准，剩余天数折算不清零**（简化：直接覆盖到期日，不折算，成本可忽略且规则好解释）
- 降级：过期自动，或管理员手动

### 6.3 权益清单

| 权益 | normal | silver | gold |
|---|---|---|---|
| 消耗折扣 | ×1.00 | ×0.85 | ×0.70 |
| 充值加成 | ×1.00 | ×1.15 | ×1.30 |
| 旗舰模型 | 置灰不可选 | 可用 | 可用 |
| 单次输出上限 | 2000 字 | 3000 字 | 5000 字 |
| Agent 最大步数 | 4 | 6 | 10 |

> 界面必须写死一句：**「会员 = 更便宜地消耗积分」**，不能让人以为会员费能抵积分。这是最高频的客诉来源。

---

## 7. 权限模型

> **本节已被 RBAC 取代。** 实际实现见 `docs/rbac.md`：不再有 `users.role` 单字段、
> 也不用 `ADMIN_EMAILS` 白名单，改为「用户 — 角色 — 权限点」多对多 + `is_superadmin` 布尔位。
> 下方内容仅保留当时的设计推理，供对照。

### 7.1 管理员身份（历史设计，勿照此实现）

```python
def require_admin(user: CurrentUser) -> models.User:
    if user.role != "admin":
        raise forbidden("需要管理员权限")
    return user
AdminUser = Annotated[models.User, Depends(require_admin)]
```

三层来源，职责分明：

| 层 | 作用 | 是否可省 |
|---|---|---|
| `users.role` | **唯一鉴权依据**，读一次用户行即可判断 | 不可 |
| `ADMIN_EMAILS` 环境变量 | 启动时幂等提升为 admin，**救援通道** | 不可 |
| `BOOTSTRAP_FIRST_USER_ADMIN` | 首个注册用户自动提权，默认 `true`，公网部署应设 `false` | 可关 |

救援场景：field 被改坏、你换了邮箱注册新号、误把自己降权——改环境变量重启即可恢复，不必进库手改。

### 7.2 管理员能做什么，不能做什么

| 允许 | 禁止 |
|---|---|
| 改动作基础分、模型倍率、模型门槛 | **浏览用户正文/稿件内容** |
| 给用户加/减积分（必填 reason） | 直接改 `balance` 字段（必须走流水） |
| 授予/延长/取消会员 | 删除 `credit_transactions` 记录 |
| 批量生成兑换码 | 修改已发生的价格快照 |

**所有管理操作必须留痕**：调账写 `operator_id`；改价写 `updated_by`；会员变更写 `membership_logs`。

后台只提供**聚合视图**（用量、积分、会员分布），不提供正文浏览——这是隐私红线，也是团队扩张后唯一守得住的约定。

---

## 8. 积分入口

| 入口 | 实现 | 说明 |
|---|---|---|
| 注册赠送 | 注册成功同事务写 `grant` 流水，默认 50 分 | 让新用户能真正写完一篇文章；真实成本约 0.2 元，全站 ROI 最高的一笔钱 |
| 管理员充值 | `POST /admin/users/{id}/credits` | 必填 `reason`，写 `admin_adjust` |
| 兑换码 | 管理员批量生成 → 用户 `POST /billing/redeem` | 线下收款发码，**不需要接支付即可闭环** |
| 在线支付 | 阶段二 | 需订单表 + 回调幂等 + 对账 + 退款，不在本期 |

兑换码规则：

- 一次性使用，`redeemed_by` 非空即失效
- 未兑换前可设过期时间；**已兑换的积分永不过期**
- 兑换同时可授予会员（`tier_grant` + `duration_days`）

---

## 9. Agent 会话计费

`agent_runtime` 一次会话是「规划 → 多工具 → 可能再规划」的循环，若每工具静默扣一次，用户会认为在偷钱。规则：

1. **开跑前预告**：规划完成后返回 `estimatedSteps` 与 `estimatedPoints`，前端弹确认
2. **逐步冻结**：每个工具调用前按该工具映射的 action 冻结一次，写 `price_snapshot`
3. **硬上限**：步数超过档位上限（normal 4 / silver 6 / gold 10）直接终止并提示
4. **余额中断**：任一步冻结失败 → 会话暂停为 `paused_insufficient_credits`，已完成步骤照常结算，不整体回滚（回滚等于白送上游成本）

工具 → action 映射写在 `agent_policy.TOOL_POLICY` 旁边，与风险分级同表维护。

---

## 10. 接口清单

### 用户侧 `/api/v1/billing`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/me` | `{balance, frozen, available, tier, expiresAt, discount, bonus}` |
| GET | `/quote?action=&modelId=` | 报价与拆解，供按钮上的「14 分」使用 |
| GET | `/transactions?cursor=` | 流水（keyset 分页，与 `jobs` 一致） |
| POST | `/redeem` | `{code}` |

### 管理侧 `/api/v1/admin`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PATCH | `/action-prices` | 动作基础分 |
| PATCH | `/models/{id}/pricing` | `{creditMultiplier, minTier}` |
| POST | `/users/{id}/credits` | `{points, reason}` |
| POST | `/users/{id}/membership` | `{tier, days, reason}` |
| POST | `/redeem-codes/batch` | `{points?, tier?, days?, count}` |
| GET | `/billing/overview` | 聚合，无正文 |

错误码：`402 insufficient_credits`、`403 model_requires_tier`、`409 credit_conflict`（幂等键冲突且状态不一致）、`400 action_not_billable`。

---

## 11. 已知风险与待决事项

### 11.1 「按次」对润色/摘要是错配的（需决策）

初稿有封顶能控住，但润色的**输入**等于全文：用户拿 8000 字点润色扣 6 分，上游成本可能是初稿的 2 倍。

三种解法，建议选 A：

- **A（建议）**：`polish` / `summarize` 按输入字数分档，写进 `action_prices.tiers`
  `[{maxChars: 2000, points: 6}, {maxChars: 5000, points: 15}, {maxChars: -1, points: 30}]`
- B：限定单次输入长度（超长强制先分段），实现简单但体验割裂
- C：两者都不做，接受这两类动作亏钱换体验

### 11.2 双轨优惠叠加后让利 46%

`0.70 / 1.30 = 0.538`。若按「七折」估毛利，会低估让利一半。定价（充值包面额、会员价）必须按 **0.538** 重算，且 silver（`0.85/1.15 = 0.739`）同样不是 85 折。

### 11.3 存量 `model_connections` 迁移

开发实例里已有用户自建连接。二选一，**必须在写迁移脚本前定**：

- **A（建议）**：`UPDATE model_connections SET scope='platform', owner_user_id='system' WHERE owner_user_id=<你的 uid>`，保留配置
- B：全部清空，上线后由管理员重新登记

### 11.4 注册赠送被刷

50 分 × N 个邮箱 = 免费额度被刷。阶段一靠邀请码/人工审核控制注册；接支付前需补手机号验证或设备指纹。

### 11.5 Agent 会话可能「跑飞」

预告步数基于首次规划，失败重试会导致实际步数超预期。硬上限（§9.3）是唯一可靠的刹车，不可省。

---

## 12. 验收清单

1. 余额 0 用户点「初稿」→ 402，**上游无任何请求**（查日志确认）
2. 并发连点 5 次「初稿」，余额只够 2 次 → 2 成功 3 拒绝，余额与流水完全对得上
3. 上游 429 失败 → 有 `release` 流水，余额回到发起前，用户明细可见
4. 流中断但已有正文落库 → 按成功扣费（不是按 HTTP 状态）
5. 同一 `generation_id` 重放扣费接口 3 次 → 只产生 1 条 `consume` 流水
6. silver 用户：报价 = `round(base × mult × 0.85)`，与 `price_snapshot` 一致
7. normal 用户选旗舰模型 → 下拉置灰 + 服务端 403（前端绕过也拦住）
8. 会员过期后立刻（无需重启/定时任务）享受 normal 价格
9. 管理员改基础分 → 已冻结的进行中任务按旧价结算
10. 管理员调账 → 流水有 `operator_id`，`balance_after` 与钱包一致
11. 兑换码二次兑换 → 409，余额不变
12. 流水全表 `SUM(balance_delta)` = 钱包 `balance`（对账脚本常驻）
