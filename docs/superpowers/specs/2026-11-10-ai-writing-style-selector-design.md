# AI 助手写作风格选择设计

## 1. 目标

在创作空间的 AI 助手中增加写作风格选择，让用户在生成初稿、整篇润色和整篇改写时明确指定风格。选择结果通过现有 Skill Registry 传递到后端，由后端按当前用户可见的 Skill manifest 组织成文提示词。

本功能不改变设置页现有的写作风格管理和内置风格定制机制，也不改变自动配图的异步生成流程。

## 2. 范围与任务规则

### 2.1 使用写作风格的任务

以下任务显示写作风格选择，并在请求中发送 `skillKey`：

- `article`：写初稿
- `polish`：润色（整篇）
- `rewrite`：改写（整篇）

默认风格为 `article-drafting`。如果用户已定制该内置风格，`/skills` 返回的个人副本会替换内置版本，前端继续使用相同的 `skillKey`。

### 2.2 不使用写作风格的任务

以下任务不显示或不发送写作风格：

- `idea_expand`：扩展角度
- `outline`：生成大纲
- `summarize`：摘要

切换到这些任务时，当前风格选择不参与请求；切回文章类任务时保留用户上一次选择，只要该风格仍然可用。

## 3. 前端设计

### 3.1 Skill 加载

`StudioView.vue` 在初始化时调用 `GET /skills`，从返回结果中筛选写作风格：保留 `source === "custom"` 或 `manifest.tone` 非空的 Skill。

加载失败不阻断创作空间和 AI 助手使用；风格列表为空时隐藏选择器，并让后端使用默认成文风格。

### 3.2 选择器

AI 助手中在任务选择附近增加写作风格下拉框：

- 仅当当前任务为 `article`、`polish` 或 `rewrite` 时显示；
- 选项展示 `displayName`，必要时补充简短描述；
- 默认选择 `article-drafting`，若该 Skill 不在列表中则选择第一个可用写作风格；
- AI 运行期间禁用；
- 风格列表加载中显示加载状态，加载失败不显示阻塞性错误；
- 无可用风格时显示“使用默认风格”。

### 3.3 请求参数

调用 `streamGeneration` 时，在文章类任务请求中加入：

```js
skillKey: selectedWritingStyleKey.value || 'article-drafting'
```

非文章类任务不发送该字段，避免让无关任务被写作风格约束。

自动配图开关继续只对 `article` 生效。文章完成并保存后，仍从最终 AI 输出中解析 `coverPrompt` 和 `inlinePrompts`，再异步提交图片任务。

## 4. 后端设计

### 4.1 Skill 解析

AI 成文后端接收可选 `skillKey` 后，使用当前用户上下文读取 Skill Registry：

- 内置 Skill 对所有用户可见；
- 用户自定义 Skill 仅当前用户可见；
- 用户定制的内置 Skill 优先于内置版本；
- 不接受其他用户的自定义 Skill；
- `skillKey` 不存在、不可见或读取失败时回退到 `article-drafting`。

### 4.2 Manifest 应用

成文逻辑从最终 Skill manifest 中读取并应用：

- `promptTemplate`：作为提示词模板基础；
- `tone`：作为语气要求；
- `styleRules`：作为风格规则列表；
- `defaultTheme`：保留在文章结果或后续排版上下文中，不能影响成文失败判定。

后端负责把 manifest 约束与任务输入、目标字数、平台要求合并。前端不拼接完整系统提示词，只传递 `skillKey` 和用户请求。

对 `polish` 和 `rewrite`，风格约束作用于整篇输入内容；对 `article`，风格约束作用于新文章生成。`idea_expand`、`outline`、`summarize` 沿用原有任务提示词。

## 5. 错误处理与兼容性

- `/skills` 加载失败：AI 助手仍可使用，后端使用默认风格。
- 所选 Skill 被删除或不可见：后端回退到 `article-drafting`，并在响应或日志中保留可诊断信息；不阻断生成。
- Skill manifest 缺少可选字段：使用默认成文模板、空风格规则或默认主题。
- 文本生成失败：不创建自动配图任务。
- 图片任务失败：沿用现有单图隔离和单独重试机制，不影响文章版本。
- 用户切换稿件时不跨稿件复用生成中的上下文；异步图片任务继续使用生成时绑定的 `contentItemId`。

## 6. 验证标准

### 前端

1. 创作空间能加载 `/skills` 并展示可用写作风格。
2. 文章类任务显示选择器，其他任务隐藏选择器。
3. 默认选择 `article-drafting`；定制副本能以同一 `skillKey` 被使用。
4. 生成请求携带正确的 `skillKey`，非文章任务不携带。
5. 自动配图开启时，风格请求与异步配图流程同时正常工作。

### 后端

1. 可见内置 Skill 和当前用户自定义 Skill 能被正确解析。
2. 用户定制副本优先于内置 Skill。
3. 其他用户的 Skill 不可被读取。
4. Skill 缺失或 manifest 异常时回退默认风格。
5. `promptTemplate`、`tone`、`styleRules` 会进入对应成文任务的提示词上下文。
6. 现有后端测试与前端生产构建继续通过。
