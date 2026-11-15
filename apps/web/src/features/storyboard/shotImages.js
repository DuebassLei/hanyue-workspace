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

/**
 * 下载一张图：fetch 成 blob 再触发保存。
 *
 * 不能直接用 `<a href download>`——跨域 URL 上 download 属性会被浏览器忽略，
 * 而素材原图的 Content-Disposition 是 attachment，点一下会变成 Chrome「打开/另存为/保存」
 * 预览弹窗（ai-1-xxxxx 那串就是 asset.original_name）。fetch→blob 后文件名完全由前端决定。
 * url 传不带查询参数的原链即可（签名链接带 sig 也能直接 fetch）。
 */
export async function downloadImage(url, filename) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`图片下载失败（HTTP ${response.status}）`)
  const blobUrl = URL.createObjectURL(await response.blob())
  try {
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // 立刻 revoke 会让部分浏览器来不及取数据，延后一拍
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  }
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
