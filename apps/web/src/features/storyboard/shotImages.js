/**
 * 分镜镜头的图片与提示词共享逻辑。
 *
 * 后端下发的 sketchUrl / finalUrls 已是带签名的同源直链（/api/v1/assets/...），
 * <img src> 与新窗口打开都可以直接用，不需要再拼前缀。
 */

/** 一个镜头可查看的全部图片：草图在前，正式候选按返回顺序排列。 */
export function shotImages(shot) {
  if (!shot) return []
  const images = []
  if (shot.sketchUrl) images.push({ url: shot.sketchUrl, label: '草图', stage: 'sketch' })
  ;(shot.finalUrls || []).forEach((url, index) => {
    if (url) images.push({ url, label: `正式候选 ${index + 1}`, stage: 'final' })
  })
  return images
}

/** 该阶段实际入队生图的完整提示词（含参考图标注、角色一致性文案）。 */
export function shotStagePrompt(shot, stage) {
  if (!shot) return ''
  return (stage === 'final' ? shot.finalPrompt : shot.sketchPrompt) || ''
}

/** 镜头手写的画面提示词；未填时回退到画面描述。 */
export function shotFramePrompt(shot) {
  return shot?.prompt || shot?.frameDescription || ''
}

/** 打开某张图时展示的提示词：优先实际出图文本，没有生成记录时回退到画面描述。 */
export function shotPromptFor(shot, stage) {
  return shotStagePrompt(shot, stage) || shotFramePrompt(shot)
}

/** 复制到剪贴板；非安全上下文或权限被拒时用 execCommand 兜底。 */
export async function copyText(text) {
  const value = String(text || '')
  if (!value.trim()) return false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    /* 落到下面的兜底路径 */
  }
  try {
    const area = document.createElement('textarea')
    area.value = value
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  } catch {
    return false
  }
}
