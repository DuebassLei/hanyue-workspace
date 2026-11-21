/**
 * 段落草稿的本地暂存（成稿台）。
 *
 * 用模块级单例而不是每个组件一份：
 * 同一份草稿会被「未保存段落」列表、段号角标和编辑器同时读写，组件各自持有副本
 * 就一定对不上；这里让模块顶层 Map 当唯一真相，再用 draftVersion 这个全局计数
 * 通知外面「草稿集合变了，该重算了」。
 *
 * 草稿刻意不落库：后端只有「整段覆盖」接口，没有草稿概念，半成品正文一旦进了快照，
 * 后台的质检 / 改写任务就会拿草稿去跑，等于替用户做了决定。
 */
import { ref } from 'vue'

const PREFIX = 'hanyue-studio'
// articleId -> Map<blockId, { text, original }>
const ARTICLES = new Map()
// articleId -> blockId，用来做「接着改第 N 段」
const LAST = new Map()

const draftVersion = ref(0)
// 只上报、不抛出：隐私模式 / 配额满的时候编辑仍然要能用，只是刷新会丢
const storageFailed = ref(false)

const draftsKey = (articleId) => `${PREFIX}:paragraph-drafts:v1:${articleId}`
const lastKey = (articleId) => `${PREFIX}:last-paragraph:${articleId}`

/** 某个 articleId 的草稿表；内存里没有才去读 localStorage，且只读一次 */
function readDrafts(articleId) {
  const cached = ARTICLES.get(articleId)
  if (cached) return cached
  const drafts = new Map()
  try {
    const stored = JSON.parse(localStorage.getItem(draftsKey(articleId)) || '[]')
    if (Array.isArray(stored)) {
      for (const entry of stored) {
        const [blockId, draft] = Array.isArray(entry) ? entry : []
        // 手改过 localStorage 或旧版本字段缺失时跳过：一条脏数据不该让整篇草稿读挂
        if (typeof blockId !== 'string') continue
        if (typeof draft?.text !== 'string' || typeof draft?.original !== 'string') continue
        drafts.set(blockId, { text: draft.text, original: draft.original })
      }
    }
  } catch {
    // 存储不可用时按「没有草稿」处理
  }
  ARTICLES.set(articleId, drafts)
  return drafts
}

function persist(articleId, drafts) {
  try {
    if (drafts.size) localStorage.setItem(draftsKey(articleId), JSON.stringify([...drafts]))
    else localStorage.removeItem(draftsKey(articleId))
    if (storageFailed.value) storageFailed.value = false
  } catch {
    storageFailed.value = true
  }
}

export function useParagraphDrafts() {
  function draftsFor(articleId) {
    if (!articleId) return new Map()
    return readDrafts(articleId)
  }

  /**
   * draft 传 null 表示清除该段草稿。
   * 与原文只差空格的情况按「没改」处理，否则「未保存」角标会永远挂着清不掉。
   */
  function setDraft(articleId, blockId, draft) {
    if (!articleId || !blockId) return
    const drafts = readDrafts(articleId)
    const had = drafts.has(blockId)
    const keep =
      Boolean(draft) &&
      typeof draft.original === 'string' &&
      String(draft.text ?? '').trim() !== draft.original
    if (keep) drafts.set(blockId, { text: String(draft.text ?? ''), original: draft.original })
    else drafts.delete(blockId)
    const has = drafts.has(blockId)
    // 只在「有 -> 无 / 无 -> 有」时递增；纯内容变化列表不需要重排，但磁盘要跟上
    if (!has && !had) return
    persist(articleId, drafts)
    if (has !== had) draftVersion.value += 1
  }

  /** 整篇草稿清空（删稿 / 归档时用），保留同一个 Map 实例，避免外部拿到失效引用 */
  function clearArticle(articleId) {
    if (!articleId) return
    const drafts = readDrafts(articleId)
    const had = drafts.size > 0
    drafts.clear()
    LAST.delete(articleId)
    try {
      localStorage.removeItem(draftsKey(articleId))
      localStorage.removeItem(lastKey(articleId))
    } catch {
      storageFailed.value = true
    }
    if (had) draftVersion.value += 1
  }

  /**
   * 只统计仍存在于文章里的段：后台任务可能已经把这篇文章删段 / 换段，
   * localStorage 里残留的 blockId 既点不开也不该被算进「N 段未保存」。
   */
  function listUnsaved(article) {
    if (!article?.id || !Array.isArray(article.blocks)) return []
    const drafts = readDrafts(article.id)
    if (!drafts.size) return []
    const rows = []
    article.blocks.forEach((block, index) => {
      const draft = drafts.get(block.id)
      if (draft) rows.push({ block, number: index + 1, draft })
    })
    return rows
  }

  function countUnsaved(article) {
    return listUnsaved(article).length
  }

  function lastParagraph(articleId) {
    if (!articleId) return null
    if (LAST.has(articleId)) return LAST.get(articleId)
    let blockId = null
    try {
      blockId = localStorage.getItem(lastKey(articleId)) || null
    } catch {
      blockId = null
    }
    LAST.set(articleId, blockId)
    return blockId
  }

  function setLastParagraph(articleId, blockId) {
    if (!articleId) return
    LAST.set(articleId, blockId || null)
    try {
      if (blockId) localStorage.setItem(lastKey(articleId), blockId)
      else localStorage.removeItem(lastKey(articleId))
    } catch {
      storageFailed.value = true
    }
  }

  return {
    draftVersion,
    storageFailed,
    draftsFor,
    setDraft,
    clearArticle,
    countUnsaved,
    listUnsaved,
    lastParagraph,
    setLastParagraph,
  }
}
