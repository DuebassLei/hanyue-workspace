# 手绘风格提示词库设计（C · 完整 Skill 运行时）

- 日期：2026-11-12
- 状态：待评审
- 关联能力：`handdraw-style-prompter`（本地已安装 Skill）

## 1. 背景与目标

现有提示词库支持用户自建模板，并在图片生成页快速套用。本设计把本地手绘风格 Skill 的核心生图能力引入应用：

- 331 种手绘风格
- 36 种主题色
- 模型能力驱动的激活策略
- 参考图自动挂载

目标是让用户在产品内完成「选风格 → 输入主题 → 生成图片」，不依赖本机 Skill 环境。

## 2. 范围

### 包含

| 能力 | 说明 |
|---|---|
| 风格库 | 331 条，含新编号、旧编号、分组、参考作者、生成名、核心特征 |
| 主题色 | 36 条，含名称、分类、中英文描述、色卡 |
| 激活策略 | 名称直出 / 特征激活 / 必须参考图 |
| 参考图 | 随仓库打包，按策略自动附加 |
| 提示词合成 | 中英双语，纯图模式 |
| 提示词库入口 | 我的提示词 / 手绘风格 |
| 生图页套用 | 选风格、选色、合成预览、提交生成 |

### 不包含

- 版面布局库（SC / IG / SB / IP / EC）
- 图文模式与固定后缀
- IP、海报、风格融合、视频、封面等扩展生成器
- 运行时读取本机 Skill 目录
- 用户编辑内置风格
- 自动推荐风格与主题色
- 内置风格收藏与自定义副本

## 3. 关键决策

| 决策 | 结论 |
|---|---|
| 嵌入程度 | C：完整引入风格、颜色、激活策略、参考图 |
| 数据来源 | 导入快照 + 内置资源，运行时不依赖 Skill 路径 |
| 参考图打包 | 随仓库打包，约 18.5 MB，367 张 webp |
| 参考图策略 | 按模型能力自动判断并自动附加，用户可取消 |
| 入口结构 | 提示词库内切换，独立路由 `/prompts` 与 `/handdraw` |
| 主题色 | 用户显式选择，不启用 Skill 的自动推荐 |
| 模式 | 仅纯图模式 |
| 编号展示 | 同时展示新编号（FA-001）与旧编号（#001） |

## 4. 资源与导入

### 4.1 内置资源目录

```text
apps/api/app/resources/handdraw/
├── styles.json                 # 331 条风格
├── colors.json                 # 36 种主题色
├── model_capabilities.json     # 模型激活策略
├── style_alias_map.json        # 新旧编号映射
├── version.json
├── refs/FA/FA-001.webp ...     # 优先 _grid.webp，缺失回退单图
└── swatches/C-01.webp ...      # 36 张色卡
```

资源来源为本地 Skill：

```text
C:\Users\11301\.trae-cn\skills\handraw-style\skills\handdraw-style-prompter\references\
C:\Users\11301\.trae-cn\skills\handraw-style\images\
```

### 4.2 导入脚本

`apps/api/scripts/import_handdraw_library.py`

- `--source <path>`：从外部 Skill 包复制资源到内置目录，供维护使用
- 默认模式：只读取内置目录，写入数据库
- `--force`：忽略哈希，强制重建
- 行为：
  1. 计算资源哈希
  2. 解析 `styles.json`、`colors.json`、`model_capabilities.json`、`style_alias_map.json`
  3. 校验 331 / 36 数量
  4. 逐个检查参考图与色卡，缺失写入 `missing_references`，不中断
  5. 幂等 upsert
  6. 写入 `handdraw_library_meta`

### 4.3 启动导入

- 应用启动时若 `handdraw_styles` 为空，自动执行一次内置目录导入
- 哈希变化时按需重新导入；可通过 `HANDDRAW_AUTO_IMPORT=0` 关闭
- 导入只写内置表，不触碰 `prompt_templates`

## 5. 数据模型

新增三张表，跟随现有 `create_all` 建表机制（见 [db.py](file:///D:/aicode/hanyue-workspace/apps/api/app/core/db.py)）。

### handdraw_styles

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str PK | |
| style_number | str unique | 新编号，如 FA-001 |
| legacy_number | str | 旧编号，如 001 |
| group_key | str | FA–FH |
| group_name | str | 分组中文名 |
| reference_author | str | 参考作者 / 风格名称 |
| generation_name | str | 生成用风格名 |
| traits_raw | text | 核心特征原文 |
| reference_file | str nullable | 相对资源路径 |
| reference_kind | str | grid / single |
| reference_available | bool | 文件是否存在 |
| sort_order | int | 展示排序 |
| enabled | bool | 固定 True |

### handdraw_colors

| 字段 | 说明 |
|---|---|
| color_id | C-01–C-36 |
| category / category_zh / category_en | 分类 |
| name_zh / name_en | 名称 |
| quote_zh / quote_en | 文案 |
| prompt_zh / prompt_en | 单色提示句 |
| swatch_file | 色卡路径 |
| sort_order | 排序 |

### handdraw_library_meta

| 字段 | 说明 |
|---|---|
| library_key | 固定 `handdraw-style` |
| version | Skill 版本 |
| source_hash | 资源哈希 |
| imported_at | 导入时间 |
| style_count / color_count / reference_count | 数量 |
| missing_references | JSON 数组 |

## 6. 提示词合成规则

新增 `app/modules/handdraw.py` 与 `app/services/handdraw_prompt.py`，规则对齐 Skill 的 `resolve_reference.py` 与 `prompt_style.py`。

### 6.1 编号归一

- 纯数字：`018` → alias map → `FA-018`
- 形如 `fa_1`：规范为 `FA-001`
- 同时接受新旧编号查询

### 6.2 激活判断

1. 确定 `modelCode`：请求带 `modelId` 时通过 `image_provider.resolve_image_provider` 解析；未带时使用当前默认供应商的 `modelCode`；均不可用时按默认策略处理
2. 从 `model_capabilities.json` 读取 `models.{modelCode}`
3. 未匹配到模型时使用 `default`：`name_activation=unknown`、`use_reference_image=true`
4. 支持模型代号归一：小写、去空格、去供应商前缀（`openai/gpt-image-2` → `gpt-image-2`）

判定顺序：

```text
name_activation == strong
  → activation_source = name+style
  → 不输出特征，不附参考图

traits_activation == strong 且存在正向特征
  → activation_source = name+style+traits
  → 输出特征，不附参考图

其他
  → use_reference_image = true
  → 输出正向特征（若有）
  → activation_source = name+style+traits+reference-image
                     或 name+style+reference-image
```

### 6.3 正向特征过滤

- 按 `；;。\n` 分句
- 丢弃含「避免 / 不要 / 不准 / 禁止」的分句
- 丢弃匹配 `无(写实纹理|精细材质|真实纹理)` 的分句
- 特别保留原文：`FE-048`、`FE-049`、`FE-051`、`FH-050`、`FD-042`

### 6.4 提示词拼装

中文：

```text
风格名称：{generation_name}。{主题色句}主题：{theme}。参考作者/风格名称：{reference}。{核心风格特征}{参考图说明}{附加要求}
```

英文：

```text
Style name: {generation_name}.{theme color sentence} Theme: {theme}. Reference author/style name: {reference}.{core style traits}{reference image note}{extra requirements}
```

参考图说明不使用本机路径：

```text
参考图：随请求自动附加本风格参考图。所附图片仅用于参考画风。只提取参考图的风格特征……
```

### 6.5 语言选择

- 主题含中文字符 → 中文
- 否则 → 英文
- 接口同时返回中英两版，前端可切换

### 6.6 主题色

- 单色：直接使用 `colors.json` 的 `prompt_zh` / `prompt_en`
- 双色：`主色为 X（X），点缀色为 Y（Y）`
- 未选择时不注入任何颜色描述

## 7. 接口

统一前缀 `/api/v1/handdraw`，返回字段 camelCase。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/meta` | 版本、导入时间、数量、缺失参考图 |
| GET | `/styles` | 分页、搜索、分组筛选 |
| GET | `/styles/{number}` | 风格详情，支持新旧编号 |
| GET | `/styles/{number}/reference` | 参考图，带缓存头 |
| GET | `/colors` | 主题色列表 |
| GET | `/colors/{id}/swatch` | 色卡 |
| POST | `/compose` | 合成提示词与激活信息 |

`compose` 请求：

```json
{
  "styleNumber": "FA-001",
  "theme": "春天在窗边读书的女孩",
  "colorId": "C-01",
  "accentColorId": null,
  "modelId": "可选，目录模型 ID",
  "language": "auto"
}
```

`compose` 响应：

```json
{
  "style": {
    "number": "FA-001",
    "legacyNumber": "001",
    "generationName": "Playful Deadpan Doodle",
    "referenceAuthor": "Gemma Correll"
  },
  "color": { "id": "C-01", "nameZh": "克莱因蓝" },
  "promptZh": "...",
  "promptEn": "...",
  "activationSource": "name+style",
  "includeTraits": false,
  "needReference": false,
  "referenceAvailable": true,
  "language": "zh"
}
```

## 8. 生图接入

### 8.1 请求扩展

生图接口新增可选字段 `builtinReferenceStyleNumber`。

### 8.2 任务执行

改造 [job_runner.py](file:///D:/aicode/hanyue-workspace/apps/api/app/services/job_runner.py#L223-L251)：

1. 读取用户 `referenceAssetIds`
2. 若存在 `builtinReferenceStyleNumber`：
   - 校验风格存在且启用
   - 用实际模型代号重新判断 `needReference`
   - 需要时读取内置参考图，追加到参考列表
3. 参考图总数上限 4 张
4. 参考图缺失时返回 `JobFailure("reference_missing")`，不静默降级

参考图仍由 [image_provider.py](file:///D:/aicode/hanyue-workspace/apps/api/app/services/image_provider.py#L167-L236) 走 `/images/edits`，无需新增供应商协议。

### 8.3 提示词一致性

- 前端提交的是 `compose` 返回的提示词，用户可编辑
- 任务执行时不再覆盖提示词，仅按风格编号决定是否附加参考图
- 用户编辑后仍保留参考图，界面标明「已自动附加参考图」，可取消

## 9. 前端设计

### 9.1 入口

- 侧栏保持「提示词库」一项
- 移动端导航不新增项
- `/prompts` 与 `/handdraw` 均为「提示词库」的激活态

### 9.2 提示词库页

顶部增加分段控件：

```text
[ 我的提示词 ] [ 手绘风格 ]
```

「我的提示词」保持现有 CRUD；「手绘风格」跳转 `/handdraw`。

### 9.3 手绘风格页

- 分组筛选：全部 / FA–FH
- 搜索：新编号、旧编号、生成名、参考作者、特征
- 卡片：缩略图、编号、生成名、参考作者、激活标记
- 分页每页 24 条，缩略图懒加载
- 详情抽屉：大图、完整特征、中英提示词预览、复制、使用到生图
- 「使用到生图」通过路由参数传递，如 `/image-gen?handdrawStyle=FA-001`，生图页读取后自动选中并请求合成
- 空态与导入失败提示明确指向 `import_handdraw_library.py`

### 9.4 生图页

- 「提示词库」旁新增「手绘风格」按钮
- 选中后出现风格条：`#FA-001 · Playful Deadpan Doodle × 克莱因蓝 [取消]`
- 提示词框作为主题输入
- 下方展示合成预览与「重新生成」
- 主题色支持 36 色与「不指定」，最多一个点缀色
- 显示参考图状态：已附加 / 该风格无需参考图 / 参考图缺失

### 9.5 组件拆分

- `HanddrawStyleGrid.vue`：列表、筛选、分页
- `HanddrawStyleCard.vue`：卡片
- `HanddrawStyleDrawer.vue`：详情与提示词预览
- `HanddrawStylePicker.vue`：生图页弹窗
- `HanddrawColorSelect.vue`：主题色选择

## 10. 错误与降级

| 场景 | 处理 |
|---|---|
| 未导入 | 页面提示执行导入，接口返回 `imported=false` |
| 参考图缺失 | `referenceAvailable=false`，禁止提交带参考图的生成 |
| 风格不存在 | 404，不泄露内部路径 |
| 模型未识别 | 按默认策略附参考图 |
| 供应商不支持 `/images/edits` | 沿用现有错误提示 |
| 导入哈希变化 | 启动时重建内置表，不影响用户模板 |
| 提示词为空 | 前端禁用提交并提示 |

## 11. 测试与验收

### 后端

- 导入：数量 331 / 36、重复导入不重复、缺失参考图列表、哈希稳定
- 编号：`018` → `FA-018`、`fa_1` → `FA-001`
- 激活：
  - 名称直出（FA-001 + gpt-image-2）
  - 特征激活（traits strong 风格）
  - 参考图兜底（FF-001 + gpt-image-2）
  - 未知模型走默认
- 特征过滤：负向分句剔除、特别保留风格不被过滤
- 主题色：单色、双色、未选择
- 语言：中文主题出中文，英文主题出英文
- 生图任务：内置参考图被附加、总数上限 4、缺图明确失败、用户素材归属校验不回归

### 前端

- `npm run build` 通过
- 浏览器验证：列表、搜索、详情、套用、生成
- 移动端宽度与明暗主题可读
- 现有提示词 CRUD 不回归

### 验收标准

- 331 条风格与 36 种颜色全部可查
- 缺失参考图为 0
- 合成结果与 Skill CLI 抽样一致
- 参考图按模型策略正确附加

## 12. 风险与约束

| 风险 | 说明 | 应对 |
|---|---|---|
| 仓库体积 | 参考图约 18.5 MB | 已确认打包；后续可改用发布产物按需下载 |
| 版权与署名 | Skill 为 MIT + 署名要求 | 保留版权声明，在仓库与界面展示「手绘风格库由 yang0 / handraw-style 提供」 |
| 模型代号不一致 | 第三方模型代号与 `model_capabilities.json` 不同 | 归一化匹配，未匹配按默认附参考图 |
| 供应商能力差异 | 部分供应商不支持参考图编辑 | 沿用现有错误提示，不隐藏失败 |
| 数据升级 | Skill 更新后风格变化 | 以哈希触发重建，内置表只读，不影响用户数据 |
| 图片体积与加载 | 367 张缩略图 | 分页 24 条、懒加载、浏览器缓存头 |

## 13. 实施顺序

1. 资源落盘与导入脚本
2. 数据表与导入服务
3. 合成服务与单元测试
4. `handdraw` 接口
5. 生图任务扩展
6. 手绘风格页面与提示词库分段入口
7. 生图页选择器与合成预览
8. 端到端验证与回归

## 14. 署名

本项目引入的手绘风格数据与参考图来自 `yang0/handraw-style`（MIT License with Attribution Requirement）。需在仓库文档和界面保留原始作者与仓库链接。
