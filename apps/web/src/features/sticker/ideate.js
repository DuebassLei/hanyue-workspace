import { streamGeneration } from '../../api/client'

/**
 * AI 构思：复用同一条 SSE 通道（streamGeneration → /ai/text-generations），
 * 输入主题 + 风格名 + 张数，输出 [{caption, framePrompt, slotType}]。
 * 解析失败时 reject，由调用方提示用户手动填写。
 */

const SLOT_TYPES = new Set(['info', 'pace'])

function buildPrompt(theme, styleName, count) {
  return [
    `主题：${theme}`,
    styleName ? `画风：${styleName}` : '',
    `请围绕主题规划 ${count} 张风格统一的微信九宫格贴图分镜。`,
    '只输出一个 JSON 数组，不要任何解释、不要 markdown 代码块围栏。',
    '每个元素形如 {"caption":"不超过18字的配文","framePrompt":"具体的英文画面描述，包含场景、构图、色调","slotType":"info或pace"}。',
    'slotType 规则：承载核心信息的填 "info"，调节节奏氛围的填 "pace"。',
  ]
    .filter(Boolean)
    .join('\n')
}

function parseItems(text) {
  const raw = String(text || '')
  // 模型偶尔会包一层 ```json 围栏或在前后带闲话，取第一个 [ 到最后一个 ] 再解析
  const start = raw.indexOf('[')
  const end = raw.lastIndexOf(']')
  if (start < 0 || end <= start) throw new Error('模型没有返回结构化结果')
  const parsed = JSON.parse(raw.slice(start, end + 1))
  if (!Array.isArray(parsed) || !parsed.length) throw new Error('模型返回的分镜列表为空')
  return parsed.map((item, index) => ({
    caption: String(item?.caption ?? '').trim(),
    framePrompt: String(item?.framePrompt ?? item?.prompt ?? '').trim(),
    slotType: SLOT_TYPES.has(item?.slotType) ? item.slotType : 'info',
    // 兜底行也要有标识，方便 UI 提示哪一行需要补
    _index: index,
  }))
}

/**
 * @returns {Promise<Array<{caption:string, framePrompt:string, slotType:string}>>}
 */
export function ideateOutline({ theme, styleName = '', count = 6, timeoutMs = 45000 }) {
  let collected = ''
  let settled = false
  return new Promise((resolve, reject) => {
    const finish = (fn, arg) => {
      if (settled) return
      settled = true
      fn(arg)
    }
    const parseOrReject = () => {
      try {
        finish(resolve, parseItems(collected))
      } catch (err) {
        finish(reject, new Error(`AI 构思结果解析失败：${err.message}，请在下方手动编辑分镜`))
      }
    }
    const stop = streamGeneration(
      { taskType: 'image_prompt', prompt: buildPrompt(theme, styleName, count) },
      {
        onDelta(data) {
          collected += data.text || ''
        },
        onError(data) {
          finish(reject, new Error(data.message || data.code || 'AI 构思请求失败'))
        },
        onDone: parseOrReject,
        // done/error 之后总会跟 end；只有流提前结束没发 done 时才在这里兜底结算
        onEnd: parseOrReject,
      },
    )
    // 超时保护：流卡住时不无限等待
    setTimeout(() => {
      stop()
      finish(reject, new Error('AI 构思超时，请稍后重试或手动编辑分镜'))
    }, timeoutMs)
  })
}
