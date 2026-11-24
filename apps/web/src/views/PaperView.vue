<script setup>
/**
 * 成稿台：段落级编辑编排。
 *
 * 与（已下线的）创作空间的分工：
 * - 创作空间曾是一次性生成整篇、所见即所得地改；
 * - 成稿台把正文拆成有段号的段落，所有改动（对话改稿、标红修复、手工编辑）
 *   都落到具体段落上，能逐段对比、逐段退回，也能整篇撤销。
 *
 * 页面所有状态都来自后端返回的整篇快照；后台任务跑起来后按 job 字段轮询，
 * 不做乐观更新，避免和后台改稿互相覆盖。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { NButton, NInput, NModal, NPopover, NSelect } from 'naive-ui'
import PetChatPanel from '../components/PetChatPanel.vue'
import { useModelSelectionStore } from '../stores/modelSelection'
import { studioApi, uploadReport } from '../api/studio'
import { api, apiUrl } from '../api/client'
import { markdownToHtml } from '../utils/markdownSerializer'
import { confirm, toast } from '../ui/feedback'
import { useParagraphDrafts } from '../composables/useParagraphDrafts'
import { usePaperParagraphSelection } from '../composables/usePaperParagraphSelection'
import PaperParagraphPickControls from '../components/paper/PaperParagraphPickControls.vue'
import PaperLayoutPreview from '../components/paper/PaperLayoutPreview.vue'
import PaperLayoutPanel from '../components/paper/PaperLayoutPanel.vue'
import PaperMediaPanel from '../components/paper/PaperMediaPanel.vue'
import PaperPublishPanel from '../components/paper/PaperPublishPanel.vue'
import PaperBlockEditor from '../components/paper/PaperBlockEditor.vue'
import PaperAnnotation from '../components/paper/PaperAnnotation.vue'
import QualityRadar from '../components/paper/QualityRadar.vue'

const modelSelection = useModelSelectionStore()
modelSelection.load()
const articles = ref([])
const article = ref(null)
const route = useRoute()
const settings = ref({})
// 可选写作风格档由后端扫描 styles 目录下发：加一个 md 就多一档，前端不用改
const writerStyles = ref([])
const writingSkills = ref([])
const selectedSkillIds = ref([])
const skillsError = ref('')
const skillsLoading = ref(false)
async function loadWritingSkills() {
  skillsLoading.value = true
  skillsError.value = ''
  try {
    const skills = await api.get('/skill-center/skills')
    writingSkills.value = (skills || []).filter((skill) => skill.skillType === 'writing' && skill.status === 'ready')
  } catch (err) {
    skillsError.value = err.message || '加载写作技能失败'
  } finally {
    skillsLoading.value = false
  }
}
const writingSkillOptions = computed(() => writingSkills.value.map((skill) => ({ label: skill.name, value: skill.id, disabled: selectedSkillIds.value.length >= 5 && !selectedSkillIds.value.includes(skill.id) })))
const archived = ref(false)
const loading = ref(false)
const listLoading = ref(false)
const listError = ref('')
const articleQuery = ref('')
const createError = ref('')
const error = ref('')
const tab = ref('layout')
const mediaSection = ref('cover')
watch(mediaSection, (value) => {
  if (value !== 'svg') sourceSelection.stop()
})
const showPublish = ref(false)
const showAssistantTools = ref(false)
const assistantApplyBusy = ref(false)
const assistantNarrow = ref(false)
const assistantMounted = ref(false)
const assistantEntry = ref(null)
const assistantMobileEntry = ref(null)
const assistantPanel = ref(null)
const assistantClose = ref(null)
const previousSideTab = ref('layout')
const assistantOpen = computed(() => tab.value === 'assistant')
const sideTab = computed(() => assistantNarrow.value && assistantOpen.value ? previousSideTab.value : tab.value)
const assistantDrawer = computed(() => assistantNarrow.value && assistantOpen.value)
let assistantOpener = null
let assistantMedia = null
const actionBusy = ref('')
// 同一时刻只展开一段：参考项目也是单段编辑，多段同时挂 textarea 会互相抢焦点，
// 而且草稿已经落到本机，没必要用「同时开几段」来保住修改。
const editingBlockId = ref('')
// 稿纸上直接删段时用：只让被点的那一行显示「删除中」，别的段照常可点
const deletingBlockId = ref('')
const { draftVersion, storageFailed, draftsFor, listUnsaved, lastParagraph, setLastParagraph, setDraft } = useParagraphDrafts()
const lastBlockId = ref('')
const showUnsaved = ref(false)
const showOutline = ref(false)
const showCreate = ref(false)
const showArticles = ref(false)
const createForm = reactive({ topic: '', material: '', markdown: '', writerStyle: 'tutorial' })
const reportText = ref('')
const qualityKey = ref('')
const qualityEndpoint = ref('')
const reportFile = ref(null)
const sheetMode = ref('paper')
const sourceText = ref('')
const sourceOriginal = ref(null)
const sourceLoading = ref(false)
const sourceError = ref('')
const sourceDirty = computed(() => sourceOriginal.value !== null && sourceText.value !== sourceOriginal.value)
let sourceRequest = 0

async function openSource() {
  sheetMode.value = 'source'
  if (sourceDirty.value) return
  const id = article.value?.id
  const seq = ++sourceRequest
  sourceLoading.value = true
  sourceOriginal.value = null
  sourceError.value = ''
  try {
    const { markdown } = await studioApi.markdown(id)
    if (seq !== sourceRequest || article.value?.id !== id) return
    sourceText.value = markdown
    sourceOriginal.value = markdown
  } catch (err) {
    if (seq === sourceRequest) sourceError.value = err.message || '无法加载源码'
  } finally {
    if (seq === sourceRequest) sourceLoading.value = false
  }
}

async function reloadSource() {
  const currentArticle = article.value
  const currentText = sourceText.value
  const currentOriginal = sourceOriginal.value
  if (sourceDirty.value && !(await confirm('确定放弃未保存的源码修改？'))) return
  if (article.value !== currentArticle || sourceText.value !== currentText || sourceOriginal.value !== currentOriginal) return
  sourceOriginal.value = null
  return openSource()
}

function saveSource() {
  if (editingLocked.value || unsavedCount.value || !sourceDirty.value) return
  return run('source', async () => {
    try {
      sourceError.value = ''
      const snapshot = await studioApi.saveMarkdown(article.value.id, sourceText.value, sourceOriginal.value)
      sourceText.value = snapshotMarkdown(snapshot)
      sourceOriginal.value = sourceText.value
      toast('Markdown 源码已保存', 'success')
      return snapshot
    } catch (err) {
      sourceError.value = err.message || '无法保存源码'
      throw err
    }
  }, { poll: false })
}

function canUseSavedDraft() {
  if (!sourceDirty.value && !unsavedCount.value) return true
  toast('请先保存 Markdown 源码或段落修改。', 'warning')
  return false
}
const layoutOptions = ref({})
const selectedBlockId = ref('')
const getPaperBlocks = () => article.value?.blocks || []
const sourceSelection = usePaperParagraphSelection(getPaperBlocks)
const detectSelection = usePaperParagraphSelection(getPaperBlocks)
const { active: sourcePickMode, ids: sourceBlockIds } = sourceSelection
const { active: detectPickMode, ids: detectBlockIds } = detectSelection
const activeSelection = computed(() => detectPickMode.value ? detectSelection : sourcePickMode.value ? sourceSelection : null)
const svgSourceIds = computed(() => {
  if (sourceSelection.touched.value) return sourceBlockIds.value
  return getPaperBlocks().some((block) => block.id === selectedBlockId.value) ? [selectedBlockId.value] : []
})
const visiblePickedIds = computed(() => {
  if (sideTab.value === 'media') return svgSourceIds.value
  if (sideTab.value === 'quality' && detectScope.value === 'selection') return detectBlockIds.value
  return []
})
watch(sideTab, (value) => {
  if (value !== 'media') sourceSelection.stop()
  if (value !== 'quality') detectSelection.stop()
})
const detectScope = ref('all')
const qualitySource = ref(localStorage.getItem('hanyue-quality-source') || 'auto')
let timer = null
let stateRequest = 0
let articleRequest = 0

const blockStatus = (block) => block.check?.status || ''
const statusLabel = { flag: '标红', fixed: '已改', pass: '通过', kept: '已接受' }
const kindLabel = { para: '正文', heading: '小标题', quote: '引用', list: '列表', code: '代码', media: '图片', table: '表格', rule: '分隔线' }

const checkable = computed(() => (article.value?.blocks || []).filter((block) => ['para', 'quote', 'list'].includes(block.kind)))
const flagged = computed(() => checkable.value.filter((block) => blockStatus(block) === 'flag').length)
const fixedCount = computed(() => checkable.value.filter((block) => blockStatus(block) === 'fixed').length)
const sourceLabel = computed(() => {
  const source = article.value?.zhuque?.source
  if (source === 'api') return '朱雀 API'
  if (source === 'pdf') return '朱雀报告'
  if (source === 'local') return '本地规则（未接朱雀）'
  return '尚未检测'
})
const qualityDimensions = computed(() => {
  const report = article.value?.zhuque
  if (report?.source !== 'local' || !Array.isArray(report.dimensions)) return []
  return report.dimensions.filter((item) => item?.id && item.label && Number.isFinite(item.score) && item.score >= 0 && item.score <= 100)
})
const job = computed(() => article.value?.job || null)
// 草稿变化不会改 article 对象，这里显式订阅 draftVersion，
// 否则「有未保存的修改」和工具条上的计数要等下一次快照才更新。
const dirtyIds = computed(() => {
  draftVersion.value
  const id = article.value?.id
  return new Set(id ? draftsFor(id).keys() : [])
})
const unsavedList = computed(() => {
  draftVersion.value
  return article.value ? listUnsaved(article.value) : []
})
const unsavedCount = computed(() => unsavedList.value.length)
// 后台任务或别的动作在跑时不给进编辑，避免本段保存被后端拒绝。
const switchingLocked = computed(() => Boolean(actionBusy.value || deletingBlockId.value))
const editingLocked = computed(() => Boolean(job.value || switchingLocked.value || loading.value))
const waitingBlocks = computed(() => checkable.value.filter((block) => blockStatus(block) === 'flag'))
const lastBlock = computed(() => {
  const blocks = article.value?.blocks || []
  const index = blocks.findIndex((block) => block.id === lastBlockId.value)
  return index < 0 ? null : { block: blocks[index], number: index + 1 }
})
// 「下一处待处理」从当前编辑段（没有就看上次停下的段）往后找第一个标红段，
// 到末尾绕回开头：改到最后一段时不用再滚回顶部找剩下的标红段。
const nextWaiting = computed(() => {
  const blocks = article.value?.blocks || []
  if (!waitingBlocks.value.length) return null
  const anchorId = editingBlockId.value || lastBlockId.value
  const position = blocks.findIndex((block) => block.id === anchorId)
  const next = position < 0
    ? waitingBlocks.value[0]
    : waitingBlocks.value.find((block) => blocks.findIndex((item) => item.id === block.id) > position) || waitingBlocks.value[0]
  return { block: next, number: blocks.findIndex((item) => item.id === next.id) + 1 }
})
const outline = computed(() =>
  (article.value?.blocks || [])
    .map((block, index) => ({ block, number: index + 1 }))
    .filter((item) => item.block.kind === 'heading'),
)
// 就绪度前端算：接口只给正文/检测/标题/封面/横幅五项，不额外多一次请求
const readiness = computed(() => {
  const current = article.value
  if (!current) return { items: [], done: 0, total: 5 }
  const pending = (current.blocks || []).some((block) => ['flag', 'fixed'].includes(block.check?.status))
  const items = [
    { id: 'body', label: '正文', done: Boolean(current.blocks?.length) },
    { id: 'title', label: '标题', done: Boolean(current.title) },
    { id: 'quality', label: '检测', done: Boolean(current.zhuque?.round) && !pending },
    { id: 'cover', label: '封面', done: Boolean(current.cover?.assetId || current.cover?.id) },
    { id: 'banner', label: '横幅', done: Boolean(current.banners?.[current.bannerStyle]?.spec) },
  ]
  return { items, done: items.filter((item) => item.done).length, total: items.length }
})

// ---- 写作风格档 ----
// 档位只决定结构、篇幅和取舍（教程 / 方法论 / 测评 / 随笔 / 软文），
// 人设与用词始终来自 writer skill，所以切换风格不会换掉作者本人。
const styleOptions = computed(() => writerStyles.value.map((item) => ({ label: item.label, value: item.value })))
const styleHint = (value) => writerStyles.value.find((item) => item.value === value)?.hint || ''
const writerStyle = computed(() => article.value?.writerStyle || createForm.writerStyle)
const writerStyleHint = computed(() => styleHint(writerStyle.value))
const createStyleHint = computed(() => styleHint(createForm.writerStyle))

const matchingArticles = computed(() => {
  const query = articleQuery.value.trim().toLocaleLowerCase()
  if (!query) return articles.value
  return articles.value.filter((item) => [item.title || '未命名文章', item.topic]
    .some((value) => String(value || '').toLocaleLowerCase().includes(query)))
})

function showArticlePicker() {
  articleQuery.value = ''
  showArticles.value = true
  if (listError.value) loadState()
}

function filterArticles(next) {
  if (archived.value === next && !listError.value) return
  archived.value = next
  loadState()
}

async function loadState() {
  const request = ++stateRequest
  listLoading.value = true
  listError.value = ''
  try {
    const data = await studioApi.state(archived.value)
    if (request !== stateRequest) return
    articles.value = data.articles || []
    settings.value = data.settings || {}
    writerStyles.value = data.writerStyles || []
    if (!writerStyles.value.some((item) => item.value === createForm.writerStyle)) {
      createForm.writerStyle = writerStyles.value[0]?.value || 'tutorial'
    }
  } catch (err) {
    if (request === stateRequest) listError.value = err.message || '无法加载稿件列表'
  } finally {
    if (request === stateRequest) listLoading.value = false
  }
}

async function openArticle(id) {
  if (!id || switchingLocked.value) return false
  if (id === article.value?.id) {
    articleRequest += 1
    loading.value = false
    showArticles.value = false
    return true
  }
  const previousArticle = article.value
  const previousSource = sourceText.value
  const previousOriginal = sourceOriginal.value
  const previousDrafts = draftVersion.value
  if (sourceDirty.value && !(await confirm('Markdown 源码尚未保存，确定要切换稿件并放弃修改？'))) return false
  if (storageFailed.value && unsavedCount.value && !(await confirm('本地暂存失败，切换稿件可能丢失未保存修改。仍要切换吗？'))) return false
  if (article.value !== previousArticle || sourceText.value !== previousSource || sourceOriginal.value !== previousOriginal || draftVersion.value !== previousDrafts || switchingLocked.value || disposed) return false
  loading.value = true
  error.value = ''
  const requestId = ++articleRequest
  try {
    const next = await studioApi.get(id)
    if (requestId !== articleRequest) return false
    activateArticle(next)
    showArticles.value = false
    return true
  } catch (err) {
    if (requestId === articleRequest) {
      error.value = err.message
      if (showArticles.value) toast(err.message || '无法打开稿件', 'error')
    }
    return false
  } finally {
    if (requestId === articleRequest) loading.value = false
  }
}

function activateArticle(next) {
  if (article.value?.id !== next.id) {
    showAssistantTools.value = false
    showCreate.value = false
    sourceRequest += 1
    sourceOriginal.value = null
    sourceText.value = ''
    sourceLoading.value = false
    sourceError.value = ''
    if (sheetMode.value === 'source') sheetMode.value = 'paper'
  }
  stopPolling()
  applySnapshot(next)
  layoutOptions.value = { ...(next.layoutOptions || {}) }
  selectedBlockId.value = ''
  sourceSelection.reset()
  detectSelection.reset()
  detectScope.value = 'all'
  // 草稿仍保存在本机；这里只重置编辑器和导航的瞬态。
  editingBlockId.value = ''
  lastBlockId.value = lastParagraph(next.id) || ''
  showUnsaved.value = false
  showOutline.value = false
  if (next.job) startPolling()
}

function applySnapshot(next) {
  if (!next) return
  // 列表项只保留元信息，正文快照单独保存
  article.value = next
  const meta = articles.value.find((item) => item.id === next.id)
  if (meta) Object.assign(meta, { title: next.title, updatedAt: next.updatedAt })
}

async function run(label, work, { poll = true } = {}) {
  if (switchingLocked.value || loading.value || disposed) return
  actionBusy.value = label
  error.value = ''
  if (label === 'create') createError.value = ''
  try {
    const snapshot = await work()
    if (disposed) return
    if (snapshot) applySnapshot(snapshot)
    if (poll && snapshot?.job) startPolling()
    else await loadState()
  } catch (err) {
    if (disposed) return
    error.value = err.message
    if (label === 'create') createError.value = err.message || '无法新建稿件'
    toast(err.message, 'error')
  } finally {
    actionBusy.value = ''
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(async () => {
    if (!article.value) return stopPolling()
    try {
      const id = article.value.id
      const snapshot = await studioApi.get(id)
      if (article.value?.id !== id) return
      applySnapshot(snapshot)
      if (!snapshot.job) {
        stopPolling()
        await loadState()
      }
    } catch {
      stopPolling()
    }
  }, 1500)
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function openPet(mode, event) {
  if (!assistantOpen.value) {
    previousSideTab.value = tab.value
    assistantOpener = event?.currentTarget || null
  }
  showAssistantTools.value = mode === 'tools'
  assistantMounted.value = true
  tab.value = 'assistant'
}

function closeAssistant() {
  if (!assistantOpen.value) return
  tab.value = previousSideTab.value
  nextTick(() => {
    if (assistantOpen.value) return
    const entry = assistantNarrow.value ? assistantMobileEntry.value : assistantEntry.value
    const opener = assistantOpener?.getClientRects().length ? assistantOpener : entry
    opener?.focus()
    assistantOpener = null
  })
}

function onAssistantKeydown(event) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    closeAssistant()
    return
  }
  if (event.key !== 'Tab' || !assistantDrawer.value) return
  const buttons = [...assistantPanel.value.querySelectorAll('button, input, textarea, select, a[href], summary, [tabindex]')]
    .filter((element) => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length)
  const first = buttons[0]
  const last = buttons.at(-1)
  if ((event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
    event.preventDefault()
    const target = event.shiftKey ? last : first
    target?.focus()
  }
}

function syncAssistantViewport() {
  assistantNarrow.value = assistantMedia?.matches ?? false
}

watch(assistantDrawer, (open) => {
  if (open) assistantClose.value?.focus()
}, { flush: 'post' })

function snapshotMarkdown(snapshot) {
  // 与服务端的 to_markdown 序列化一致，以便比较稿纸与 GET 的基线。
  const parts = [snapshot.front, snapshot.title && `# ${snapshot.title}`].filter(Boolean)
  return [...parts, ...(snapshot.blocks || []).map((block) => block.text)].join('\n\n') + '\n'
}

async function applyAssistantMarkdown(markdown) {
  const body = typeof markdown === 'string'
    ? markdown.replace(/\r\n?/g, '\n').replace(/^---\n[\s\S]*?\n---(?:\n|$)/, '').trim()
    : ''
  if (!body || /^#\s+[^\n]+$/.test(body)) {
    throw new Error('只有包含正文的初稿才能写入当前稿。')
  }
  if (assistantApplyBusy.value) throw new Error('正在核对初稿，请稍候。')
  const current = article.value
  const request = articleRequest
  const routeId = route.query.id
  const stale = '当前稿件已有新版本，初稿已保留。请重新核对最新正文后再试。'
  const assertWritable = () => {
    if (!current?.id || disposed || loading.value || request !== articleRequest || route.query.id !== routeId || article.value?.id !== current.id) {
      throw new Error('当前稿件已切换或正在加载，不能写入。')
    }
    if (sourceDirty.value || unsavedCount.value) throw new Error('请先处理未保存的 Markdown 源码或段落修改。')
    if (editingBlockId.value) throw new Error('请先完成或关闭当前段落编辑，再写入初稿。')
    if (job.value || switchingLocked.value) throw new Error('当前稿件有任务正在处理，请稍后再试。')
  }
  assertWritable()
  const viewedMarkdown = snapshotMarkdown(current)
  assistantApplyBusy.value = true
  try {
    const { markdown: baseline } = await studioApi.markdown(current.id)
    assertWritable()
    if (baseline !== viewedMarkdown || baseline !== snapshotMarkdown(article.value)) throw new Error(stale)
    if (current.blocks?.length && !(await confirm('写入初稿会替换当前标题和正文，可用撤销恢复上一版。确定写入？', '写入当前稿'))) return false
    assertWritable()
    if (baseline !== snapshotMarkdown(article.value)) throw new Error(stale)
    // 只在真正提交时锁稿，确认期间仍检查切稿、编辑和后台任务。
    actionBusy.value = 'assistant-apply'
    try {
      const snapshot = await studioApi.saveMarkdown(current.id, markdown, baseline)
      if (!disposed && article.value?.id === current.id) {
        applySnapshot(snapshot)
        sourceRequest += 1
        sourceText.value = snapshotMarkdown(snapshot)
        sourceOriginal.value = sourceText.value
        sourceLoading.value = false
        sourceError.value = ''
        selectedBlockId.value = ''
        toast('初稿已写入当前稿', 'success')
      }
      return true
    } catch (err) {
      if (err.status === 409) throw new Error('写入遇到版本冲突或后台任务，初稿已保留。请重新核对当前稿件。')
      throw err
    } finally {
      actionBusy.value = ''
    }
  } finally {
    assistantApplyBusy.value = false
  }
}

function openCreateTools() {
  showArticles.value = false
  showCreate.value = true
  openPet('tools')
}

const createArticle = async () => {
  const previousArticle = article.value
  const previousSource = sourceText.value
  const previousOriginal = sourceOriginal.value
  const previousDrafts = draftVersion.value
  if (sourceDirty.value && !(await confirm('Markdown 源码尚未保存，确定要新建稿件并放弃修改？'))) return
  if (storageFailed.value && unsavedCount.value && !(await confirm('本地暂存失败，新建稿件可能丢失未保存修改。仍要继续吗？'))) return
  if (article.value !== previousArticle || sourceText.value !== previousSource || sourceOriginal.value !== previousOriginal || draftVersion.value !== previousDrafts) return
  return run('create', async () => {
    const snapshot = await studioApi.create({
      topic: createForm.topic,
      material: createForm.material,
      markdown: createForm.markdown,
      writerStyle: createForm.writerStyle,
      skillIds: createForm.markdown.trim() ? [] : selectedSkillIds.value,
      draft: !createForm.markdown.trim(),
      titles: true,
    })
    activateArticle(snapshot)
    showCreate.value = false
    showArticles.value = false
    createForm.topic = ''
    createForm.material = ''
    createForm.markdown = ''
    archived.value = false
    return null
  })
}

// ---- 对话改稿 ----
// 改稿最费劲的往往不是「改什么」，而是「怎么说」。常用说法做成胶囊，
// 点一下就把话说全；详细指令放 title 里，鼠标停一下能看全。
/** 使用稳定段落 ID 指定改稿范围，服务端会验证模型返回的操作。 */
const chatAnchor = computed(() => {
  const blocks = article.value?.blocks || []
  const index = blocks.findIndex((block) => block.id === selectedBlockId.value)
  if (index < 0) return null
  return { id: blocks[index].id, number: index + 1, text: plainText(blocks[index].text) }
})

const sendPetEdit = async (value, modelId) => {
  const text = String(value || '').trim()
  if (!text || !article.value || editingLocked.value) throw new Error('稿件正在处理，请稍后再试。')
  if (sourceDirty.value || unsavedCount.value || editingBlockId.value) {
    throw new Error('请先保存 Markdown 源码和段落修改，并关闭段落编辑。')
  }
  const articleId = article.value.id
  const requestId = articleRequest
  const target = chatAnchor.value
  actionBusy.value = 'chat'
  error.value = ''
  try {
    const snapshot = await studioApi.chat(articleId, text, { targetBlockId: target?.id || null, preview: true, ...(modelId ? { modelId } : {}) })
    if (article.value?.id !== articleId || articleRequest !== requestId) throw new Error('稿件已切换，改稿任务仍在原稿件中。')
    applySnapshot(snapshot)
    if (snapshot?.job) startPolling()
    else await loadState()
  } catch (err) {
    if (article.value?.id === articleId && articleRequest === requestId) error.value = err.message
    throw err
  } finally {
    if (article.value?.id === articleId && articleRequest === requestId) actionBusy.value = ''
  }
}

async function resolveChatProposal(proposalId, accept) {
  if (!article.value || editingLocked.value) throw new Error('稿件正在处理，请稍后再试。')
  if (sourceDirty.value || unsavedCount.value || editingBlockId.value) {
    throw new Error('请先保存 Markdown 源码和段落修改，并关闭段落编辑。')
  }
  const id = article.value.id
  const requestId = articleRequest
  actionBusy.value = 'chat-proposal'
  try {
    const snapshot = await (accept ? studioApi.acceptChatProposal(id, proposalId) : studioApi.discardChatProposal(id, proposalId))
    if (article.value?.id !== id || articleRequest !== requestId) throw new Error('稿件已切换，请返回原稿查看结果。')
    applySnapshot(snapshot)
  } finally {
    if (article.value?.id === id && articleRequest === requestId) actionBusy.value = ''
  }
}

const assistantInitialPrompt = ref(null)
async function ensureAssistantArticle(text) {
  if (article.value || switchingLocked.value || loading.value) return
  const prompt = String(text || '')
  actionBusy.value = 'assistant-create'
  try {
    const created = await studioApi.create({ topic: '', material: '', markdown: '', draft: false, titles: false })
    if (!created?.id) throw new Error('创建稿件失败，请重试。')
    assistantInitialPrompt.value = { articleId: created.id, text: prompt }
    activateArticle(created)
    await loadState()
    toast('已创建空稿，请确认要求后发送。', 'success')
  } catch (err) {
    toast(err.message || '创建稿件失败，请重试。', 'error')
  } finally {
    actionBusy.value = ''
  }
}

// 助手新建成稿后直接打开，避免用户还要去列表里翻
async function onPetCreated(id) {
  if (!id || disposed) return
  if (await openArticle(id)) {
    archived.value = false
    toast('已新建成稿并打开。', 'success')
  } else {
    toast('已新建成稿，但未切换；可稍后从稿件列表打开。', 'warning')
  }
  await loadState()
}

// ---- 段落编辑：进入 / 退出只有一条路径 ----
//
// 点正文任意位置就进编辑（参考项目的约定）。进入时把「下一段」记下来，
// 因为保存后快照会整体替换，那时再按 editingBlockId 反查已经查不到（本段可能被删）。
const editorNextId = ref('')

function openEditor(block) {
  // 后台任务正在改这一篇、或别的动作还没结束时不让进：后端 _busy 会 409，
  // 与其让人写完再被拒，不如点不进去
  if (editingLocked.value) return
  if (!block) return
  editingBlockId.value = block.id
  selectedBlockId.value = block.id // 素材面板跟着走：进哪一段就是在给哪一段配图
  lastBlockId.value = block.id
  if (article.value) setLastParagraph(article.value.id, block.id)
  const blocks = article.value?.blocks || []
  editorNextId.value = blocks[blocks.findIndex((item) => item.id === block.id) + 1]?.id || ''
  nextTick(() => document.getElementById(`paper-block-${block.id}`)?.scrollIntoView({ block: 'nearest' }))
}

function closeEditor(payload = {}) {
  const editId = editingBlockId.value
  if (editId) {
    // 编辑态是瞬态，退出时顺手把这一段也记成「上次停下的位置」
    lastBlockId.value = editId
    if (article.value) setLastParagraph(article.value.id, editId)
  }
  editingBlockId.value = ''
  if (!payload.advance) return
  const next = (article.value?.blocks || []).find((block) => block.id === editorNextId.value)
  if (next) nextTick(() => openEditor(next))
}

function onBlockSaved(snapshot) {
  if (snapshot?.id === article.value?.id) applySnapshot(snapshot)
}

/**
 * 稿纸段号旁的快捷删除：不进编辑器就能删掉整段。
 *
 * 这里直接调 updateBlock 传空文本（后端 pop 掉该段并留快照），
 * 所以和编辑器里的「删除本段」是同一条链路，撤销行为也一致。
 */
async function deleteBlock(block, index) {
  if (editingLocked.value || deletingBlockId.value) return
  if (activeSelection.value) return
  // 这一段正开着编辑器：交给编辑器里的删除，它会一并处理草稿、焦点和冲突态
  if (editingBlockId.value === block.id) return
  if (!article.value) return
  // 有未保存草稿时不静默丢：先确认，避免「删段顺手把改了一半的稿子也删了」
  if (dirtyIds.value.has(block.id)) {
    const ok = await confirm(
      `第 ${index + 1} 段有未保存的修改，删除会一并丢弃。仍要删除吗？`,
      '删除段落',
    )
    if (!ok) return
  }
  const articleId = article.value.id
  deletingBlockId.value = block.id
  try {
    const snapshot = await studioApi.updateBlock(articleId, block.id, {
      text: '',
      expectedText: block.text,
    })
    setDraft(articleId, block.id, null)
    if (snapshot?.id === article.value?.id) applySnapshot(snapshot)
    // 删掉的可能正是「接着改」「素材面板」指向的那一段，顺手清掉，别留悬空引用
    if (selectedBlockId.value === block.id) selectedBlockId.value = ''
    if (lastBlockId.value === block.id) lastBlockId.value = ''
    toast(`第 ${index + 1} 段已删除，可用「撤销一步」恢复`, 'success')
  } catch (err) {
    toast(err?.message || '删除失败', 'error')
  } finally {
    deletingBlockId.value = ''
  }
}

function jumpTo(blockId, open = false) {
  showUnsaved.value = false
  showOutline.value = false
  const block = (article.value?.blocks || []).find((item) => item.id === blockId)
  if (!block) return
  if (open) openEditor(block)
  else nextTick(() => document.getElementById(`paper-block-${block.id}`)?.scrollIntoView({ block: 'center' }))
}

function startSourcePick() {
  openMedia('svg')
  closeEditor()
  detectSelection.stop()
  sourceSelection.start(selectedBlockId.value)
}

function toggleSourcePick() {
  if (sourcePickMode.value) sourceSelection.stop()
  else startSourcePick()
}

function setDetectScope(value) {
  detectScope.value = value
  if (value !== 'selection') detectSelection.stop()
}

function setQualitySource(value) {
  qualitySource.value = value
  localStorage.setItem('hanyue-quality-source', value)
}

function startDetectPick() {
  sheetMode.value = 'paper'
  closeEditor()
  sourceSelection.stop()
  detectSelection.start(selectedBlockId.value)
}

function onBlockClick(block, event) {
  const selection = activeSelection.value || (event?.ctrlKey || event?.metaKey ? sourceSelection : null)
  if (selection) {
    selection.toggle(block.id, event?.shiftKey)
    return
  }
  openEditor(block)
}

const fixBlock = (block) => run('fix', () => studioApi.fixBlock(article.value.id, block.id))
const keepBlock = (block) => run('keep', () => studioApi.keepBlock(article.value.id, block.id), { poll: false })
const revertBlock = (block) => run('revert', () => studioApi.revertBlock(article.value.id, block.id), { poll: false })
const undo = () => run('undo', () => studioApi.undo(article.value.id), { poll: false })
const detect = () => {
  if (!article.value) return
  const body = { source: qualitySource.value }
  if (detectScope.value === 'selection' && detectBlockIds.value.length) body.blockIds = detectBlockIds.value
  return run('detect', () => studioApi.detect(article.value.id, body))
}
const fixAll = () => run('fixAll', () => studioApi.fixAll(article.value.id))
const stop = () => run('stop', () => studioApi.stop(article.value.id), { poll: false })
const regenTitles = () => run('titles', () => studioApi.titles(article.value.id))
const pickTitle = (text) => run('title', () => studioApi.pickTitle(article.value.id, text), { poll: false })

// 写正文是整篇覆盖：有正文时先让人确认。后端在覆盖前会存一版快照，撤销能退回来。
const draftArticle = async () => {
  if (!article.value || job.value || actionBusy.value) return
  const currentArticle = article.value
  if (currentArticle.blocks?.length && !(await confirm('重写会整篇覆盖现在的正文（覆盖前会自动存一版，可以撤销）。继续吗？'))) return
  if (article.value !== currentArticle || job.value || actionBusy.value) return
  return run('draft', () => studioApi.draft(currentArticle.id, writerStyle.value, selectedSkillIds.value))
}

// 只换档位不重写：下次写正文、以及之后的对话改稿都按新档位走
const changeWriterStyle = (value) => {
  if (!article.value || !value || value === article.value.writerStyle) return
  return run('style', () => studioApi.setWriterStyle(article.value.id, value), { poll: false })
}

function changeTitle(value) {
  const next = String(value || '').trim()
  if (!article.value || !next || next === article.value.title) return
  pickTitle(next)
}

// ---- 排版 / 素材 / 发布：三个面板只负责交互，快照统一回到这里落 ----
//
// 面板自己不发整篇请求（避免两个面板同时写覆盖彼此），只把最新快照 emit 上来。
function onLayoutChange(options) {
  layoutOptions.value = { ...(options || {}) }
}

function applyPanelSnapshot(next) {
  if (next?.id && next.id === article.value?.id) applySnapshot(next)
  if (next?.id === article.value?.id && next?.job) startPolling()
}

// 后台任务可能把正在编辑的这一段删掉或整段替换掉（fixall 会改 text 但 id 不变，
// 只有「空段保存」或外部删段才会消失）。段没了就把编辑器收起来，
// 不然 textarea 会挂在一段已经不存在的正文上，保存必然 404。
watch(
  () => (article.value?.blocks || []).map((block) => block.id).join(','),
  () => {
    if (!editingBlockId.value) return
    if (!(article.value?.blocks || []).some((block) => block.id === editingBlockId.value)) editingBlockId.value = ''
  },
)

// 段内 Markdown 直接渲染成样式（加粗、小标题、引用），不再是 <pre> 直出原文。
// 与「创作空间」的正文预览共用同一个渲染器，避免两处 Markdown 表现不一致。
//
// 正文里的图片写成 `asset://<id>`（后端内部约定），浏览器不认这个协议，
// 稿纸里会变成裂图；这里换成素材的签名直链，插图才看得见。
const assetLinks = computed(() => {
  const map = new Map()
  const push = (entry) => {
    if (!entry?.assetId || !entry?.url) return
    map.set(entry.assetId, apiUrl(entry.url))
  }
  push(article.value?.cover)
  ;(article.value?.inlineDiagrams || []).forEach(push)
  return map
})

const renderBody = (text) => {
  const html = markdownToHtml(text)
  if (!html.includes('asset://')) return html
  return html.replace(/asset:\/\/([A-Za-z0-9_-]+)/g, (match, id) => assetLinks.value.get(id) || match)
}

// 目录/未保存列表里的摘要：去掉图片与标记符号，只留能认出是哪一段的文字
const plainText = (text) =>
  String(text || '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '[图片]')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*`_~>]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60)

// 两个下拉都靠外部点击收起：稿纸很长，用户点到别处时菜单不该一直浮在上面
function closeNav(event) {
  if (!showUnsaved.value && !showOutline.value) return
  if (event.target.closest?.('.nav-pop')) return
  showUnsaved.value = false
  showOutline.value = false
}

// 只在本机暂存失败时才拦关闭：草稿正常写进 localStorage 的话，
// 刷新页面不丢修改，没必要每次都弹一个浏览器确认框。
onBeforeRouteLeave(() => !sourceDirty.value || confirm('Markdown 源码尚未保存，确定要离开并放弃修改？'))

function onBeforeUnload(event) {
  if (!sourceDirty.value && (!storageFailed.value || !unsavedCount.value)) return
  event.preventDefault()
  event.returnValue = ''
}

// 切到「排版预览」时右栏一并切到排版设置：用户点一次就能看到效果并直接调样式。
// 无条件切 —— 停在素材页时也照样跳过去，否则点完「排版预览」右边还是别的面板，
// 看上去像按钮没反应。从排版切回稿纸则不动右栏，方便对照着改。
function openLayout() {
  sheetMode.value = 'layout'
  tab.value = 'layout'
}

function openMedia(section) {
  mediaSection.value = section
  sheetMode.value = 'paper'
  tab.value = 'media'
}

// 就绪度胶囊点了直接跳到该干活的地方：省得再在右栏里翻页签
function jumpToReadiness(id) {
  if (id === 'body') {
    sheetMode.value = 'paper'
    nextTick(() => document.querySelector('.blocks')?.scrollTo({ top: 0 }))
    return
  }
  if (id === 'title') openPet('tools')
  else if (id === 'quality') tab.value = 'quality'
  else if (id === 'cover') openMedia('cover')
  else if (id === 'banner') tab.value = 'layout'
}

// AI 拟的插图建议可能指向别的段：把选中段落换过去，稿纸也滚到那一段
function onSelectBlock(blockId) {
  if (!blockId) return
  selectedBlockId.value = blockId
  sheetMode.value = 'paper'
  nextTick(() => document.getElementById(`paper-block-${blockId}`)?.scrollIntoView({ block: 'center' }))
}

function notifyPanel(text) {
  if (text) toast(text, 'success')
  // 发布/排期只改稿件头字段（publishedAt / scheduledFor），面板拿不到新快照，
  // 这里补一次拉取，否则界面上「已发布」状态要等下次进页面才更新。
  if (text && /发布|排期|存入文章管理/.test(text)) refreshArticle()
}

async function refreshArticle() {
  if (!article.value) return
  try {
    applySnapshot(await studioApi.get(article.value.id))
  } catch {
    // 刷新失败不该打断用户刚完成的动作，界面保留旧快照即可
  }
}

const saveQuality = () =>
  run(
    'quality-settings',
    async () => {
      settings.value = await studioApi.saveSettings({
        zhuqueKey: qualityKey.value,
        zhuqueEndpoint: qualityEndpoint.value || null,
      })
      qualityKey.value = ''
      toast(settings.value.zhuqueConfigured ? '朱雀 Key 已保存' : '已清除朱雀 Key', 'success')
      return null
    },
    { poll: false },
  )

const exportArticle = () =>
  run(
    'export',
    async () => {
      if (!canUseSavedDraft()) return null
      const result = await studioApi.exportToContent(article.value.id)
      toast(`已存档：${result.title}`, 'success')
      return null
    },
    { poll: false },
  )

const submitReport = () =>
  run('report', async () => {
    if (reportFile.value) {
      const snapshot = await uploadReport(article.value.id, reportFile.value)
      reportFile.value = null
      startPolling()
      return snapshot
    }
    if (!reportText.value.trim()) throw new Error('先上传报告 PDF，或粘贴报告文字')
    const response = await fetch(`/api/v1/studio/articles/${article.value.id}/zhuque/report`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('hanyue-token') || ''}`,
      },
      body: JSON.stringify({ text: reportText.value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload?.message || payload?.detail?.message || '报告回填失败')
    startPolling()
    return payload
  })

function pickReport(event) {
  reportFile.value = event.target.files?.[0] || null
}

const assistantHotspot = computed(() => {
  const material = article.value?.material?.trim() || ''
  if (!material.startsWith('【热点选题】')) return null
  return { title: material.split('\n', 1)[0].slice('【热点选题】'.length).trim() || article.value.topic, material }
})

let routeReady = false
let routePending = false
let disposed = false

async function syncRouteArticle(initial = false) {
  const wanted = typeof route.query.id === 'string' ? route.query.id : ''
  const targetId = wanted || (initial ? articles.value[0]?.id : '')
  if (switchingLocked.value) {
    routePending = true
    return
  }
  // A deep link may point to an archived draft or one outside the list limit.
  if (targetId && !(await openArticle(targetId))) return
  if (disposed || route.query.pet !== '1' || wanted !== (route.query.id || '') || (wanted && article.value?.id !== wanted)) return
  openPet('create')
}

onMounted(async () => {
  assistantMedia = window.matchMedia?.('(max-width: 1180px)')
  syncAssistantViewport()
  assistantMedia?.addEventListener('change', syncAssistantViewport)
  window.addEventListener('beforeunload', onBeforeUnload)
  document.addEventListener('pointerdown', closeNav)
  loadWritingSkills()
  await loadState()
  if (disposed) return
  routeReady = true
  await syncRouteArticle(!article.value)
})

watch(() => [route.query.id, route.query.pet], () => {
  if (routeReady) syncRouteArticle()
})

watch(switchingLocked, (locked) => {
  if (locked || !routePending) return
  routePending = false
  syncRouteArticle()
})

onBeforeUnmount(() => {
  disposed = true
  articleRequest += 1
  stateRequest += 1
  stopPolling()
  assistantMedia?.removeEventListener('change', syncAssistantViewport)
  window.removeEventListener('beforeunload', onBeforeUnload)
  document.removeEventListener('pointerdown', closeNav)
})
</script>

<template>
  <div class="paper-view">
    <header class="paper-head" :inert="assistantDrawer || undefined">
      <div class="head-actions">
        <NButton size="small" @click="showArticlePicker">选择稿件</NButton>
        <button ref="assistantMobileEntry" type="button" class="assistant-mobile-entry nav-btn" aria-controls="paper-assistant" :aria-expanded="assistantOpen" @click="openPet(undefined, $event)">创作助手</button>
      </div>
      <div class="head-actions head-actions-end">
        <NButton size="small" type="primary" :disabled="!article || editingLocked" :loading="actionBusy === 'export'" @click="exportArticle">文章存档</NButton>
        <NButton size="small" :disabled="!article || editingLocked" @click="canUseSavedDraft() && (showPublish = true)">推送草稿</NButton>
      </div>
    </header>

    <div v-if="error" class="paper-alert error" role="alert">{{ error }}</div>
    <div v-if="article?.notice" class="paper-alert" :class="{ error: article.notice.error }" role="status">
      <span>{{ article.notice.text }}</span>
      <button class="text-button" @click="studioApi.clearNotice(article.id).then(applyPanelSnapshot)">知道了</button>
    </div>


    <NModal v-model:show="showArticles" preset="card" title="选择稿件" class="paper-dialog">
      <div class="article-picker-head">
        <div class="article-filter" role="group" aria-label="稿件分类">
          <NButton size="small" :type="!archived ? 'primary' : 'default'" :aria-pressed="!archived" @click="filterArticles(false)">当前稿件</NButton>
          <NButton size="small" :type="archived ? 'primary' : 'default'" :aria-pressed="archived" @click="filterArticles(true)">归档稿件</NButton>
        </div>
        <NInput v-model:value="articleQuery" size="small" clearable placeholder="搜索标题或选题" :input-props="{ 'aria-label': '搜索标题或选题' }" />
      </div>
      <div class="paper-list" :aria-busy="listLoading || loading">
        <p v-if="listLoading" class="empty" role="status">正在加载稿件…</p>
        <div v-else-if="listError" class="picker-empty" role="alert">
          <p class="empty">{{ listError }}</p>
          <NButton size="small" @click="loadState">重试</NButton>
        </div>
        <div v-else-if="!matchingArticles.length" class="picker-empty">
          <p class="empty" role="status">{{ articleQuery.trim() ? '没有匹配的稿件。' : archived ? '还没有归档稿件。' : '还没有稿件。' }}</p>
          <NButton v-if="articleQuery.trim()" size="small" @click="articleQuery = ''">清除搜索</NButton>
          <NButton v-else-if="!archived" size="small" :disabled="loading || switchingLocked" @click="openCreateTools">前往助手新建</NButton>
        </div>
        <template v-else>
          <button
            v-for="item in matchingArticles"
            :key="item.id"
            type="button"
            class="list-item"
            :class="{ active: article?.id === item.id }"
            :aria-current="article?.id === item.id ? 'true' : undefined"
            :disabled="loading || switchingLocked"
            @click="openArticle(item.id)"
          >
            <span class="list-title">{{ item.title || '未命名文章' }}</span>
            <span class="list-meta">
              {{ item.chars }} 字 · {{ item.blocks }} 段
              <span v-if="article?.id === item.id" class="badge current">正在编辑</span>
              <span v-if="item.review" class="badge flag">{{ item.review }} 待改</span>
              <span v-else-if="item.checked" class="badge pass">已检测</span>
            </span>
          </button>
        </template>
      </div>
    </NModal>

    <div class="paper-grid">
      <main class="paper-column" :inert="assistantDrawer || undefined">
        <template v-if="article">
          <section class="paper-sheet" :class="{ 'with-preview': sheetMode === 'layout' }" :aria-busy="loading">
            <!-- 稿纸头部：标题 + 两种看法 + 这篇稿件的就绪度。
                 切换按钮放在标题同一行，看的是「这篇稿子」，就该贴在稿子上，
                 挤在页头工具条里反而要找。 -->
            <div class="sheet-head">
              <div class="sheet-head-row">
                <NInput
                  v-model:value="article.title"
                  class="sheet-title"
                  size="large"
                  placeholder="给文章一个标题"
                  :disabled="sheetMode === 'source'"
                  @blur="changeTitle(article.title)"
                />
                <div class="view-switch" role="tablist" aria-label="查看方式">
                  <button
                    role="tab"
                    :aria-selected="sheetMode === 'paper'"
                    :class="{ active: sheetMode === 'paper' }"
                    @click="sheetMode = 'paper'"
                  >段号稿纸</button>
                  <button
                    role="tab"
                    :aria-selected="sheetMode === 'layout'"
                    :class="{ active: sheetMode === 'layout' }"
                    @click="openLayout"
                  >排版预览</button>
                  <button
                    type="button"
                    role="tab"
                    :aria-selected="sheetMode === 'source'"
                    :class="{ active: sheetMode === 'source' }"
                    :disabled="switchingLocked"
                    @click="openSource"
                  >Markdown 源码<span v-if="sourceDirty" aria-label="未保存"> *</span></button>
                </div>
              </div>

              <div class="sheet-head-row sheet-head-meta">
                <div class="readiness">
                  <span class="readiness-count">
                    <span class="readiness-dot" :class="{ full: readiness.done === readiness.total }" />
                    {{ readiness.done }}/{{ readiness.total }} 就绪
                  </span>
                  <button
                    v-for="item in readiness.items"
                    :key="item.id"
                    type="button"
                    class="ready-chip"
                    :class="{ done: item.done }"
                    :title="item.done ? `${item.label}已就绪` : `${item.label}还没弄，点我去处理`"
                    @click="jumpToReadiness(item.id)"
                  >
                    <span class="chip-mark" aria-hidden="true">{{ item.done ? '✓' : '○' }}</span>
                    {{ item.label }}
                  </button>
                </div>
                <!-- 排版预览把自己的统计/复制按钮挂到这里，不留在正文卡片里 -->
                <div id="paper-preview-bar-slot" class="preview-bar-slot" />
              </div>
            </div>

            <div v-if="job" class="job-bar" role="status">
              <span class="job-dot" />
              <span>{{ job.label }}{{ job.progress ? ` · ${job.progress.done}/${job.progress.total} 段` : '' }}{{ job.stopping ? '（正在停止）' : '' }}</span>
              <NButton v-if="job.kind === 'fixall'" size="tiny" @click="stop">停止</NButton>
            </div>

            <!-- 稿纸导航：目录 / 下一处待处理 / 接着改 / 未保存，参考项目也是把这几件事
                 放在正文最上面，改稿时不用来回滚。 -->
            <div v-if="sheetMode === 'paper'" class="paper-nav">
              <NPopover v-model:show="showOutline" trigger="click" placement="bottom-start" :show-arrow="false">
                <template #trigger>
                  <button class="nav-btn" :aria-expanded="showOutline" @click="showUnsaved = false">正文目录</button>
                </template>
                <nav class="nav-list" aria-label="正文小标题">
                  <button v-for="item in outline" :key="item.block.id" @click="jumpTo(item.block.id)">
                    <b>{{ item.number }}</b><span>{{ plainText(item.block.text) }}</span>
                  </button>
                  <p v-if="!outline.length" class="hint">这篇还没有小标题。</p>
                </nav>
              </NPopover>
              <button v-if="nextWaiting" class="nav-btn" @click="jumpTo(nextWaiting.block.id, true)">
                下一处待处理 <span class="nav-count">· {{ waitingBlocks.length }}</span>
              </button>
              <button v-if="lastBlock" class="nav-btn" :disabled="editingLocked" @click="openEditor(lastBlock.block)">
                接着改第 {{ lastBlock.number }} 段
              </button>
              <!-- 素材段落多选：开着时点正文就是勾选，SVG 卡按这些段合并生成 -->
              <button
                class="nav-btn"
                :class="{ active: sourcePickMode }"
                :aria-pressed="sourcePickMode"
                @click="toggleSourcePick"
              >
                {{ sourcePickMode ? `选素材中 · ${sourceBlockIds.length} 段` : '选素材段落' }}
              </button>
              <template v-if="sourcePickMode">
                <span class="nav-hint">点正文勾选，Shift 连选</span>
                <button v-if="sourceBlockIds.length" class="nav-btn" @click="sourceSelection.clear">清空</button>
                <button class="nav-btn primary" @click="sourceSelection.stop">完成</button>
              </template>
              <span class="nav-grow" />
              <!-- 撤销贴着正文放：它回退的是稿纸上的内容，撤完的提示也出现在正文正上方 -->
              <button
                class="nav-btn"
                :disabled="!article || editingLocked || !!job"
                :title="job ? '后台任务正在改这篇，等它结束再撤销' : '撤销上一篇改稿，可按提示恢复'"
                @click="undo"
              >撤销一步</button>
              <button class="text-button" @click="openMedia('inline')">配插图 →</button>
              <NPopover v-if="unsavedCount" v-model:show="showUnsaved" trigger="click" placement="bottom-end" :show-arrow="false">
                <template #trigger>
                <button
                  class="nav-btn warn"
                  :disabled="!!job"
                  :aria-expanded="showUnsaved"
                  :title="storageFailed ? '本地暂存失败，退出前请保存正文' : '未保存的修改已暂存在本机'"
                  @click="showOutline = false"
                >{{ unsavedCount }} 段未保存</button>
                </template>
                <div class="nav-list" aria-label="未保存段落">
                  <button v-for="item in unsavedList" :key="item.block.id" @click="jumpTo(item.block.id, true)">
                    <b>第 {{ item.number }} 段</b><span>{{ plainText(item.draft.text) || '已清空' }}</span>
                  </button>
                </div>
              </NPopover>
            </div>

            <ol v-if="sheetMode === 'paper'" class="blocks">
              <li
                v-for="(block, index) in article.blocks"
                :id="`paper-block-${block.id}`"
                :key="block.id"
                class="block"
                :class="[block.check?.status || '', {
                  editing: editingBlockId === block.id,
                  selected: selectedBlockId === block.id,
                  picking: sourcePickMode || detectPickMode,
                  picked: visiblePickedIds.includes(block.id),
                }]"
              >
                <div class="block-head">
                  <button
                    class="block-no"
                    :disabled="editingLocked && !sourcePickMode && !detectPickMode"
                    :aria-label="detectPickMode ? `把第 ${index + 1} 段加入或移出检测` : (sourcePickMode ? `把第 ${index + 1} 段加入或移出素材` : `编辑第 ${index + 1} 段`)"
                    :aria-pressed="activeSelection ? visiblePickedIds.includes(block.id) : undefined"
                    @click="onBlockClick(block, $event)"
                  >
                    <span v-if="activeSelection" class="pick-box" aria-hidden="true">{{ visiblePickedIds.includes(block.id) ? '✓' : '' }}</span>
                    {{ index + 1 }}<span v-if="!sourcePickMode && !detectPickMode" class="pencil" aria-hidden="true">✎</span>
                  </button>
                  <span class="block-kind">{{ kindLabel[block.kind] || block.kind }}</span>
                  <span v-if="visiblePickedIds.includes(block.id)" class="badge picked-label">{{ tab === 'quality' ? '待检' : '素材' }}</span>
                  <span v-if="block.check?.status" class="badge" :class="block.check.status">
                    {{ statusLabel[block.check.status] || block.check.status }}
                    <template v-if="block.check.score">· {{ block.check.score }}</template>
                    <template v-if="block.check.fixes">· 改过 {{ block.check.fixes }} 轮</template>
                  </span>
                  <span v-if="block.check?.worse" class="badge danger">比上一版更重</span>
                  <span v-if="dirtyIds.has(block.id)" class="badge unsaved">未保存</span>
                  <span v-if="editingBlockId === block.id" class="block-hint">正在编辑第 {{ index + 1 }} 段</span>
                  <!-- 段头右端的快捷删除：hover 才浮出；选素材/编辑中/任务运行时不显示。
                       不放段号正旁边，是因为段号本身占满了 44px 的左侧留白，紧贴会压住「正文」标签。 -->
                  <button
                    v-if="!sourcePickMode && !detectPickMode && editingBlockId !== block.id"
                    class="block-del"
                    type="button"
                    :disabled="editingLocked || deletingBlockId === block.id"
                    :aria-label="`删除第 ${index + 1} 段`"
                    :title="editingLocked ? '后台任务正在改这篇，等它结束再删' : '删除这一段（可用顶部「撤销一步」恢复）'"
                    @click.stop="deleteBlock(block, index)"
                  >{{ deletingBlockId === block.id ? '…' : '×' }}</button>
                </div>

                <PaperBlockEditor
                  v-if="editingBlockId === block.id"
                  :article-id="article.id"
                  :block="block"
                  :number="index + 1"
                  :next-block="article.blocks[index + 1] || null"
                  :busy="!!job || Boolean(actionBusy)"
                  @saved="onBlockSaved"
                  @done="closeEditor"
                />
                <div
                  v-else
                  class="block-body"
                  :class="{ locked: editingLocked && !activeSelection }"
                  :role="editingLocked && !sourcePickMode && !detectPickMode ? undefined : 'button'"
                  :tabindex="editingLocked && !sourcePickMode && !detectPickMode ? -1 : 0"
                  :title="detectPickMode ? '点一下加入或移出检测；Shift 点连选一段' : (sourcePickMode ? '点一下加入或移出素材；Shift 点连选一段' : (editingLocked ? '后台任务正在改这篇，等它结束再编辑' : '点一下直接改这一段'))"
                  @click="onBlockClick(block, $event)"
                  @keydown.enter.prevent="onBlockClick(block, $event)"
                >
                  <div class="block-html" v-html="renderBody(block.text)" />
                  <span v-if="dirtyIds.has(block.id)" class="block-unsaved">有未保存的修改</span>
                  <ul v-if="block.check?.hits?.length" class="block-reasons">
                    <li v-for="reason in block.check.hits" :key="reason">{{ reason }}</li>
                  </ul>
                </div>

                <PaperAnnotation
                  :block="block"
                  :job="job"
                  :number="index + 1"
                  @fix="fixBlock(block)"
                  @keep="keepBlock(block)"
                  @revert="revertBlock(block)"
                />
              </li>
            </ol>
            <PaperLayoutPreview
              v-else-if="sheetMode === 'layout'"
              :article="article"
              :options="layoutOptions"
              active
            />
            <section v-else class="source-editor" aria-label="Markdown 源码">
              <div class="source-toolbar">
                <span v-if="sourceLoading" role="status">正在加载源码…</span>
                <span v-else-if="unsavedCount" role="status">请先回到段号稿纸保存段落修改。</span>
                <span v-else-if="sourceDirty" role="status">源码未保存 · 预览仍使用已保存版本</span>
                <span v-else-if="sourceOriginal !== null">正文及一级标题可直接编辑</span>
                <div>
                  <NButton v-if="sourceDirty || (sourceError && sourceOriginal === null)" size="small" :disabled="switchingLocked" @click="reloadSource">{{ sourceDirty ? '放弃修改并重新载入' : '重新载入' }}</NButton>
                  <NButton size="small" type="primary" :loading="actionBusy === 'source'" :disabled="editingLocked || unsavedCount > 0 || !sourceDirty" @click="saveSource">保存源码</NButton>
                </div>
              </div>
              <p v-if="sourceError" role="alert" class="source-error">{{ sourceError }}</p>
              <textarea v-if="sourceOriginal !== null" v-model="sourceText" aria-label="编辑 Markdown 源码" :readonly="editingLocked || unsavedCount > 0" spellcheck="false" />
            </section>
            <p v-if="sheetMode === 'paper' && !article.blocks.length" class="empty">
              还没有正文。<button type="button" class="text-button" @click="openPet('create', $event)">打开助手写初稿</button>
            </p>
          </section>
        </template>
        <section v-else class="paper-sheet" :aria-busy="loading">
          <div v-if="loading || listLoading" class="sheet-loading">正在加载稿件…</div>
          <div v-else class="empty">
            <p>{{ listError ? '无法加载稿件列表。' : '尚未打开稿件。' }}</p>
            <NButton size="small" @click="openCreateTools">打开助手新建稿件</NButton>
          </div>
        </section>
      </main>

      <aside class="paper-side">
        <div class="tabs" :inert="assistantDrawer || undefined">
          <button
            v-for="item in [['layout', '排版'], ['media', '素材'], ['quality', '检测']]"
            :key="item[0]"
            :class="{ active: sideTab === item[0] }"
            :aria-pressed="sideTab === item[0]"
            @click="tab = item[0]"
          >{{ item[1] }}</button>
          <button ref="assistantEntry" type="button" class="assistant-side-entry" :class="{ active: assistantOpen }" aria-controls="paper-assistant" :aria-expanded="assistantOpen" @click="openPet(undefined, $event)">创作助手</button>
        </div>

        <section v-if="sideTab === 'quality'" :inert="assistantDrawer || undefined" class="panel quality-panel" aria-label="稿件检测">
          <div class="quality-summary">
            <div class="quality-head">
              <div>
                <strong>稿件检测</strong>
                <span>{{ sourceLabel }}<template v-if="article?.zhuque?.round"> · 第 {{ article.zhuque.round }} 轮</template></span>
              </div>
            </div>

            <div class="quality-controls">
              <div class="qc-row">
                <span class="qc-label">检测范围</span>
                <div class="seg" role="group" aria-label="检测范围">
                  <button :class="{ active: detectScope === 'all' }" @click="setDetectScope('all')">全文</button>
                  <button :class="{ active: detectScope === 'selection' }" @click="setDetectScope('selection')">指定段落</button>
                </div>
              </div>
              <div class="qc-row">
                <span class="qc-label">检测来源</span>
                <div class="seg" role="group" aria-label="检测来源">
                  <button :class="{ active: qualitySource === 'auto' }" @click="setQualitySource('auto')">自动</button>
                  <button
                    :class="{ active: qualitySource === 'zhuque' }"
                    :title="settings.zhuqueConfigured ? '优先用朱雀，失败自动兜底本地规则' : '未配置 Key，将自动兜底本地规则'"
                    @click="setQualitySource('zhuque')"
                  >朱雀 AI</button>
                  <button :class="{ active: qualitySource === 'local' }" @click="setQualitySource('local')">本地规则</button>
                </div>
              </div>
              <PaperParagraphPickControls
                v-if="detectScope === 'selection'"
                :active="detectPickMode"
                :count="detectBlockIds.length"
                :disabled="!article?.blocks?.length"
                @start="startDetectPick"
                @clear="detectSelection.clear"
                @finish="detectSelection.stop"
              />
            </div>

            <NButton
              size="small"
              type="primary"
              :disabled="!article || (detectScope === 'selection' && !detectBlockIds.length)"
              :loading="actionBusy === 'detect'"
              @click="detect"
            >{{ detectScope === 'selection' ? `检测选中 ${detectBlockIds.length} 段` : (article?.zhuque?.round ? '重新检测全文' : '开始检测') }}</NButton>
            <dl v-if="article?.zhuque?.round" class="quality-counts" aria-live="polite">
              <div><dt>待改段落</dt><dd :class="{ 'needs-work': flagged }">{{ flagged }}</dd></div>
              <div><dt>已改段落</dt><dd>{{ fixedCount }}</dd></div>
            </dl>
            <p v-else class="quality-empty">{{ article ? '开始检测后，可查看待改段落和检测结果。' : '选择稿件后即可开始检测。' }}</p>
            <div v-if="flagged || job?.kind === 'fixall'" class="panel-row">
              <NButton size="small" :disabled="!article || !!job || !flagged" :loading="actionBusy === 'fixAll'" @click="fixAll">一键处理 {{ flagged }} 段</NButton>
              <NButton v-if="job?.kind === 'fixall'" size="small" @click="stop">停止</NButton>
            </div>
          </div>
          <p v-if="article?.zhuque?.note" class="hint">{{ article.zhuque.note }}</p>
          <p v-else-if="article?.zhuque?.source === 'local'" class="hint">这是旧版三维结果，再检测一次后显示六维写作质量。</p>
          <figure v-if="qualityDimensions.length >= 6" class="quality-radar">
            <figcaption><strong>本地检测维度</strong><span>0–100 分 · 越高越好</span></figcaption>
            <QualityRadar :dimensions="qualityDimensions" />
            <ul class="radar-legend" aria-label="本地检测各维度分数">
              <li v-for="dimension in qualityDimensions" :key="dimension.id">
                <span class="radar-legend-label">{{ dimension.label }}</span>
                <span class="radar-legend-score">{{ dimension.score }}</span>
              </li>
            </ul>
          </figure>
          <details v-if="!settings.zhuqueConfigured" class="report-box">
            <summary>配置朱雀 API Key（不配则走本地规则）</summary>
            <NInput v-model:value="qualityKey" size="small" type="password" show-password-on="click" placeholder="粘贴朱雀 API Key" />
            <NInput v-model:value="qualityEndpoint" size="small" :placeholder="settings.zhuqueEndpoint || '接口地址（可留空用默认）'" />
            <NButton size="tiny" :loading="actionBusy === 'quality-settings'" @click="saveQuality">保存</NButton>
          </details>
          <p v-else class="hint">朱雀 Key 已配置（{{ settings.zhuqueKeyHint }}）。检测会优先调用朱雀接口。</p>

          <details class="report-box">
            <summary>用朱雀报告回填（PDF 或文字）</summary>
            <input type="file" accept="application/pdf" @change="pickReport" />
            <NInput v-model:value="reportText" type="textarea" :rows="4" placeholder="或者把报告文字粘贴在这里" />
            <NButton size="tiny" :disabled="!article || !!job" :loading="actionBusy === 'report'" @click="submitReport">回填</NButton>
          </details>
        </section>

        <section v-else-if="sideTab === 'layout'" :inert="assistantDrawer || undefined" class="panel">
          <PaperLayoutPanel
            :article="article"
            :options="layoutOptions"
            :busy="actionBusy"
            @change="onLayoutChange"
            @saved="applyPanelSnapshot"
            @notify="notifyPanel"
          />
        </section>

        <section v-else-if="sideTab === 'media'" :inert="assistantDrawer || undefined" class="panel">
          <PaperMediaPanel
            v-model:section="mediaSection"
            :article="article"
            :selected-block-id="selectedBlockId"
            :source-block-ids="svgSourceIds"
            :source-pick-mode="sourcePickMode"
            :busy="actionBusy"
            @saved="applyPanelSnapshot"
            @notify="notifyPanel"
            @pick-in-paper="startSourcePick"
            @clear-pick="sourceSelection.clear"
            @finish-pick="sourceSelection.stop"
            @select-block="onSelectBlock"
          />
        </section>

        <div v-if="assistantDrawer" class="assistant-backdrop" aria-hidden="true" @click="closeAssistant" />
        <section
          v-show="assistantOpen"
          id="paper-assistant"
          ref="assistantPanel"
          class="panel assistant-panel"
          :role="assistantDrawer ? 'dialog' : undefined"
          :aria-modal="assistantDrawer || undefined"
          aria-label="创作助手"
          @keydown="onAssistantKeydown"
        >
          <div class="assistant-toolbar">
            <strong>{{ showAssistantTools ? '写作工具' : '创作助手' }}</strong>
            <button type="button" class="assistant-tool-toggle" :aria-expanded="showAssistantTools" aria-controls="assistant-tools" @click="showAssistantTools = !showAssistantTools">{{ showAssistantTools ? '← 返回对话' : '写作工具' }}</button>
            <button ref="assistantClose" type="button" class="assistant-close" aria-label="关闭创作助手" title="关闭创作助手" @click="closeAssistant">×</button>
          </div>
          <div v-if="assistantMounted" v-show="!loading && !showAssistantTools" class="assistant-conversation">
            <PetChatPanel
              :key="article?.id || 'new'"
              :studio-article-id="article?.id"
              v-model:model-id="modelSelection.selectedModelId"
              :model-options="modelSelection.options"
              :model-error="modelSelection.error"
              @reload-models="modelSelection.load(true)"
              :article-title="article?.title"
              mode="unified"
              :apply-busy="assistantApplyBusy"
              :apply-markdown="article?.id ? applyAssistantMarkdown : undefined"
              :hotspot="assistantHotspot"
              :chat-turns="article?.chat || []"
              :chat-busy="editingLocked"
              :selected-block="chatAnchor"
              :initial-prompt="assistantInitialPrompt?.articleId === article?.id ? assistantInitialPrompt.text : ''"
              :send-edit-request="sendPetEdit"
              :on-accept-proposal="id => resolveChatProposal(id, true)"
              :on-discard-proposal="id => resolveChatProposal(id, false)"
              @ensure-article="ensureAssistantArticle"
              @created="onPetCreated"
              @clear-selection="selectedBlockId = ''"
            />
          </div>
          <div id="assistant-tools" v-show="showAssistantTools" class="pet-tools">
            <div v-if="article && !showCreate" class="tool-new-draft">
              <div><strong>开始新稿</strong><p class="hint">新建或导入另一篇文章，不影响当前对话。</p></div>
              <NButton size="small" :disabled="editingLocked" @click="openCreateTools">新建稿件</NButton>
            </div>
            <div class="assistant-tool-section">
              <label class="field">
                <span>写作技能（最多 5 个）</span>
                <NSelect v-model:value="selectedSkillIds" multiple clearable size="small" :options="writingSkillOptions" :loading="skillsLoading" :disabled="editingLocked || skillsLoading" placeholder="不选择则使用默认写作规则" />
              </label>
              <p class="hint">仅用于本次新建生成或整篇重写；导入 Markdown 和对话改稿不使用这里的选择。</p>
              <p v-if="skillsError" role="alert" class="pet-error">{{ skillsError }} <button type="button" @click="loadWritingSkills">重试</button></p>
              <p v-else-if="!skillsLoading && !writingSkillOptions.length" class="hint">暂无可用写作技能，可前往技能中心创建。</p>
            </div>
            <form v-if="showCreate || !article" class="create-form" @submit.prevent="createArticle">
              <div v-if="createError" role="alert" class="pet-error">{{ createError }}</div>
              <div class="tool-section-heading">
                <strong>新建稿件</strong>
                <NButton v-if="article" size="tiny" :disabled="actionBusy === 'create'" @click="showCreate = false">返回</NButton>
              </div>
              <label class="field"><span>选题</span><NInput v-model:value="createForm.topic" size="small" placeholder="可留空，由模型提炼" /></label>
              <label class="field"><span>创作素材</span><NInput v-model:value="createForm.material" size="small" type="textarea" :rows="2" placeholder="事实、数字或经历" /></label>
              <label class="field"><span>导入 Markdown</span><NInput v-model:value="createForm.markdown" size="small" type="textarea" :rows="3" placeholder="粘贴已有正文" /></label>
              <label class="field">
                <span>写作风格</span>
                <NSelect v-model:value="createForm.writerStyle" size="small" :options="styleOptions" :disabled="!styleOptions.length || switchingLocked" />
              </label>
              <p v-if="createStyleHint" class="hint">{{ createStyleHint }}</p>
              <div class="create-actions">
                <NButton size="small" attr-type="submit" type="primary" :disabled="loading || switchingLocked" :loading="actionBusy === 'create'">
                  {{ createForm.markdown.trim() ? '导入稿件' : '让模型写正文' }}
                </NButton>
              </div>
            </form>
            <template v-else>
              <section class="assistant-tool-section">
                <div class="tool-section-heading"><strong>正文创作</strong><span class="tool-section-tag">整篇操作</span></div>
                <p class="hint">只想调整某一段？返回对话，选中段落后提出修改要求。</p>
                <label class="field">
                  <span>写作风格</span>
                  <NSelect :value="writerStyle" size="small" :options="styleOptions" :disabled="editingLocked || !styleOptions.length" @update:value="changeWriterStyle" />
                </label>
                <p v-if="writerStyleHint" class="hint">{{ writerStyleHint }}</p>
                <div class="create-actions">
                  <NButton size="small" :disabled="editingLocked || !styleOptions.length" :loading="actionBusy === 'draft'" @click="draftArticle">
                    {{ article.blocks?.length ? '按这个风格重写' : '让模型写正文' }}
                  </NButton>
                </div>
              </section>
              <section class="assistant-tool-section">
                <div class="tool-section-heading">
                  <strong>候选标题</strong>
                  <NButton size="small" :disabled="editingLocked || !article.blocks?.length" :loading="actionBusy === 'titles'" @click="regenTitles">
                    {{ article.titles?.length ? '重新出 5 个' : '生成候选标题' }}
                  </NButton>
                </div>
                <ul v-if="article.titles?.length" class="title-list">
                  <li v-for="(item, index) in article.titles" :key="index" :class="{ active: item.text === article.title }">
                    <button type="button" class="title-pick" :aria-current="item.text === article.title ? 'true' : undefined" :disabled="editingLocked || item.text === article.title" @click="pickTitle(item.text)">
                      <span>{{ item.text }}</span><em v-if="item.text === article.title">当前</em><em v-else-if="item.recommended">推荐</em>
                    </button>
                    <p v-if="item.hook || item.reason" class="hint">{{ [item.hook, item.reason].filter(Boolean).join(' · ') }}</p>
                  </li>
                </ul>
                <p v-else class="hint">{{ article.blocks?.length ? '还没有候选标题。' : '有正文后可以生成候选标题。' }}</p>
              </section>
            </template>
          </div>
        </section>
      </aside>
      <NModal v-model:show="showPublish" preset="card" title="创建草稿推送任务" class="publish-modal" :style="{ width: 'min(520px, calc(100vw - 32px))' }">
        <PaperPublishPanel
          :article="article"
          :busy="actionBusy"
          :before-submit="() => !editingLocked && canUseSavedDraft()"
          @saved="applyPanelSnapshot"
          @notify="notifyPanel"
        />
      </NModal>
    </div>
  </div>
</template>

<style scoped>
.assistant-mobile-entry { display: none; }
.assistant-toolbar, .tool-section-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.assistant-toolbar { flex: none; padding: 8px 12px; border-bottom: 1px solid var(--color-border); }
.assistant-toolbar strong { margin-right: auto; font-size: 13px; }
.assistant-tool-toggle, .assistant-close { padding: 5px 8px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; font-size: 12px; }
.assistant-tool-toggle:hover, .assistant-close:hover { background: var(--color-surface-muted); color: var(--color-text-primary); }
.assistant-close { font-size: 18px; line-height: 1; }
.pet-tools { display: flex; flex-direction: column; align-items: stretch; flex: 1; min-height: 0; min-width: 0; gap: 14px; padding: 14px; overflow: auto; overscroll-behavior: contain; }
.tool-new-draft { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding-bottom: 2px; font-size: 13px; }
.tool-new-draft > div { flex: 1; min-width: 140px; }
.pet-tools .hint { margin: 0; line-height: 1.65; }
.tool-new-draft .hint { margin-top: 4px; }
.assistant-tool-section, .pet-tools > .create-form { border: 1px solid var(--color-border); border-radius: 10px; padding: 14px; background: var(--color-surface); font-size: 13px; }
.tool-section-tag { font-size: 11px; font-weight: 400; color: var(--color-text-secondary); }
.pet-tools .create-actions { margin-top: 6px; }
.assistant-tool-toggle:focus-visible, .assistant-close:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 2px; }
.assistant-conversation { display: flex; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }

.paper-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 24px 16px;
  /* 铺满主滚动区的可视高度：稿纸、排版预览、右栏都在自己内部滚动，
     不再用 min-height:520px 把预览卡成一个矮框、又把整页撑出第二根滚动条 */
  height: 100%;
  min-height: 0;
  overflow: hidden;
  color: var(--color-text-primary);
}
.paper-head { position: relative; z-index: 10; display: flex; align-items: center; gap: 10px 16px; flex-wrap: wrap; }
.head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* 右边的动作靠右；左边是「换稿」，两组动作之间不再夹别的东西 */
.head-actions-end { margin-left: auto; }
.publish-modal :deep(.publish-panel) { border: none; padding: 0; background: transparent; }

.paper-alert {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border-radius: 10px;
  background: var(--paper-accent-tint); border: 1px solid var(--paper-accent-soft);
  font-size: 13px;
}
.paper-alert.error { background: var(--paper-danger-soft); border-color: var(--paper-danger-line); color: var(--paper-danger); }

.paper-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 380px);
  gap: 16px;
  flex: 1;
  min-height: 0;
  align-items: stretch;
}

.paper-dialog { width: min(560px, calc(100vw - 32px)); max-height: 85vh; overflow: auto; }
.article-picker-head { display: grid; gap: 12px; margin-bottom: 12px; }
.article-filter { display: flex; flex-wrap: wrap; gap: 8px; }
.paper-list { display: flex; flex-direction: column; gap: 6px; max-height: 55vh; min-width: 0; overflow: auto; }
.picker-empty { display: grid; justify-items: start; gap: 8px; padding-block: 8px; }
.picker-empty p { margin: 0; overflow-wrap: anywhere; }
.list-item {
  display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; text-align: left;
  border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-surface);
  cursor: pointer; color: inherit; min-width: 0;
}
.list-item.active { border-color: var(--paper-accent); background: var(--paper-accent-tint); }
.list-item:not(:disabled):hover { border-color: var(--paper-accent); }
.list-item:focus-visible, .pet-close:focus-visible, .title-pick:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: -2px; }
.list-item:disabled { opacity: .7; cursor: not-allowed; }
.list-title { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.list-meta { font-size: 11px; color: var(--color-text-secondary); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.list-meta .current { color: var(--paper-accent); }

.create-form, .assistant-tool-section { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.create-actions { display: flex; justify-content: flex-end; }

/* 中栏：工具条和正文是两张独立卡片，工具条固定在标题上方 */
.paper-column {
  --sheet-pad-x: 26px;
  --sheet-pad-y: 18px;
  /* 正文单行宽度：稿纸再宽，文字也只排到这里，右侧留给批注。
     定死而不是用 1fr —— 否则有批注的段落窄、没批注的段落铺满整张稿纸，
     同一篇文章的阅读宽度来回跳。 */
  --paper-text-measure: 720px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
}
.paper-sheet {
  background: var(--paper-bg);
  border: 1px solid var(--paper-line);
  border-radius: 14px;
  padding: 0 0 12px;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: var(--shadow-md);
}
.paper-sheet.with-preview { padding: 0; }
.sheet-head {
  padding: var(--sheet-pad-y) var(--sheet-pad-x) 12px;
  border-bottom: 1px solid var(--paper-line);
  margin-bottom: 10px;
}
/* 标题行：标题占满，切换按钮贴右 */
.sheet-head-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; min-width: 0; }
.sheet-head-row + .sheet-head-row { margin-top: 8px; }
.sheet-title { flex: 1 1 auto; min-width: 0; }
.sheet-head-meta { flex-wrap: wrap; justify-content: space-between; }

/* 排版预览铺满稿纸剩下的高度，去掉自己那圈边框，避免卡片套卡片还只露出半截 */
.paper-sheet > :deep(.layout-preview) {
  flex: 1 1 auto;
  min-height: 0;
  border: none;
  border-radius: 0;
  background: transparent;
}
.preview-bar-slot:empty { display: none; }
.preview-bar-slot { min-width: 0; }
.preview-bar-slot :deep(.preview-bar) {
  padding: 0;
  gap: 6px 10px;
  border: 0;
  background: transparent;
}
/* 无工具条的状态（加载中/未选稿件）自己补上顶部留白 */
.paper-sheet > .sheet-loading,
.paper-sheet > .empty { padding: var(--sheet-pad-y) var(--sheet-pad-x); }

/* 稿纸 / 排版预览：分段控制器（一整块底 + 选中块），比两个独立胶囊更像一个开关 */
.source-editor { display: flex; flex-direction: column; gap: 10px; padding: 16px; flex: 1; min-height: 0; }
.source-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 12px; }
.source-toolbar > div { display: flex; gap: 8px; margin-inline-start: auto; }
.source-error { color: var(--color-danger); margin: 0; }
.source-editor textarea {
  flex: 1; min-height: 260px; width: 100%; box-sizing: border-box; resize: vertical;
  background: transparent; color: inherit; padding: 12px; border: 1px solid var(--border-color, #777); border-radius: 6px;
  font: 13px/1.75 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.source-editor textarea:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 1px; }
.view-switch {
  display: inline-flex;
  flex: none;
  padding: 3px;
  border-radius: 10px;
  background: var(--color-surface-muted);
  border: 1px solid var(--paper-line);
}
.view-switch button {
  padding: 5px 16px;
  font-size: 12px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
  transition: background .15s ease, color .15s ease;
}
.view-switch button:hover:not(.active) { color: var(--paper-accent); }
.view-switch button.active {
  background: var(--color-surface);
  color: var(--paper-accent);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}
/* 就绪度：进度 + 一排可点的小胶囊。
   没就绪的那几项要一眼看出「还欠着」，点一下直接把人送到该干活的地方。 */
.readiness { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 11px; }
.readiness-count {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--paper-accent-tint);
  color: var(--paper-ink-soft);
  font-variant-numeric: tabular-nums;
}
.readiness-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--paper-line-strong);
}
.readiness-dot.full { background: var(--paper-success); }
.ready-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  font-size: 11px;
  line-height: 18px;
  border-radius: 999px;
  border: 1px dashed var(--paper-line-strong);
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition: border-color .15s ease, color .15s ease, background .15s ease;
}
.ready-chip:hover { border-color: var(--paper-accent); color: var(--paper-accent); background: var(--paper-accent-tint); }
.ready-chip.done {
  border-style: solid;
  border-color: var(--paper-success);
  background: var(--paper-success-soft);
  color: var(--paper-success);
}
.ready-chip.done:hover { background: var(--paper-success-soft); }
.chip-mark { font-size: 10px; line-height: 1; }
.ready-chip:not(.done) .chip-mark { opacity: .55; }

.job-bar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; margin: 0 var(--sheet-pad-x) 12px; border-radius: 8px; background: var(--paper-accent-tint); font-size: 12px; }
.job-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--paper-accent); animation: pulse 1.2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }

.blocks {
  list-style: none; margin: 0; padding: 0 var(--sheet-pad-x);
  display: flex; flex-direction: column; gap: 2px;
  flex: 1 1 auto; min-height: 0; overflow: auto;
  container-type: inline-size;
}
.block { position: relative; padding: 10px 12px 10px 44px; border-radius: 8px; border: 1px solid transparent; min-width: 0; }
@container (min-width: 680px) {
  /* 正文列按固定宽度排，多出来的空间留在右侧：批注因此始终紧贴正文，不再被推到稿纸边缘 */
  .block:has(> .ann) { display: grid; grid-template-columns: minmax(0, var(--paper-text-measure)) minmax(170px, 220px); column-gap: 24px; }
  .block:has(> .ann) > .block-head { grid-column: 1; grid-row: 1; }
  .block:has(> .ann) > .block-body, .block:has(> .ann) > :deep(.paper-block-editor) { grid-column: 1; grid-row: 2; }
  .block > :deep(.ann) { grid-column: 2; grid-row: 1 / span 2; }
  .block > :deep(.ann)::before { content: ''; position: absolute; width: 20px; height: 1px; right: 100%; top: 12px; background: currentColor; opacity: .5; }
}
.block:hover { background: var(--paper-hover); }
.block.flag { background: var(--paper-danger-soft); border-color: var(--paper-danger-line); }
.block.fixed { background: var(--paper-accent-tint); border-color: var(--paper-accent-soft); }
.block.editing { background: var(--color-surface); border-color: var(--paper-accent); }
.block.selected { background: var(--paper-accent-tint); border-color: var(--paper-accent-soft); }
/* 编辑态不再重复「选中」的底色：描边已经说明是哪一段，双层底色反而糊成一片 */
.block.editing.selected { background: var(--paper-bg); }
/* 编辑态和阅读态守同一条宽度，切换时不该重新折行 */
.block :deep(.paper-block-editor) { min-width: 0; max-width: var(--paper-text-measure); background: var(--paper-bg); }
.block :deep(.editor-input), .block :deep(.editor-preview) { color: var(--paper-ink); }
.block-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 11px; color: var(--color-text-tertiary); }
/* 段号兼作编辑入口：鼠标移上去才露铅笔，平时保持稿纸的干净 */
.block-no {
  position: absolute; left: 8px; top: 7px;
  display: inline-flex; align-items: center; gap: 2px;
  padding: 2px 5px; border: none; border-radius: 5px; background: none;
  font-family: inherit; font-size: 11px; font-variant-numeric: tabular-nums; font-weight: 600;
  color: var(--paper-ink-soft); cursor: pointer;
}
.block-no:hover { background: var(--paper-accent-tint); color: var(--paper-accent); }
.block-no:disabled { cursor: default; }
.block-no .pencil { font-size: 10px; color: var(--paper-accent); opacity: 0; transition: opacity .15s; }
.block:hover .block-no .pencil { opacity: 1; }
.block-kind { letter-spacing: .04em; }
.block-hint { margin-left: auto; color: var(--paper-accent); }
/* 快捷删除：平时不出现，鼠标进到这一段才浮到段头最右，避免满屏红叉 */
.block-del {
  margin-left: auto;
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; padding: 0;
  border: 1px solid transparent; border-radius: 5px;
  background: none; color: var(--paper-danger);
  font-family: inherit; font-size: 13px; line-height: 1;
  cursor: pointer; opacity: 0; transition: opacity .12s;
}
.block:hover .block-del, .block-del:focus-visible { opacity: 1; }
.block-del:hover:not(:disabled) {
  background: var(--paper-danger-soft);
  border-color: var(--paper-danger-line);
}
.block-del:disabled { opacity: 0; cursor: default; }
/* 触屏没有 hover，常驻半透明，免得根本找不到删除入口 */
@media (hover: none) { .block-del { opacity: .6; } .block-del:disabled { opacity: 0; } }

/* 稿纸勾选：检测和素材统一用绿色显示已选段落。 */
.block.picking { cursor: pointer; }
.block.picking:hover { background: var(--paper-accent-tint); }
.block.picking .block-body { cursor: pointer; }
.block.picked { background: var(--paper-success-soft); border-color: var(--paper-success); }
.block.picked .block-no .pick-box { background: var(--paper-success); border-color: var(--paper-success); color: var(--paper-on-accent); }
.badge.picked-label { border-color: var(--paper-success); color: var(--paper-success); }
.block-no .pick-box {
  display: inline-flex; align-items: center; justify-content: center;
  width: 12px; height: 12px; margin-right: 2px;
  border: 1px solid var(--paper-line-strong); border-radius: 3px;
  font-size: 9px; line-height: 1;
}

/* 正文：整块可点，点哪儿都进编辑；内部链接不响应点击，否则一改稿就跳走 */
.block-body { position: relative; padding: 2px 6px; margin: 0 -6px; border-radius: 6px; outline: none; cursor: text; }
.block-body:focus-visible { box-shadow: 0 0 0 2px var(--paper-accent-soft); }
/* 任务期锁住正文：不给「可点」的光标，避免点了没反应 */
.block-body.locked { cursor: not-allowed; opacity: .72; }
.block-body :deep(a) { pointer-events: none; }
/* 正文按固定宽度换行：没有批注的段落也守同一条右边界 */
.block-html { max-width: var(--paper-text-measure); font-size: 15px; line-height: 1.85; color: var(--paper-ink); word-break: break-word; }
.block-html :deep(p) { margin: 0 0 6px; }
.block-html :deep(p:last-child) { margin-bottom: 0; }
.block-html :deep(h1), .block-html :deep(h2), .block-html :deep(h3) { margin: 4px 0; font-size: 16px; font-weight: 650; line-height: 1.5; }
.block-html :deep(blockquote) { margin: 0; padding-left: 10px; border-left: 3px solid var(--paper-line-strong); color: var(--color-text-secondary); }
.block-html :deep(ul), .block-html :deep(ol) { margin: 4px 0; padding-left: 20px; }
.block-html :deep(img) { max-width: 100%; border-radius: 6px; }
.block-html :deep(code) { padding: 1px 4px; border-radius: 4px; background: var(--paper-accent-tint); font-size: 13px; }
.block-html :deep(hr) { margin: 8px 0; border: none; border-top: 1px solid var(--paper-line); }
/* 角标挂在正文块右上角：一眼看出这段改过但还没写进正文 */
.block-unsaved {
  position: absolute; right: 2px; top: -9px;
  padding: 0 6px; border-radius: 999px;
  font-size: 10px; line-height: 16px; color: var(--color-on-accent); background: var(--color-warning);
}

/* 稿纸导航条：目录、下一处待处理、接着改、未保存 */
.paper-nav {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 0 var(--sheet-pad-x) 10px;
}
.nav-grow { flex: 1 1 auto; }
.nav-pop { position: relative; }
.nav-btn {
  padding: 3px 9px; font-size: 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--paper-line-strong); background: transparent;
  color: var(--color-text-secondary);
}
.nav-btn:hover { border-color: var(--paper-accent); color: var(--paper-accent); }
.nav-btn:disabled { opacity: .5; cursor: not-allowed; }
.nav-btn.warn { border-color: var(--color-warning); color: var(--color-warning); }
.nav-btn.active { border-color: var(--paper-accent); color: var(--paper-accent); background: var(--paper-accent-tint); }
.nav-btn.primary { border-color: var(--paper-accent); background: var(--paper-accent); color: var(--paper-on-accent); }
.nav-btn.primary:hover { color: var(--paper-on-accent); opacity: .9; }
.nav-hint { font-size: 11px; color: var(--color-text-tertiary); }
.nav-count { color: var(--color-text-tertiary); }
.nav-list {
  width: min(320px, calc(100vw - 64px)); max-height: min(320px, 50vh); overflow: auto;
  display: flex; flex-direction: column; gap: 2px; padding: 6px;
  border: 1px solid var(--paper-line); border-radius: 10px;
  background: var(--color-surface); box-shadow: var(--shadow-lg);
}
/* 未保存那个菜单靠右对齐，否则会顶到稿纸外面 */
.paper-nav > .nav-pop:last-child .nav-list { left: auto; right: 0; }
.nav-list button {
  display: flex; gap: 8px; width: 100%; padding: 6px 8px; text-align: left;
  border: none; border-radius: 6px; background: none; font-size: 12px; color: inherit; cursor: pointer;
}
.nav-list button:hover { background: var(--paper-accent-tint); }
.nav-list b { flex: none; color: var(--paper-ink-soft); font-variant-numeric: tabular-nums; }
.nav-list span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.paper-side { display: flex; flex-direction: column; gap: 10px; min-height: 0; overflow: hidden; }
.tabs { display: grid; grid-template-columns: repeat(3, 1fr) minmax(68px, 1.3fr); gap: 4px; padding: 4px; border-radius: 10px; background: var(--color-surface-muted); }
.tabs button { padding: 6px 4px; font-size: 12px; border: none; border-radius: 7px; background: transparent; cursor: pointer; color: var(--color-text-secondary); }
.tabs button.active { background: var(--color-surface); color: var(--color-text-primary); box-shadow: var(--shadow-sm); }
.panel {
  display: flex; flex-direction: column; gap: 10px; padding: 14px;
  border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface);
  flex: 1; min-height: 0; overflow: auto;
}

.panel.assistant-panel { padding: 0; gap: 0; min-width: 0; overflow: hidden; }
.assistant-backdrop { display: none; }

/* 窄屏改回整页滚动，只有创作助手使用同一实例的抽屉布局。 */
@media (max-width: 1180px) {
  .paper-view { height: auto; overflow: visible; }
  .paper-grid { grid-template-columns: 1fr; flex: none; }
  .paper-list, .paper-side, .panel { overflow: visible; height: auto; flex: none; }
  .paper-column { height: auto; }
  .paper-sheet { flex: none; height: auto; min-height: 70vh; overflow: visible; }
  .blocks { overflow: visible; }
  .paper-sheet > :deep(.layout-preview) { min-height: 70vh; }
  .paper-sheet > :deep(.preview-frame) { flex: none; height: 70vh; }
  .assistant-mobile-entry { display: inline-flex; }
  .tabs { grid-template-columns: repeat(3, 1fr); }
  .assistant-side-entry { display: none; }
  .assistant-backdrop { display: block; position: fixed; inset: 0; z-index: 59; background: var(--color-overlay); }
  .panel.assistant-panel {
    position: fixed; inset: 0 0 0 auto; z-index: 60;
    width: min(100%, 460px); height: 100dvh; box-sizing: border-box;
    border-radius: 12px 0 0 12px; box-shadow: var(--shadow-lg);
  }
}
.panel-actions, .panel-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }

/* 对话改稿：气泡分左右，指令做成胶囊，选中段时把「只改第 N 段」摆明白 */
.chat-panel { gap: 8px; }
.chat-job {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 8px;
  background: var(--paper-accent-tint);
  font-size: 12px; color: var(--paper-ink-soft);
}
.chat-log { display: flex; flex-direction: column; gap: 8px; max-height: 340px; overflow: auto; }
.chat-turn {
  align-self: flex-start;
  max-width: 92%;
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
  background: var(--color-surface-muted);
}
/* 作者的话靠右：一列对话里要能一眼分清谁说的 */
.chat-turn.user { align-self: flex-end; background: var(--paper-accent-tint); }
.chat-text { margin: 0; white-space: pre-wrap; }
/* 回执是「实际动了哪一段」，用对勾列出来，比一段 prose 更好扫 */
.receipts {
  display: flex; flex-direction: column; gap: 2px;
  margin: 6px 0 0; padding: 0; list-style: none;
  font-size: 11px; color: var(--color-text-tertiary);
}
.receipts li { position: relative; padding-left: 14px; }
.receipts li::before { content: '✓'; position: absolute; left: 0; color: var(--paper-success); }

.chat-presets { display: flex; flex-wrap: wrap; gap: 6px; }
.preset-chip {
  padding: 3px 9px; font-family: inherit; font-size: 12px; border-radius: 999px;
  border: 1px solid var(--paper-line-strong); background: transparent;
  color: var(--color-text-secondary); cursor: pointer;
}
.preset-chip:hover:not(:disabled) { border-color: var(--paper-accent); color: var(--paper-accent); }
.preset-chip:disabled { opacity: .5; cursor: not-allowed; }

.chat-anchor {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 8px; border-radius: 8px;
  background: var(--paper-accent-tint);
  font-size: 12px; color: var(--paper-ink-soft);
}
.chat-anchor-text { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat-anchor-off {
  padding: 0 2px; border: none; background: none;
  color: var(--paper-ink-soft); font-size: 14px; line-height: 1; cursor: pointer;
}
.chat-anchor-off:hover { color: var(--paper-danger); }

.quality-summary { display: grid; gap: 12px; padding: 12px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); }
.quality-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }
.quality-head strong { font-size: 15px; }
.quality-counts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; padding-top: 10px; border-top: 1px solid var(--color-border); }
.quality-counts div + div { padding-inline-start: 12px; border-inline-start: 1px solid var(--color-border); }
.quality-counts dt, .quality-empty { margin: 0; color: var(--color-text-secondary); font-size: 12px; }
.quality-counts dd { margin: 4px 0 0; font-size: 24px; font-weight: 600; line-height: 1.15; font-variant-numeric: tabular-nums; }
.quality-counts .needs-work { color: var(--paper-danger); }
.quality-controls { display: grid; gap: 8px; }
.qc-row { display: flex; align-items: center; gap: 8px; }
.qc-label { flex: none; width: 56px; font-size: 12px; color: var(--color-text-secondary); }
.seg { display: inline-flex; flex: 1; min-width: 0; border: 1px solid var(--color-border); border-radius: 7px; overflow: hidden; background: var(--color-surface); }
.seg button { flex: 1; min-width: 0; border: none; background: none; padding: 5px 6px; font-size: 12px; color: var(--color-text-secondary); cursor: pointer; white-space: nowrap; }
.seg button + button { border-inline-start: 1px solid var(--color-border); }
.seg button.active { background: var(--paper-accent-tint); color: var(--paper-accent); font-weight: 600; }
.seg button:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: -2px; }
.quality-panel .report-box { padding: 8px 10px; border-radius: 8px; border: 1px solid var(--color-border); }
.quality-panel summary:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 2px; }
.quality-panel .report-box input { max-width: 100%; }
.quality-radar { margin: 0; padding-block: 10px; border-block: 1px solid var(--color-border); }
.quality-radar figcaption { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 8px; font-size: 12px; margin-bottom: 2px; }
.quality-radar figcaption span { color: var(--color-text-secondary); }
.radar-legend { list-style: none; margin: 6px 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(82px, 1fr)); gap: 6px; }
.radar-legend li { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-surface); }
.radar-legend-label { font-size: 11px; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.radar-legend-score { font-size: 16px; font-weight: 600; color: var(--paper-accent); font-variant-numeric: tabular-nums; line-height: 1.2; }
.quality-head div { display: flex; flex-direction: column; gap: 2px; }
.quality-head span { color: var(--color-text-tertiary); }

.report-box { display: flex; flex-direction: column; gap: 8px; font-size: 12px; }
.report-box summary { cursor: pointer; color: var(--color-text-secondary); }
.title-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.title-list li { padding: 8px 10px; border: 1px solid var(--color-border); border-radius: 8px; }
.title-list li.active { border-color: var(--paper-accent); background: var(--paper-accent-tint); }
.title-pick { display: flex; justify-content: space-between; gap: 8px; width: 100%; padding: 2px 0; border: none; background: none; font-size: 13px; text-align: left; cursor: pointer; color: inherit; }
.title-pick span, .title-list p { overflow-wrap: anywhere; min-width: 0; }
.title-pick:disabled { cursor: default; }
.title-pick em { flex: none; font-style: normal; color: var(--paper-accent); }
.pet-error { color: var(--paper-danger); font-size: 12px; overflow-wrap: anywhere; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }

.badge { padding: 1px 7px; border-radius: 999px; font-size: 10px; border: 1px solid currentColor; }
.badge.flag { color: var(--paper-danger); }
.badge.fixed, .badge.danger { color: var(--color-warning); }
.badge.pass { color: var(--paper-success); }
.badge.kept { color: var(--color-text-tertiary); }
.badge.unsaved { color: var(--color-warning); }
.block-reasons { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px; }
.block-reasons li { padding: 2px 8px; border-radius: 999px; font-size: 11px; color: var(--paper-ink-soft); background: var(--paper-warning-soft); border: 1px solid var(--paper-warning-line); }
.hint { margin: 0; font-size: 11px; color: var(--color-text-tertiary); line-height: 1.6; }
.empty { font-size: 12px; color: var(--color-text-tertiary); }
.sheet-loading { font-size: 13px; color: var(--color-text-tertiary); }
.text-button { border: none; background: none; padding: 0; font-size: 12px; color: var(--paper-accent); cursor: pointer; }
.text-button:disabled { color: var(--color-text-tertiary); cursor: not-allowed; }
</style>
