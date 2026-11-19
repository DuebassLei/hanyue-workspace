# 漫画分镜 · 组级统一风格规划

> 状态：**规划稿，未实现**。需求 03：同一分镜组（项目）内的图片要统一风格，并可选择出图风格。

---

## 1. 现状 RCA

| 现状 | 证据 | 后果 |
|---|---|---|
| 分镜三张表零风格字段 | `StoryboardProject`/`Scene`/`Shot`（models.py:850-899）无 style/seed/prompt_suffix | 没有地方存"这一组用什么画风" |
| 出图不拼风格 | `_queue_generation`（storyboards.py:794-890）prompt = subject + 参考图标签 + 硬编码角色一致性句，无风格后缀、无 seed | 每个镜头画风靠模型默认行为，跨镜头易漂移 |
| `styles` 资产可被挤掉 | `_storyboard_references`（391-425）参考图槽位最多 4，超额"先丢风格"（418-422）；测试 test_storyboards.py:348 断言风格被挤掉 | 想要统一风格也保证不了，风格参考图反而是最先被牺牲的 |
| 请求体风格字段被忽略 | `StoryboardGenerateIn.reference_asset_ids` 定义了但入队逻辑明确不使用；前端只发 `modelId` | 接口形同虚设 |
| 无 seed | `image_provider.generate_images`（257-266）全链路无 seed 参数 | 无法用固定种子锁画风 |

**今天维持"一致"的只有三样**：角色库参考图（按名字/景别选图）、硬编码角色一致性英文句、正式图用已通过草图锁构图。**没有跨镜头统一画风**。

**现成范式可直接复用**（避免造第三套风格体系，呼应总览 D3）：
- 角色 4 档风格 `CHARACTER_STYLES = manga/realistic/guoman/watercolor`（schemas.py:1077）+ `character_images.STYLE_WORDS` 中文词。
- 贴图 6 档 `sticker_styles.STYLES`（含英文 `prompt_suffix`），且后缀"执行期只拼一次"（job_runner:489-496）。
- 手绘 `builtinReferenceStyleNumber` + `_builtin_reference`（job_runner:451-482）——"选了风格却读不到参考图就明确失败，不静默降级"。
- 配置快照 `characterSnapshot`（storyboards.py:858-872）。
- 前端风格选择 UI：`CharactersView.vue:56-67,404` 的 `GEN_STYLES` + localStorage + 请求体 `style`。

---

## 2. 目标 / 非目标

**目标**
1. 一个分镜项目可**选定并保存一个统一出图风格**，作用到该项目所有场景、所有镜头。
2. 支持"项目默认 + 场景覆盖 + 镜头微调"三级（本期至少做到项目级）。
3. 风格以"提示词后缀 + 固定风格参考图 + 可选 seed"组合方式生效，保证跨镜头一致。
4. 每种风格在入队时**快照进 job payload**，历史镜头可追溯"当时用的什么风格"。
5. 前端提供风格选择器，风格选项来自统一风格注册表（与需求 02 共用）。

**非目标**
- 不改 AI 拆解逻辑（`BREAKDOWN_SYSTEM` 明确"不要生成绘画提示词/参考图"）——风格是用户配置项，不是 AI 拆解产物。
- 不做自动识别已有图片的风格并反向统一。

---

## 3. 数据模型设计

### 3.1 风格配置落点：`StoryboardProject`（首选，唯一公共祖先）

在 `storyboard_projects`（models.py:850-860）增加一个 JSON 配置列 `style_config`（或拆成独立列）：

```jsonc
{
  "style_id": "manga",              // 风格预设 id，引用统一风格注册表
  "prompt_suffix": "日系漫画风格，干净线条，赛璐璐上色",  // 由 preset 展开，冗余存储便于追溯
  "reference_asset_id": "ast_xxx",  // 可选：固定风格参考图
  "seed": 123456,                   // 可选：固定种子（需 provider 支持）
  "negative_prompt": "低清晰度, 多余肢体"  // 可选：复用需求 02 的负向能力
}
```

- 备选：`StoryboardScene` 同字段做"场级覆盖"；本期先项目级，接口/结构预留即可。
- Shot 级不强制加字段；如需单镜头覆盖，可在 `asset_refs` 或新增可选列承载（避免破坏现有 Create/Patch）。
- 同步改 `StoryboardProjectCreate/Patch/Out`（schemas.py:1125-1134, 1228-1238）。

### 3.2 统一风格注册表（与需求 02 共用，总览 D3）

抽取 `style_presets`，每条含：`id`、`name`、`prompt_suffix`（正向风格后缀）、`negative_prompt`（建议负向）、`reference_image`（可选内置参考图）、`applicable_to`（cover/illustration/comic_panel）。首期内置项对齐现有枚举：

| 风格 id | 来源 | 后缀示例 |
|---|---|---|
| `manga` | CHARACTER_STYLES | 日系漫画风格，干净线条，赛璐璐上色 |
| `realistic` | CHARACTER_STYLES | 写实风格，真实光影 |
| `guoman` | CHARACTER_STYLES | 国漫风格 |
| `watercolor` | CHARACTER_STYLES | 水彩风格 |
| （贴图 6 档按需纳入） | sticker_styles | 各档 prompt_suffix |

> ✅ 已定（D3）：**共用统一注册表**，管理员维护。本表首期内置项即注册表的初始数据，提示词库与分镜都从此读取，不再各维护一份。

---

## 4. 出图链路改造（核心）

### 4.1 `_queue_generation`（storyboards.py:794-890）

当前 prompt = `subject + labels + consistency`。改为：

```
prompt = style_config.prompt_suffix + "\n" + subject + "\n" + labels + "\n" + consistency
```

- **风格后缀置于最前**，作为全局画风锚点；`consistency`（角色一致性句）保留。
- **风格参考图固定占一个槽位且优先保留**：改 `_storyboard_references`（391-425）的丢弃顺序——"先丢地点，风格最后丢"；项目级 `reference_asset_id` 作为常驻槽，草图/正式图阶段都带。
- **负向提示词**：随 payload 传 `negativePrompt`（依赖需求 02 的贯通），供应商不支持时按 D2 明示。
- **seed**：若 provider 支持，写入 payload 并在 `image_provider.generate_images` 增加 seed 参数透传；不支持则 UI 禁用该项（不静默忽略）。
- **风格配置快照**：入队时把生效的 `style_config` 完整快照进 payload（仿 `characterSnapshot`，建议字段名 `styleSnapshot`），供 `_generation_prompt` 与导出复用。

### 4.2 改动影响面

| 位置 | 改动 |
|---|---|
| `modules/storyboards.py` | `_queue_generation` 拼装 + 快照；`_storyboard_references` 槽位优先级；PATCH 项目接口接收 style_config；`_project_out` 下发 |
| `services/storyboard_breakdown.py` | **不动**（风格非 AI 产物） |
| `services/job_runner.py` + `image_provider.py` | seed / negativePrompt 透传（与需求 02 同一处改动，合并做） |
| `schemas.py` | project 三 schema + generate in 增字段（并把被忽略的 `reference_asset_ids` 明确：要么用起来要么删） |
| 前端 | `StoryboardWorkspace.vue` 顶部加风格选择器；`ShotInspector.vue:70-83` generate() 带上风格配置；`useStoryboard.js` 加 saveStyleConfig |

---

## 5. 一致性保证策略（如何做到"统一"）

| 手段 | 作用 | 本期是否做 |
|---|---|---|
| 组级统一 `prompt_suffix` | 所有镜头 prompt 前缀相同 → 画风锚定 | ✅ 核心 |
| 固定风格参考图常驻槽 | 视觉参考一致，且不再被挤掉 | ✅ 核心 |
| 固定 seed | 同模型同 seed 画风最稳 | ⚠️ 依赖 provider 支持，不支持则禁用 |
| 角色库参考图 + 一致性句 | 角色跨镜头一致（现有） | ✅ 保留 |
| 正式图用已通过草图锁构图 | 单镜头构图一致（现有） | ✅ 保留 |

> 说明：彻底的一致性（尤其人物长相）当前主要靠参考图机制；画风统一靠后缀 + 参考图 + seed 三管齐下。要在 UI 如实说明各手段的生效条件。

---

## 6. 分阶段实施

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 数据模型 | project 加 style_config；schema 三处；对接需求 02 的统一风格注册表 | 依赖需求 02 M1 先建好注册表 |
| M2 入队拼装 | `_queue_generation` 加后缀 + 快照；参考图槽位优先级调整 | 无 |
| M3 生成参数 | seed / negativePrompt 透传至 provider；风格参考图常驻 | 需求 02 的 M2（负向贯通）先完成 |
| M4 前端 | 风格选择器 + 保存 + generate 携带；风格选项来自注册表 | M1 |
| M5 导出/追溯 | prompt-pack 导出含风格信息；`_shot_out` 下发风格快照 | M2 |

---

## 7. 验收标准

1. 建项目选"日系漫画"风格，拆解出多个镜头并全部出草图：各镜头提示词前缀都含同一风格后缀，且都带同一张风格参考图。
2. 风格参考图不再被地点/其它资产挤掉；超额时丢的是地点。
3. 改项目风格后，历史镜头仍能追溯旧风格（靠 job payload 快照），新镜头用新风格。
4. seed / 负向词：供应商支持时生效并可复现，不支持时 UI 明示、不静默。
5. 现有测试断言（风格被挤掉、手写 prompt 开头、导出=入队 prompt）按新语义更新并通过。

---

## 8. 决策结论（✅ 已确认）

- D3 风格来源：与需求 02 **共用统一 `style_presets` Registry**，由管理员维护（用户拍板，总览 D3）。分镜风格选择器与提示词库取同一份数据源。
- D2 负向提示词：按能力探测处理，不支持时 UI 明示（总览 D2）。
- Q1 配置层级：**本期做到项目级**即可；"项目默认 + 场景覆盖"两级留作二期，data model 预留扩展空间不实现。
- Q3 seed：**供应商支持才启用**；实现前先探测 `image_provider` 是否透传 seed，不支持则 UI 禁用该项，靠风格后缀 + 固定参考图统一。
- Q4 `reference_asset_ids`：本期保持**忽略**，不启用也不删除（避免扩大范围）；style_config 的 `reference_asset_id` 承担参考图职责。
- Q5 旧镜头重生成：本期**不做批量重生成**；仅在改风格的入口提示"已有镜头需重新生成才会应用新风格"，历史图靠 styleSnapshot 保持可追溯。
