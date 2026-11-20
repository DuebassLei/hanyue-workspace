/**
 * 解析 AI 成文的结构化输出。
 *
 * 文章类任务（article / polish / rewrite）按后端输出契约返回一个 JSON 对象：
 * {title, markdown, summary, coverPrompt, inlinePrompts}。
 * 模型偶尔会给 JSON 包一层 ```json 代码块或夹带解释文字，这里统一剥掉；
 * 解析不出 markdown 时返回 null，调用方按普通文本处理。
 */
/**
 * 某些模型把整篇 Markdown 的换行多转义了一次。
 * 仅修复无真实换行、包含转义段落分隔的整篇文本；不对正常 Markdown
 * 或代码片段做通用反转义，避免破坏代码中的 \\n、路径和引号。
 */
export function normalizeArticleMarkdown(raw) {
  const text = String(raw ?? '')
  if (/[\r\n]/.test(text) || text.includes('```') || !/(?:\\r\\n|\\n){2}/.test(text)) return text
  return text.replace(/\\r\\n|\\n|\\r/g, '\n')
}

export function parseArticleOutput(raw) {
  const text = String(raw || '').trim()
  if (!text) return null
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  let data
  try {
    data = JSON.parse(text.slice(start, end + 1))
  } catch {
    return null
  }
  if (!data || typeof data !== 'object' || typeof data.markdown !== 'string' || !data.markdown.trim()) {
    return null
  }
  return {
    title: typeof data.title === 'string' ? data.title.trim() : '',
    markdown: normalizeArticleMarkdown(data.markdown.trim()),
    summary: typeof data.summary === 'string' ? data.summary.trim() : '',
    coverPrompt: typeof data.coverPrompt === 'string' ? data.coverPrompt.trim() : '',
    inlinePrompts: Array.isArray(data.inlinePrompts)
      ? data.inlinePrompts.filter((prompt) => typeof prompt === 'string' && prompt.trim()).map((prompt) => prompt.trim())
      : [],
  }
}

/** 取模型输出里的正文：结构化文章只取 markdown，其他任务原样返回。 */
export function articleBodyFromOutput(raw) {
  return parseArticleOutput(raw)?.markdown ?? normalizeArticleMarkdown(raw)
}
