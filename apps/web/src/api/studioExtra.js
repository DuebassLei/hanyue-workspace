/**
 * 成稿台「排版 · 媒体 · 发布」接口客户端。
 *
 * 与 `api/studio.js` 分开维护：那份是主控统一改的成稿台主流程接口，
 * 这份只放本轮新增的三个能力（见 docs/paper-studio-contract.md 第 3 节）。
 * 返回同样遵守「整篇快照」约定——除 render / review 这类只读派生数据外，
 * 所有写接口都回完整 article，调用方按快照整体替换，不做局部合并。
 */
import { api } from './client'

const base = (id) => `/studio/articles/${id}`

export const studioExtraApi = {
  // ---- 排版 ----
  /** 渲染公众号正文：中间栏预览唯一的数据来源，避免多处各渲染一次。options 缺省时后端用稿件已存的排版选项。 */
  render: (id, options) => api.post(`${base(id)}/render`, options ? { options } : {}),
  /** 排版面板的初始化：拿稿件当前选项 + 风格/主题/SVG 卡目录（目录决定面板里能选什么）。 */
  renderOptions: (id) => api.get(`${base(id)}/render/options`),
  /** 「保存为默认」：把当前选项写回稿件的 layout_options，下次打开/发布自动沿用。 */
  saveLayout: (id, options) => api.post(`${base(id)}/layout/preferences`, options || {}),

  // ---- 封面 ----
  /** 提交生图任务；后端只入队并回 running 快照，产物要靠 sync 拉，所以前端必须轮询。 */
  generateCover: (id, body) => api.post(`${base(id)}/cover/generate`, body || {}),
  /** 轮询查询封面任务结果：成功写 assetId/url，失败写 error（不会静默成功）。 */
  syncCover: (id) => api.post(`${base(id)}/cover/sync`, {}),
  /** 选定/清空封面素材：换图走同一接口，传 null 表示移除封面。 */
  pickCover: (id, assetId) => api.post(`${base(id)}/cover/pick`, { assetId: assetId || null }),

  // ---- 正文插图 ----
  /** 针对某个段落提交插图任务；同样只入队，结果靠 sync 拉。 */
  generateInline: (id, body) => api.post(`${base(id)}/inline/generate`, body || {}),
  /** 轮询所有 running 的插图：成功后后端会在段落之后插入 media 块，所以必须用返回的快照刷新整篇。 */
  syncInline: (id) => api.post(`${base(id)}/inline/sync`, {}),
  /** 移除一张插图：同时删掉它插入正文的那个块，避免留下空图段。 */
  removeInline: (id, diagramId) => api.del(`${base(id)}/inline/${diagramId}`),

  // ---- SVG 动画卡 ----
  /** 按段落 + 卡片类型生成 SVG 卡，后端把 fence 作为新块插在该段之后。 */
  svgCard: (id, blockId, kind) => api.post(`${base(id)}/blocks/${blockId}/svg-card`, { kind }),

  // ---- 发布与复盘 ----
  /** 发布：dryRun=true 只导出成稿件版本（存入文章管理），false 才真正入发布队列。 */
  publish: (id, body) => api.post(`${base(id)}/publish`, body || {}),
  /** 复盘：按 contentItemId 反查各平台任务状态，用于发布结果表格。 */
  review: (id) => api.get(`${base(id)}/review`),
  /** 排期：只记「计划什么时候发」，与 studio.js 的 schedule 同一接口，这里复用于发布面板。 */
  schedule: (id, at) => api.post(`${base(id)}/schedule`, { at: at || null }),
  /** 平台能力矩阵：发布面板的平台勾选项必须来自真实目录，不能在前端写死平台名。 */
  platformCapabilities: () => api.get('/platforms/capabilities'),
}

/**
 * 能力接口不可用时的兜底平台。
 *
 * 后端 `connectors/base.py` 目前只接入了 `wechat_official`（草稿箱推送），
 * 平台标识必须用这个真实 id，不能用路由前缀式的 `wechat-mp`。
 */
export const FALLBACK_PLATFORMS = [
  { platform: 'wechat_official', label: '微信公众号', available: true, draftUpload: true },
]
