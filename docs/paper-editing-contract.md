# 成稿台段落编辑契约

成稿台的改写体验要跟参考项目 `dsh-wechat-studio` 对齐。参考实现是 React 单文件
（`client/app.jsx` 的 `Editor` / `Annotation` / `UnsavedParagraphs` / `PaperNavigation`），
本项目拆成 Vue 组件，接口按下文固定，避免并行开发时互相猜。

## 1. 要恢复的四组前提

参考项目里，改一段文章的默认路径只有一条，本项目迁移后三处假设被换掉了：

| 参考项目 | 当前成稿台 | 结论 |
| --- | --- | --- |
| 点正文任意位置进入编辑 | 点右上角小字「改」 | 整段可点，段号 hover 出铅笔 |
| 显式保存 + 快捷键 + 保存并改下一段 | 只有「保存 / 取消」，无快捷键 | 全部补回 |
| 草稿落 localStorage，切稿不丢 | 只在内存 `editing`，切稿即丢 | 用 composable 持久化 |
| 任务运行期整段不可点 | 仍可编辑，保存时才 409 | 编辑入口按 `busy` 禁用 |

## 2. 文件归属（并行开发时不要越界）

| 文件 | 归属 |
| --- | --- |
| `apps/web/src/composables/useParagraphDrafts.js` | 编辑器线（新建） |
| `apps/web/src/components/paper/PaperBlockEditor.vue` | 编辑器线（新建） |
| `apps/web/src/components/paper/PaperDiffDialog.vue` | 批注线（新建） |
| `apps/web/src/components/paper/PaperAnnotation.vue` | 批注线（新建） |
| `apps/web/src/views/PaperView.vue` | 主控独占 |
| `apps/api/**` | 主控独占（本轮不改接口） |

不改 `src/api/studio.js`、不改后端路由。后端契约已经够用（见第 5 节）。

## 3. `useParagraphDrafts`

模块级单例（不是每组件一份），key 与参考项目区分开，前缀用 `hanyue-studio`。

```js
export function useParagraphDrafts() {
  return {
    // 响应式：任何一段草稿增删都 +1，供列表/角标订阅
    draftVersion,        // Ref<number>
    storageFailed,       // Ref<boolean> 写 localStorage 失败时为 true
    draftsFor,           // (articleId) => Map<blockId, { text: string, original: string }>
    setDraft,            // (articleId, blockId, draft | null) => void；null 表示清除
    clearArticle,        // (articleId) => void
    countUnsaved,        // (article) => number，只统计仍存在于文章里的段
    listUnsaved,         // (article) => Array<{ block, number, draft }>，number 从 1 开始
    lastParagraph,       // (articleId) => blockId | null
    setLastParagraph,    // (articleId, blockId) => void
  }
}
```

约定：

- localStorage：`hanyue-studio:paragraph-drafts:v1:${articleId}`（存 `[[blockId, draft], ...]`）、
  `hanyue-studio:last-paragraph:${articleId}`。
- 读写都包 try/catch。写失败只置 `storageFailed`，不抛异常，编辑照常可用。
- `setDraft` 传入的 `draft.text.trim() === draft.original` 时按「无改动」处理，直接清掉键，
  避免只多一个空格的草稿长期占着「未保存」。
- 草稿是**不落库**的本地暂存，保存成功后由调用方 `setDraft(..., null)` 清除。

## 4. 组件接口

### 4.1 `PaperBlockEditor.vue`

```js
props: {
  articleId: { type: String, required: true },
  block:     { type: Object, required: true },  // 快照段：{ id, text, kind, check, prevText }
  number:    { type: Number, required: true },  // 段号，从 1 开始
  nextBlock: { type: Object, default: null },   // 下一段，用于「保存并改下一段」
  busy:      { type: Boolean, default: false }, // 有 job 或别的动作在跑
},
emits: ['saved' /* 整篇快照 */, 'done' /* { advance: boolean } */, 'error' /* message */],
```

内部必须实现的行为：

1. **初值**：`draftsFor(articleId).get(block.id)` 有草稿取草稿，否则 `{ text: block.text, original: block.text }`。
2. **三个派生状态**：
   - `changed = draft.text.trim() !== draft.original`
   - `conflict = block.text !== draft.original`（后端已经有新版本）
   - `empty = !draft.text.trim()`
3. **编辑即暂存**：每次输入调用 `setDraft`，无改动时传 `null`。
4. **保存**：调 `studioApi.updateBlock(articleId, block.id, { text, expectedText: draft.original })`。
   成功后 `setDraft(null)`、`emit('saved', snapshot)`、`emit('done', { advance })`。
   失败由组件自己弹 toast（它知道是「有任务在跑」还是「内容冲突」），同时
   `emit('error', err.message)`；页面不再重复弹同一条。`err.status === 409` 时进入冲突态
   （保留草稿，显示「复制修改」）。
5. **删除**：工具条右侧常驻「删除本段」（快捷键 `Ctrl/Cmd + Shift + K`），不在页脚——
   段落一长页脚就被推出屏幕，删一段要先滚到底。空段时主按钮文案变「删除段落」，
   调用 `updateBlock` 传 `text: ''`（后端会 pop 掉这一段，可通过顶部「撤销一步」恢复）。
   空段不能点「保存并改下一段」。
   **稿纸层也有一个**：段头右端 hover 浮出 `×`（`.block-del`），不进编辑器就能删；
   走同一条 `updateBlock` 链路，选素材模式、该段正在编辑、任务运行时不显示，
   该段有未保存草稿时先弹确认。图标不放段号紧邻处——左侧 44px 留白已被段号占满，
   再塞 18px 按钮会压住「正文」标签。
6. **快捷键**：`Ctrl/Cmd + Enter` 保存；`Ctrl/Cmd + Shift + Enter` 保存并改下一段；
   `Ctrl/Cmd + Shift + K` 删除本段（与工具条「删除本段」等价，删除后提示可用「撤销一步」恢复）；
   `Esc` 先退出预览/关菜单，再取消编辑。输入法合成期（`isComposing` / `keyCode === 229`）忽略。
   删除只绑定 `K`：`Backspace` 在输入框里本就是删字，绑成删段太容易失手。
7. **预览**：工具条右上「预览 / 继续编辑」，用 `markdownToHtml`（`../utils/markdownSerializer`）
   `v-html` 渲染当前草稿。预览态隐藏格式化按钮与「保存并改下一段」。
8. **自动高度**：textarea 随内容长高（`scrollHeight` + `ResizeObserver`），不出现内部滚动条。
9. **禁用**：`busy` 或保存中，保存/删除/改下一段都 disabled；保存中 textarea `readOnly`。
10. **状态行**（底部左）：正在保存 / 本地暂存失败 / 正文已有新版本 / 清空后可手动删除 /
    已在本机暂存，尚未写入正文 / `⌘ / Ctrl + Enter 保存`。
11. **格式化与插入**：强调三件套 `**`（荧光笔加粗）/ `==`（色块）/ `++`（主题色下划线），
    按选区包裹、再点一次去包裹，多行选区逐行包裹。
    「插入」菜单分三组，每一项都必须是排版引擎真能渲染的语法（菜单边界＝引擎能力边界，
    插 `> [!GOLDEN]` 这种引擎不认的标记会在正文里印出英文标签）：

    | 分组 | 项 | 产出 |
    | --- | --- | --- |
    | 引用与提示 | 引用卡 | `> ` |
    | | 金句卡 | `> [!QUOTE] ` |
    | | 小技巧 / 注意 / 说明 / 省流版 | `> [!TIP/WARN/NOTE/TLDR] ` |
    | | 小标题 | `## ` |
    | 代码与提示词 | 终端 · bash / text | ```` ```bash ```` / ```` ```text ```` |
    | | 长提示词 · 黑标签 / 虚线框 | ```` ```prompt 文件名 ```` / ```` ```promptbox 标题 ```` |
    | 图片与分隔 | 图片与图注 / 长图卡 | `![图注](url)` / `![图注](url "long")` |
    | | 分隔线 | `---` |

    - 图片、长图、两种长提示词要先开 `PaperCardInsert` 弹窗收内容，其余点一下出占位并**选中占位文字**。
    - 块级片段两侧补空行（否则 markdown-it 会把卡片并进上一段），行内强调不补。
    - 规则集中在 `src/utils/editorFormat.js`，纯函数，由 `tests/editorFormat.test.mjs` 守着。
    - 粘贴图片（截屏 / 复制）直接开图片弹窗，正文里不会落一段 base64。
    - 菜单向下展开会超出视口时改为向上展开（长文末尾的段落几乎贴着视口底）。
12. **字数**：单段上限 20000 字符，超限时 `maxlength` 挡住并提示。

### 4.2 `PaperAnnotation.vue`

```js
props: {
  block: { type: Object, required: true },
  job:   { type: Object, default: null },  // { kind, target, targets: [] }
  number:{ type: Number, required: true },
},
emits: ['fix', 'keep', 'revert'],  // 由 PaperView 调接口
```

渲染规则（照参考项目 `Annotation`）：

- 本段正在被后台改写（`job.kind==='fix' && job.target===block.id` 或
  `job.kind==='fixall' && job.targets.includes(block.id)`）→ 「正在改写这一段…」+ shimmer。
- `status === 'pass'` → 「已通过」+ 看改动。
- `status === 'kept'` → 「保留原样」+ 看改动。
- `status === 'fixed'` → 「已改写，等下一轮检测」+ 看改动 / 退回上一版。
- `status === 'flag'`：
  - 标题：`朱雀 API：AI 特征` / `朱雀 PDF：疑似 AI` + 分数（API 显示分类置信度百分比，PDF 显示报告得分）。
  - 建议（有且只有一条，按优先级）：
    1. `check.worse` → 「改完分数反而升了，建议退回上一版的写法。」
    2. `check.fixes >= 2` → 「这一段已经改过两轮。建议你用自己平时说话的方式重说一遍，点正文就能直接改。」
    3. `check.source !== 'api' && check.score < 0.6` → 「0.5–0.6 是噪声带，每次重测都会浮动，可以不改。」
  - 按钮：`worse && prevText` 时主按钮是「退回上一版」，否则「改这段」；次按钮「保留」。
- `block.prevText` 存在时始终给「看改动」，打开 `PaperDiffDialog`。
- 没有 `check` 也没有 `prevText` 时不渲染任何东西（不占位）。

### 4.3 `PaperDiffDialog.vue`

```js
props: { show: Boolean, before: String, after: String, number: Number }
emits: ['update:show']
```

- 用 `n-modal`（`preset="card"`），标题「第 N 段的改动」，宽度 720px。
- 逐字/逐词 diff：中文按字符、英文数字按词，最长公共子序列；删除段 `<del>` 红底、新增段 `<ins>` 绿底。
  自己写 40 行以内，不引依赖。
- 底部：`退回上一版`（emit 由父级接）不做进这个弹窗——弹窗只读。
- Esc / 点遮罩关闭。

### 4.4 `PaperCardInsert.vue`

```js
props: { show: Boolean, card: Object | null }  // { id, selection, file?, start?, end? }
emits: ['update:show', 'insert', 'closed']     // insert(markdown) 由父级落位
```

- `card.id` 只会是 `image` / `long` / `prompt` / `promptbox`。
- 图片 / 长图：本地图片选择（先出缩略图，提交时才上传）→ 图片地址 → 图注。
  上传走 `POST /assets`（`api.upload`），拿到签名直链后用 `apiUrl()` 转绝对地址写进正文。
  该直链带 **7 天**有效期，弹窗里要写明，父级插入后也 toast 一次，避免发布当天裂图。
- 长提示词：标题（选填）→ 正文（必填）。
- 校验顺序：没地址也没选图 → 「请填写图片地址，或选择一张本地图片。」；
  文件 > 10 MB → 「本地图片不能超过 10 MB。」；其余交给 `cardMarkdown()` 抛的中文提示，
  错误只显示在表单底部一行红字，不弹 toast。
- 提交中禁用所有按钮并改文案「正在上传…」；关闭动画结束后 `emit('closed')`，
  父级靠它把焦点还给正文（naive-ui 会把焦点还原到打开前的「插入」按钮）。

## 5. 后端契约（本轮不改，供前端对齐）

`PATCH /studio/articles/{id}/blocks/{blockId}`：

- `_busy()` 在最前面 → 有 job 时 **409**「这篇文章还有任务在跑，等它结束再试。」
- `expectedText` 与库里不一致 → **409**「这一段已有新版本，未保存的编辑已保留。」
- `text` 规范化后与库里相同 → 直接返回快照，不产生版本。
- `text` 为空 → `blocks.pop(index)`，即删除该段。
- 文本里有多段（含空行）→ 按 Markdown 拆成多段插回原位。

所以前端只需要：job 期间禁编辑；捕获 409 后区分「有任务在跑」与「内容冲突」——
按 `err.message.includes('任务在跑')` 判断，其余 409 一律进冲突态。

## 6. 视觉与工程约定

- 颜色只用现有 CSS 变量并带 fallback：`--paper-accent`、`--paper-accent-tint`、`--paper-accent-soft`、
  `--paper-danger`、`--paper-danger-soft`、`--paper-danger-line`、`--paper-line`、`--paper-line-strong`、
  `--paper-ink`、`--paper-ink-soft`、`--paper-success`。不写死深色。
- 正文排版：`font-size: 15px; line-height: 1.85; white-space: pre-wrap`。
- naive-ui 按需 `import { NButton, NInput, NModal } from 'naive-ui'`，不要全局注册。
- 单文件组件 `<script setup>` + `<style scoped>`。
- 中文文案跟参考项目一致（「改这段」「保留」「退回上一版」「保存并改下一段」「看改动」），
  不再出现「就这样」「改这一段」两套叫法。

## 7. 验收

1. `cd apps/web && npm run build` 通过。
2. 点正文任意位置进入编辑；`Esc` 取消；`Ctrl+Enter` 保存；`Ctrl+Shift+Enter` 跳到下一段编辑。
3. 改到一半切到另一篇再切回来，草稿还在，段上有「有未保存的修改」。
4. 后台 fixall 跑着时，点正文不进入编辑。
5. 另开一个窗口改同一段 → 保存后提示冲突，草稿保留，有「复制修改」。
6. 清空一段 → 按钮变「删除段落」，删完可用「撤销一步」恢复；点工具条「删除本段」或按
   `Ctrl/Cmd + Shift + K` 也能直接删，删完有提示。
7. 段内 Markdown（`**粗体**`、`## 小标题`）在预览里渲染成样式，不再是 `<pre>` 直出。
