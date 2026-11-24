# 微信公众号独立排版工作台

> 2026-11-20：仅保留参考项目适配的新工作台与新渲染引擎；旧 token 主题、小组件库及历史发布渲染回退已退役。不清空文章、素材或数据库业务记录。

## 1. 使用流程

1. 在「文章管理」点击文章的「排版预览」，携带真实 `articleId`、`versionId` 进入 `/wechat-layout`。
2. 左侧查看稿纸，右侧调整正文风格、配色、开篇横幅、文末关注、品牌文案和头像。
3. 保存排版；修改正文会创建新文章版本，仅改配置不新增正文版本。
4. 创作空间点击「发布」，选择真实支持的平台及账号，配置标题和素材并提交。
5. 发布读取对应文章版本已保存的配置，入队时冻结快照，不随后续设置改变。

直接打开 `/wechat-layout` 为样文体验，未关联文章时不能冒充真实文章保存。

## 2. 唯一渲染入口

```python
from app.services.wechat_layout_engine import render_studio_document

result = render_studio_document(body, options, image_urls)
```

正文实现在 `services/wechat_layout_studio.py`，SVG 卡和横幅复用 `wechat_layout_engine.py`。预览、发布预检与异步推送统一使用新引擎，不读取旧 `layoutTokens`，不回退到旧主题渲染器。

- 正文风格：`std`、`mag`、`min`、`geek`、`bold`、`neo`、`note`、`zen`、`bento`、`dark`。
- 配色：15 套，标识来自引擎 `THEMES`，不再使用旧主题 ID。
- 配置：`style`、`theme`、`banner`、布尔 `outro`、`brandAuthor`、`brandSlogan`、`avatarUrl`、`title`。
- 横幅：`none`、`poster`、`neo`、`card`。
- 配置保存于 `ContentVersion.layout_options`；发布保存新排版配置快照。

## 3. API

统一前缀 `/api/v1`：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/wechat-mp/layout-studio/catalog` | 风格、配色和卡片目录 |
| GET | `/wechat-mp/layout-studio/articles/{contentItemId}?versionId=...` | 读取文章版本配置 |
| PUT | `/wechat-mp/layout-studio/articles/{contentItemId}?versionId=...` | 保存 `{options}` |
| POST | `/wechat-mp/layout-studio/render` | 提交 `{body,options,imageUrls?,assetIds?}`，返回正文 HTML、告警和统计 |
| POST | `/wechat-mp/drafts/preview` | 发布校验，带文章及版本标识 |
| POST | `/wechat-mp/drafts` | 创建真实公众号草稿推送任务 |

旧 `/themes`、`/themes/schema`、`/themes/preferences`、`/components`、`/drafts/render` 及旧 token 请求协议不再提供。排版权限 `wechat_layout.use` 继续保护独立工作台；发布权限独立校验。

## 4. 正文格式

正文使用标准 Markdown：文章标题 `#`、章节 `##`、小节 `###`、强调、引用、列表、表格、分割线、图片和代码围栏。

- 提示和金句使用引擎支持的 callout，如 `> [!NOTE]`、`> [!QUOTE]`。
- 动画卡使用代码围栏语言 `svg-table`、`svg-bars`、`svg-steps`、`svg-timeline`、`svg-breakdown`、`svg-compare`、`svg-concept`、`svg-phone`，行文本以 `|` 分列。
- 横幅、署名和品牌关注设置走排版配置，不让 AI 输出已废弃的普通 `:::card`、`:::follow`、`:::sign` 等组件协议。
- 现行图片插槽 `{{image1}}`、`{{image2|图注}}` 从 1 起索引，发布上传后替换为真实图片 URL；不重复追加已消费的图片。

## 5. 安全及行为边界

界面框架和主题调整不得改变成稿台排版组件、文章预览或导出排版的实际效果；隔离要求及回归检查见 [成稿台界面框架与文章排版的隔离约束](paper-studio.md#界面框架与文章排版的隔离约束)。

- 原文 HTML 转义，URL 校验，拒绝危险地址；头像配置仅接受安全 HTTPS URL。
- 正文输出内联样式，不输出可执行脚本；预览 iframe 使用无脚本 sandbox。
- 复制到公众号使用 `text/html` 与 `text/plain` 富文本剪贴板；HTML 源码复制单独提供。
- 渲染失败清除过期预览及自查结果，保存失败明确报错；未保存切换文章或离开页面会提醒。
- 公众号发布保留素材转码、上传、权限与业务校验，超时结果未知时不自动重发。
- SMIL 动画在不同微信客户端的实际显示仍需真机确认；本地回归不代表已完成外部发布验证。
- 不为旧格式任务恢复旧排版，不执行数据库清空或旧文章自动改写。

## 6. 主要实现与测试

- 界面：`views/LayoutView.vue`、`features/layout/useLayoutStudio.js`、`studioPresets.js`。
- 发布：`features/publish/PublishDrawer.vue`、`WechatDraftPanel.vue`、`composables/useWechatPublish.js`。
- 服务端：`modules/wechat_mp.py`、`services/wechat_article.py`、`services/job_runner.py`。
- 引擎：`services/wechat_layout_engine.py`、`services/wechat_layout_studio.py`。
- 测试：`test_wechat_studio_api.py`、`test_wechat_layout_studio.py`、`test_wechat_layout_engine.py`、`useLayoutStudio.test.mjs`、`publishEntrypoints.test.mjs`。

回归验证配置权限、版本归属、输入安全、预览和 worker 输出一致、任务快照隔离、旧接口退役、前端版本跳转、保存重试和富文本复制。数据库模型变更后须同步 `infra/postgres/schema.sql` 并检查一致性。
