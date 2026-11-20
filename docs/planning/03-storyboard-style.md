# 漫画分镜 · 组级统一风格规划（v2）

> 状态：**M1–M4 已实现并通过定向验证**。需求 03：同一分镜组（项目）内的图片要统一风格，并可选择出图风格。
>
> **v2 变更**：经 2026-11-19 盘问，风格数据源由「统一 style_presets 注册表」改为「手绘风格库 handdraw_styles（331 档）」，实质推翻 v1 §8 D3。v1 的 RCA 表与现成范式仍有效，保留于 §1/§1.1。

---

## 1. 现状 RCA（v1 保留，仍然成立）

| 现状 | 证据 | 后果 |
|---|---|---|
| 分镜三张表零风格字段 | `StoryboardProject`/`Scene`/`Shot`（models.py:850-899）无 style/seed/prompt_suffix | 没有地方存"这一组用什么画风" |
| 出图不拼风格 | `_queue_generation`（storyboards.py:794-890）prompt = subject + 参考图标签 + 硬编码角色一致性句，无风格后缀、无 seed | 每个镜头画风靠模型默认行为，跨镜头易漂移 |
| `styles` 资产可被挤掉 | `_storyboard_references`（391-425）参考图槽位最多 4，超额"先丢风格"（418-422） | 想要统一风格也保证不了 |
| 请求体风格字段被忽略 | `StoryboardGenerateIn.reference_asset_ids` 定义了但入队逻辑明确不使用 | 接口形同虚设 |
| 无 seed | `image_provider.generate_images`（257-266）全链路无 seed 参数（地基契约后已支持透传） | 无法用固定种子锁画风 |

**现成范式**（v1 保留）：
- 角色 4 档 `CHARACTER_STYLES` + 贴图 6 档 `sticker_styles.STYLES`（执行期只拼一次，job_runner:489-496）。
- 手绘库激活机制：`builtinReferenceStyleNumber` + `_builtin_reference`（job_runner:451-482）——选了风格却读不到参考图就明确失败，不静默降级。
- 配置快照 `characterSnapshot`（storyboards.py:858-872）。

### 1.1 两套风格体系现状（v2 新增）

| 体系 | 位置 | 规模 | 生效方式 |
|---|---|---|---|
| `style_presets`（统一注册表） | `services/style_presets.py`，代码级常量 | 10 档（角色 4 + 贴图 6） | `prompt_suffix` 字符串 |
| 手绘风格库 | `handdraw_styles` 表 + `resources/handdraw/styles.json`，导入只读 | **331 档**，FA/FB/FC/FD/FE 五组 | `resolve_activation`（按模型 traits_activation 决定「名称+风格+特征词」或「名称+风格+参考图」）+ `compose_prompt`（双语风格名/参考作者/特征词/参考图说明）+ 内置参考图 webp |

手绘库 331 档在当前资源快照下**全部带参考图**。规划早期以为 FB-035 起约 30 档「仅作者名、无参考图」，核对 `styles.json` + `refs/` 后不成立：空 traits 的 16 档（FC-001、FG-008 起等）也都有参考图，选择器按可用性过滤后仍全部可选。`style_unusable` 校验保留，给以后导入缺图的风格兜底。

---

## 2. 目标 / 非目标

**目标**
1. 一个分镜项目可**选定并保存一个统一出图风格**，来源为**手绘风格库**，作用到该项目所有场景、所有镜头。
2. 风格生效完整复用手绘库激活机制（`resolve_activation`）与风格段拼装，不造第二套解释逻辑。
3. 风格以「手绘风格句（置于 prompt 最前）+ 固定风格参考图」组合生效；seed/负向词沿用地基契约管道（供应商不支持时 UI 明示）。
4. 入队时把生效风格**快照进 job payload**（`styleSnapshot`），历史镜头可追溯。
5. 前端提供分组+预览的风格选择器，按可用性过滤。

**非目标**
- 不改 AI 拆解逻辑（风格是用户配置项，不是 AI 拆解产物）。
- 不做自动识别已有图片风格并反向统一。
- 手绘库的**主题色体系**（`HanddrawColor`）本期不接入分镜（Q4）：`style_config` 结构预留但不做 UI。
- 提示词库（需求 02）**不改数据源**（Q5）：继续用 `style_presets` 10 档。两套体系就此分道：分镜独用手绘库，提示词库独用 style_presets。
- 不做批量重生成历史镜头。

---

## 3. 数据模型设计

### 3.1 `StoryboardProject.style_config`（JSON 列，v2 改指向手绘库）

```jsonc
{
  "style_number": "FA-001",         // 手绘风格库 style_number（v1 的 style_id 改名，指向 handdraw_styles）
  "generation_name": "Playful Deadpan Doodle",  // 冗余存，便于列表展示与追溯
  "group_key": "FA",
  "lock_mechanism": "reference"     // "reference" | "traits"：该风格在选定模型下的锁定方式，入队时由 resolve_activation 写入
}
```

- `prompt_suffix` 不再冗余存储——手绘风格段由 activation + compose 动态决定（依赖 model_code），存静态字符串会失真；生效文本以 job payload 的 `styleSnapshot` 为准（§4.1）。
- **场级覆盖（已实现，2026-11-20）**：`StoryboardScene.style_config` 同结构字段，解析链为**场级非空用场级、否则回落项目级**；场景 Create/PATCH 走同一 `_normalize_style_config` 校验（`{}`/null = 清除覆盖），`styleSnapshot` 增加 `source: scene|project` 记录实际生效来源，无风格时快照保持 None（未选风格 prompt 逐字节不变的回归断言保留）。前端在场景树上提供覆盖徽标与「场景画风」弹窗（未覆盖时明示"跟随项目风格"）。
- 同步改 `StoryboardProjectCreate/Patch/Out`（schemas.py:1125-1134, 1228-1238）。

### 3.2 数据源（v2 重写，推翻 v1 §8 D3）

- 分镜风格选择器数据源 = `handdraw_styles` 表（现有只读导入管道 `handdraw.import_library` / `ensure_library` 不动）。
- **不新增接口**：复用手绘库现有风格列表接口（HanddrawView 已消费的那份），前端按 `group_key` 分组渲染即可；如该接口缺少 `reference_available` 字段则补下发。
- v1 §8 D3「分镜与提示词库共用统一注册表」作废，改记为：**分镜独用手绘库，提示词库独用 style_presets**。

---

## 4. 出图链路改造（核心）

### 4.1 入队拼装（Q7：入队时拼，仿 characterSnapshot 模式）

`_queue_generation`（storyboards.py:794-890）在 modelId 已知处：

1. `style_config.style_number` → 取 `HanddrawStyle` 行 → `resolve_activation(style, model_code)`。
2. 新增 `handdraw.compose_style_segment(style, activation, attach_reference)`：返回风格段字符串 = 「风格名称 + 参考作者/风格名称 + 核心风格特征（include_traits 时）+ 参考图说明」四句，语言按 `detect_language(subject)` 取 promptZh/promptEn 对应段；**跳过主题句与颜色句**（主题由分镜 subject 承担，颜色本期不做）。
3. `compose_prompt` 内部改调该函数拼风格四句——手绘出图行为零变化，只有一处拼装逻辑。
4. prompt 拼装：`style_segment + "\n" + subject + "\n" + labels + "\n" + consistency`（风格句置于最前，作全局画风锚点）。
5. payload 写入：
   - `builtinReferenceStyleNumber`：风格参考图**沿用 runner 现有路径** `_builtin_reference`（job_runner:451-482），不新开常驻槽逻辑；
   - `styleSnapshot`：`{ style_number, generation_name, group_key, reference_author, traits(生效后), activation_mode, model_code, language, prompt_head(最终风格段) }`，供 `_generation_prompt` 与导出（M5）复用。

### 4.2 参考图槽位策略（Q8：入队预扣）

- 项目**选了风格**时：分镜用户参考图（角色/地点）上限 4 → **3**，风格图固定占第 1 槽；用户素材超额时入队时按既有丢弃顺序处理（**先丢地点**，v1 验收第 2 条语义不变），不报错。
- 项目**未选风格**时：维持 4 张，行为与现状完全一致（零迁移）。
- 风格参考图缺失且 activation 判定需要参考图 → 沿用 `_builtin_reference` 的 `JobFailure`（明确失败不静默降级）；但该场景在选择器过滤后（§6 Q10）用户基本不可达。

### 4.3 改动影响面

| 位置 | 改动 |
|---|---|
| `services/handdraw.py` | 新增 `compose_style_segment`；`compose_prompt` 改调它（行为零变化） |
| `modules/storyboards.py` | `_queue_generation` 拼装 + 快照 + 预扣槽位；PATCH 项目接口接收 style_config（校验 style_number 存在且可用性达标）；`_project_out` 下发 |
| `services/job_runner.py` | **基本不动**：`_builtin_reference` / negativePrompt / seed 管道现成 |
| `schemas.py` | project 三 schema 增 style_config；`reference_asset_ids` 维持忽略（v1 Q4 结论不变） |
| 前端 | `StoryboardWorkspace.vue` 顶部风格选择器；`ShotInspector.vue` generate() 携带项目 style_config；`useStoryboard.js` 加 saveStyleConfig |

---

## 5. 一致性保证策略（v2 更新）

| 手段 | 作用 | 本期 |
|---|---|---|
| 组级手绘风格句置前 | 所有镜头 prompt 头部相同 → 画风锚定 | ✅ 核心 |
| 手绘库内置风格参考图常驻（`_builtin_reference` 路径） | 视觉参考一致；槽位预扣保证不被挤掉 | ✅ 核心 |
| 角色库参考图 + 一致性句 | 角色跨镜头一致（现有） | ✅ 保留 |
| 正式图用已通过草图锁构图 | 单镜头构图一致（现有） | ✅ 保留 |
| 固定 seed | 依赖 provider，不支持则 UI 禁用（v1 Q3 不变） | ⚠️ 能力探测 |

UI 如实标注各手段生效条件：锁定方式为 `traits` 的风格（无参考图）一致性弱于 `reference`，在选择器中以图钉 icon 区分（Q10）。

---

## 6. 本轮盘问决策记录（12 项，✅ 全部已确认）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 分镜风格来源 | **纯手绘库**，旧 10 档从 comic_panel 场景移除；D3 实质推翻并改写 |
| Q2 | 生效机制 | **完整复用** `resolve_activation` + `compose_prompt` 逻辑，不降级为简单后缀 |
| Q3 | 参考图缺失 | 选择器过滤为主（见 Q10），不可达场景沿用明确失败 |
| Q4 | 主题色 | 本期不接入，结构预留 |
| Q5 | 提示词库数据源 | 不变（style_presets 10 档）；D3 改写为两库分道 |
| Q6 | 选择器 UI | 按 `group_key` 分组 + 搜索 + 参考图缩略预览；不整体搬 HanddrawView |
| Q7 | 拼装时机与快照 | **入队时拼**；payload 带 `builtinReferenceStyleNumber` + `styleSnapshot` |
| Q8 | 槽位策略 | **入队预扣**：有风格时用户参考图上限 3，风格图占第 1 槽；无风格维持 4 |
| Q9 | 风格是否强制 | **可选不强制**；未选 = 现状行为，UI 温和提示 |
| Q10 | 过滤口径 | **按可用性过滤**：有参考图，或特征词强到无需参考图。当前 331 档全部有参考图，过滤后仍全可选；`style_unusable` 留给以后缺图的导入。列表标注锁定方式 |
| Q11 | 风格段组成与语言 | 四句（风格名+参考作者+特征词+参考图说明），语言按 `detect_language(subject)`；新增 `compose_style_segment`，`compose_prompt` 改调它 |
| Q12 | 落地顺序 | 先重写本规划稿（本文档）→ 用户确认 → 再排 M1–M5 实施 |

---

## 7. 分阶段实施

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 数据模型 | project `style_config`；schema 与 PATCH/OUT 校验下发 | ✅ |
| M2 入队拼装 | `compose_style_segment` + `_queue_generation` 拼装/快照/预扣 | ✅ |
| M3 前端 | 分组+预览选择器（可用性过滤 + 锁定方式标注）+ 保存 | ✅ |
| M4 导出/追溯 | `_shot_out` 下发 `styleSnapshot`；prompt-pack 导出快照摘要 | ✅ |
| ~~seed/负向~~ | 地基契约管道已贯通，分镜侧只需在 UI 按能力明示 | 无 |

（v1 的 M3「seed/negativePrompt 透传至 provider」已完成于地基契约，不再单列。）

---

## 8. 验收标准（v2 更新）

1. 建项目选手绘风格（如 FA-001），拆解出多个镜头并全部出草图：各镜头提示词头部含同一风格段（风格名+参考作者+特征词），且都带同一张手绘库参考图（占第 1 槽）。
2. 选风格的项目，用户参考图（角色/地点）超额时丢的是地点，风格参考图永不被挤掉；未选风格的项目维持旧 4 槽行为。
3. 改项目风格后，历史镜头仍能追溯旧风格（`styleSnapshot`），新镜头用新风格；快照含 activation_mode 与生效特征词。
4. 选择器：按 `group_key` 分组展示、可搜索、带参考图缩略预览；无参考图且无可用特征词的风格不可选（当前资源快照下没有这样的档）；`traits`/`reference` 锁定方式有区分标注。
5. 未选风格项目出图 prompt 与现状逐字节一致（回归保障）。
6. 手绘页面出图 prompt 不变（`compose_prompt` 重构零行为变化的回归断言）。
7. v1 验收第 4 条（seed/负向词能力明示）与现有测试断言按新语义更新并通过。
