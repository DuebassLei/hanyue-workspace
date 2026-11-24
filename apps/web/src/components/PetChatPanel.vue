<script setup>
import { computed, onBeforeUnmount, shallowRef, useTemplateRef, watch } from 'vue'
import { api } from '../api/client'
import { studioApi } from '../api/studio'

const props = defineProps({
  studioArticleId: { type: String, default: '' },
  articleTitle: { type: String, default: '' },
  initialPrompt: { type: String, default: '' },
  mode: { type: String, default: 'create', validator: (value) => ['create', 'edit', 'unified'].includes(value) },
  applyBusy: { type: Boolean, default: false },
  applyMarkdown: Function,
  hotspot: { type: Object, default: null },
  chatTurns: { type: Array, default: () => [] },
  chatBusy: { type: Boolean, default: false },
  sendEditRequest: Function,
  onAcceptProposal: Function,
  onDiscardProposal: Function,
  selectedBlock: { type: Object, default: null },
  modelId: { type: String, default: '' },
  modelOptions: { type: Array, default: () => [{ value: '', label: '自动' }] },
  modelError: { type: String, default: '' },
})
const emit = defineEmits(['chat', 'clearSelection', 'created', 'ensureArticle', 'update:modelId', 'reloadModels'])

const presets = [
  { label: '去 AI 味', text: '把 AI 味重的句子改成本人平时说话的样子，意思和信息都不变。' },
  { label: '开头换场景', text: '开头太像总结，换成一个具体的场景或动作。' },
  { label: '压短', text: '太长了，压到三句以内，关键信息保留。' },
  { label: '补例子', text: '补一个具体的例子或数字，别只讲道理。' },
  { label: '收结尾', text: '结尾别喊口号，用一句具体的话收住。' },
]
const typeLabels = { writing: '写作风格', persona: '人物思维', knowledge: '领域知识' }
const actionLabels = {
  create_article: '初稿',
  refine_paragraph: '改稿',
  generate_svg_card: '动态卡',
  select_skill: '技能',
  query_help: '说明',
  query_article: '稿件问答',
}

const inputMessage = shallowRef('')
const editTarget = shallowRef(false)
watch([() => props.selectedBlock?.id, () => props.selectedBlock?.number], () => { editTarget.value = false })
const historyLoading = shallowRef(false)
const skillsLoading = shallowRef(false)
const historyError = shallowRef('')
const skillsError = shallowRef('')
const editPending = shallowRef(false)
const proposalPending = shallowRef('')
const editBusy = computed(() => props.chatBusy || editPending.value)
const loading = shallowRef(false)
const conversationId = shallowRef('')
const messages = shallowRef([])
const activeSkillIds = shallowRef([])
const availableSkills = shallowRef([])
const lastGeneration = shallowRef(null)
const feedback = shallowRef('')
const draftAction = shallowRef('')
const results = useTemplateRef('results')
const draftBusy = computed(() => props.applyBusy || Boolean(draftAction.value))
const hotspotPrompt = computed(() => props.hotspot
  ? `根据「${props.hotspot.title}」和已带入的热点素材，按所选技能写一篇初稿。`
  : '')

// 每次进入稿件都是新会话视图，包括 A → B → A；迟到结果不得更新界面或触发跳转。
let session = 0
onBeforeUnmount(() => { session++ })

function readConversation(conversation) {
  conversationId.value = conversation?.id || ''
  messages.value = conversation?.messages || []
  activeSkillIds.value = (conversation?.activeSkillIds || []).slice(0, 5)
  lastGeneration.value = conversation?.lastGeneration || null
}

async function loadHistory(articleId, current = session) {
  historyLoading.value = true
  historyError.value = ''
  try {
    const conversations = await api.get(`/pet/conversations?studio_article_id=${encodeURIComponent(articleId)}`)
    if (current === session) readConversation(conversations?.[0])
  } catch (err) {
    if (current === session) historyError.value = err?.message || '加载历史失败'
  } finally {
    if (current === session) historyLoading.value = false
  }
}

async function loadSkills(current = session) {
  skillsLoading.value = true
  skillsError.value = ''
  try {
    const skills = await api.get('/skill-center/skills')
    if (current === session) availableSkills.value = skills || []
  } catch (err) {
    if (current === session) skillsError.value = err?.message || '加载技能失败'
  } finally {
    if (current === session) skillsLoading.value = false
  }
}

watch(() => props.studioArticleId, (articleId) => {
  const current = ++session
  readConversation(null)
  inputMessage.value = props.initialPrompt || hotspotPrompt.value
  loading.value = false
  editPending.value = false
  proposalPending.value = ''
  draftAction.value = ''
  feedback.value = ''
  historyError.value = ''
  skillsError.value = ''
  historyLoading.value = false
  skillsLoading.value = false
  availableSkills.value = []
  if (!articleId) return
  loadHistory(articleId, current)
  loadSkills(current)
}, { immediate: true })

watch(hotspotPrompt, (prompt, previous) => {
  if (inputMessage.value === previous || !inputMessage.value) inputMessage.value = prompt
})
watch([() => props.mode, messages, () => props.chatTurns.length, loading, () => props.chatBusy], () => {
  const el = results.value
  if (el) el.scrollTop = el.scrollHeight
}, { flush: 'post' })

async function sendMessage() {
  const text = inputMessage.value.trim()
  if (!text || loading.value || editBusy.value) return
  if (editTarget.value && props.selectedBlock) {
    await sendEdit(text)
    return
  }
  if (historyLoading.value || historyError.value) return
  if (!props.studioArticleId) {
    emit('ensureArticle', inputMessage.value)
    return
  }
  const current = session
  loading.value = true
  feedback.value = ''
  try {
    const payload = { message: text, activeSkillIds: activeSkillIds.value, ...(props.modelId ? { modelId: props.modelId } : {}) }
    const result = conversationId.value
      ? await api.post(`/pet/conversations/${encodeURIComponent(conversationId.value)}/message`, payload)
      : await api.post('/pet/conversations', { ...payload, studioArticleId: props.studioArticleId })
    if (current !== session) return
    readConversation(result)
    inputMessage.value = ''
  } catch (err) {
    if (current === session) feedback.value = err?.message || '发送失败，请稍后重试'
  } finally {
    if (current === session) loading.value = false
  }
}

function toggleSkill(skillId) {
  if (activeSkillIds.value.includes(skillId)) {
    activeSkillIds.value = activeSkillIds.value.filter((id) => id !== skillId)
  } else if (activeSkillIds.value.length < 5) {
    activeSkillIds.value = [...activeSkillIds.value, skillId]
  }
}

async function sendEdit(text = inputMessage.value.trim()) {
  if (!text || editBusy.value) return
  if (!props.sendEditRequest) {
    feedback.value = '当前无法安全改稿，请稍后重试'
    return
  }
  const current = session
  editPending.value = true
  feedback.value = ''
  try {
    await props.sendEditRequest(text, props.modelId || undefined)
    if (current === session && inputMessage.value.trim() === text) inputMessage.value = ''
  } catch (err) {
    if (current === session) feedback.value = err?.message || '改稿失败，请重试'
  } finally {
    if (current === session) editPending.value = false
  }
}

async function handleProposal(proposal, action) {
  const callback = action === 'accept' ? props.onAcceptProposal : props.onDiscardProposal
  if (!callback || proposalPending.value) return
  const current = session
  proposalPending.value = proposal.id
  feedback.value = ''
  try {
    await callback(proposal.id)
    if (current === session) feedback.value = action === 'accept' ? '已接受修改。' : '已放弃修改。'
  } catch (err) {
    if (current === session) feedback.value = err?.message || '操作失败，请重试'
  } finally {
    if (current === session) proposalPending.value = ''
  }
}

async function copyResult(text) {
  const current = session
  try {
    await navigator.clipboard.writeText(text)
    if (current === session) feedback.value = '已复制。'
  } catch {
    if (current === session) feedback.value = '复制失败，请手动选择文字复制。'
  }
}

function sourceText(metadata) {
  const sources = metadata?.sources?.length ? metadata.sources : [metadata?.source]
  return sources.map((source) => {
    if (typeof source === 'string') return source
    if (!source || typeof source !== 'object') return ''
    return [source.title, source.url, source.status && `状态：${source.status}`, Number.isFinite(source.characters) && `字符数：${source.characters}`, Number.isFinite(source.used_characters) && `使用字符数：${source.used_characters}`, source.possibly_truncated && '可能已截断', source.context_truncated && '上下文已截断'].filter(Boolean).join(' · ')
  }).filter(Boolean).join('；')
}

function draftMarkdown(message) {
  const result = message.metadata?.result
  return message.role === 'assistant' && message.metadata?.action === 'create_article' && typeof result === 'string' && result.trim()
    ? result
    : ''
}

async function useDraft(message, action) {
  const markdown = draftMarkdown(message)
  if (!markdown || draftBusy.value) return
  const apply = props.applyMarkdown
  if (action === 'apply' && !apply) return
  const current = session
  draftAction.value = action
  feedback.value = ''
  try {
    if (action === 'apply') {
      const applied = await apply(markdown)
      if (current === session) feedback.value = applied === false ? '已取消写入，当前稿未更改。' : '已写入当前稿。'
      return
    }

    const generation = lastGeneration.value
    const title = generation?.action === 'create_article' && generation.content === markdown && generation.title
      ? generation.title
      : markdown.trimStart().split(/\r?\n/, 1)[0].replace(/^#+\s*/, '').trim().slice(0, 160)
    const created = await studioApi.create({ topic: title, material: '', markdown, draft: false, titles: false })
    if (current !== session) return
    feedback.value = '已另存为新稿。'
    if (created?.id) emit('created', created.id)
  } catch (err) {
    if (current === session) feedback.value = err?.message || (action === 'apply' ? '写入当前稿失败' : '另存为新稿失败')
  } finally {
    if (current === session) draftAction.value = ''
  }
}
</script>

<template>
  <section class="pet-chat-panel" aria-label="助手对话">
    <p v-if="articleTitle" class="article-context">当前稿件：{{ articleTitle }}</p>
    <label class="model-selector">创作模型
      <select :value="modelId" aria-label="创作模型" @change="emit('update:modelId', $event.target.value)">
        <option v-for="option in modelOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </label>
    <p v-if="modelError" role="alert">{{ modelError }} <button type="button" @click="emit('reloadModels')">重试模型列表</button></p>

      <p class="input-hint">初稿技能最多选择 5 个，仅用于初稿对话；改稿使用当前稿件的改稿风格设置。</p>
      <p v-if="skillsLoading" class="input-hint">正在加载技能…</p>
      <p v-if="skillsError" class="input-hint">技能加载失败：{{ skillsError }} <button type="button" @click="loadSkills()">重试技能</button></p>
      <p v-if="historyError" class="input-hint" role="alert">历史加载失败：{{ historyError }} <button type="button" @click="loadHistory(studioArticleId)">重试历史</button></p>
      <div v-if="availableSkills.length" class="skill-selector" role="group" aria-label="写作技能">
        <span class="label">技能</span>
        <div class="skill-tags">
          <button
            v-for="skill in availableSkills" :key="skill.id" type="button" class="tool-btn"
            :title="`${typeLabels[skill.skillType] || skill.skillType}｜${skill.summary || skill.description || ''}`"
            :aria-pressed="activeSkillIds.includes(skill.id)" :disabled="loading || historyLoading || Boolean(historyError) || (activeSkillIds.length >= 5 && !activeSkillIds.includes(skill.id))"
            @click="toggleSkill(skill.id)"
          >{{ skill.name }}</button>
        </div>
      </div>

      <div ref="results" class="message-container" role="log" aria-label="助手对话记录" tabindex="0">
        <p v-if="messages.length && chatTurns.length" class="input-hint">普通对话与改稿记录分别展示，跨记录顺序不代表时间先后。</p>
        <details v-if="hotspot" class="hotspot-context">
          <summary>热点选题：{{ hotspot.title }}</summary>
          <p>{{ hotspot.material }}</p>
        </details>
        <div v-for="(msg, idx) in messages" :key="idx" :class="['message', `message-${msg.role === 'assistant' ? 'pet' : 'user'}`]">
          <div class="message-avatar" aria-hidden="true">{{ msg.role === 'user' ? '我' : '月' }}</div>
          <div class="message-body">
            <p class="message-text">{{ msg.content }}</p>
            <img v-if="msg.metadata?.card?.preview_url" class="card-preview" :src="msg.metadata.card.preview_url" alt="生成的动态卡片预览" />
            <div v-if="msg.metadata?.action" class="message-intent">{{ actionLabels[msg.metadata.action] || msg.metadata.action }}</div>
            <div v-if="msg.metadata?.model?.modelCode || msg.metadata?.model?.modelId" class="message-intent">实际模型：{{ msg.metadata.model.modelCode || msg.metadata.model.modelId }}{{ msg.metadata.model.fallbackUsed ? '（已降级）' : '' }}</div>
            <div v-if="sourceText(msg.metadata)" class="message-intent">来源：{{ sourceText(msg.metadata) }}</div>
            <div v-if="msg.metadata?.context" class="message-intent">上下文：{{ typeof msg.metadata.context === 'string' ? msg.metadata.context : JSON.stringify(msg.metadata.context) }}</div>
            <div v-if="msg.metadata?.result_type" class="message-intent">结果类型：{{ msg.metadata.result_type }}</div>
            <button v-if="msg.role === 'assistant' && msg.metadata?.action === 'refine_paragraph' && msg.metadata?.result" type="button" class="tool-btn" @click="copyResult(msg.metadata.result)">复制润色结果</button>
            <p v-if="msg.metadata?.card" class="message-intent">卡片仅供预览，可使用浏览器保存图片。</p>
            <div v-if="draftMarkdown(msg)" class="draft-actions">
              <button type="button" class="btn-primary" :disabled="draftBusy || !applyMarkdown" @click="useDraft(msg, 'apply')">写入当前稿</button>
              <button type="button" class="tool-btn" :disabled="draftBusy" @click="useDraft(msg, 'save')">另存为新稿</button>
            </div>
          </div>
        </div>
        <p v-if="historyLoading" class="empty-hint">正在加载会话…</p>
        <p v-else-if="loading" class="empty-hint">正在生成回复…</p>
        <p v-else-if="!studioArticleId" class="empty-hint">请先打开稿件，或在写作工具中新建稿件。</p>
        <p v-else-if="!messages.length" class="empty-hint">{{ hotspot ? '已带入热点素材。选好技能或补充要求后，点击发送。' : '输入标题、描述或公开链接，按选中的技能写初稿。' }}</p>
        <p v-if="chatTurns.length" class="input-hint">改稿记录</p>
        <div v-for="(turn, index) in chatTurns" :key="index" :class="['message', `message-${turn.role === 'assistant' ? 'pet' : 'user'}`]">
          <div class="message-avatar" aria-hidden="true">{{ turn.role === 'assistant' ? '月' : '我' }}</div>
          <div class="message-body">
            <p class="message-text">{{ turn.text }}</p>
            <div v-if="turn.model?.modelCode || turn.model?.modelId" class="message-intent">实际模型：{{ turn.model.modelCode || turn.model.modelId }}{{ turn.model.fallbackUsed ? '（已降级）' : '' }}</div>
            <button v-if="turn.role === 'user' && turn.text" type="button" class="tool-btn" :disabled="editBusy" @click="inputMessage = turn.text">重新编辑</button>
            <div v-if="turn.proposal" class="proposal">
              <p>修改预览（{{ turn.proposal.status === 'pending' ? '待确认' : turn.proposal.status === 'accepted' ? '已接受' : '已放弃' }}）</p>
              <div v-for="change in turn.proposal.changes" :key="change.blockId" class="proposal-change">
                <p>原文：{{ change.before }}</p><p>修改后：{{ change.after }}</p>
              </div>
              <ul v-if="turn.proposal.receipts?.length" class="receipts"><li v-for="(receipt, i) in turn.proposal.receipts" :key="i">{{ receipt }}</li></ul>
              <div v-if="turn.proposal.status === 'pending'" class="draft-actions">
                <button type="button" class="btn-primary" :disabled="Boolean(proposalPending) || !onAcceptProposal" @click="handleProposal(turn.proposal, 'accept')">接受修改</button>
                <button type="button" class="tool-btn" :disabled="Boolean(proposalPending) || !onDiscardProposal" @click="handleProposal(turn.proposal, 'discard')">放弃修改</button>
              </div>
            </div>
            <ul v-if="turn.receipts?.length" class="receipts">
              <li v-for="(receipt, i) in turn.receipts.slice(0, 5)" :key="i">{{ receipt }}</li>
              <li v-if="turn.receipts.length > 5" class="receipts-more">等 {{ turn.receipts.length }} 项改动</li>
            </ul>
          </div>
        </div>
        <p v-if="chatBusy" class="empty-hint">正在按你的要求生成修改预览。</p>
        <p v-else-if="!chatTurns.length" class="empty-hint">选择段落并勾选改写后才会改稿；否则发送普通对话。</p>
      </div>

      <div class="presets" role="group" aria-label="常用改稿指令">
        <button v-for="preset in presets" :key="preset.label" type="button" class="tool-btn" :disabled="editBusy" :title="preset.text" @click="inputMessage = preset.text">{{ preset.label }}</button>
      </div>
      <div v-if="selectedBlock" class="anchor">
        <span class="anchor-summary">已选第 {{ selectedBlock.number }} 段 · {{ selectedBlock.text || '空段' }} <label><input v-model="editTarget" type="checkbox"> 将发送内容用于改写此段</label></span>
        <button type="button" class="anchor-off" aria-label="取消指定段落，返回普通对话" @click="emit('clearSelection')">×</button>
      </div>
      <form class="input-area" @submit.prevent="sendMessage">
        <textarea
          v-model="inputMessage" class="input-field" rows="3" aria-label="对话要求"
          :placeholder="editTarget && selectedBlock ? `想怎么改第 ${selectedBlock.number} 段？` : '想写什么？输入链接、标题或一段描述…'"
          :disabled="editBusy || loading" @keydown.ctrl.enter.prevent="sendMessage"
        />
        <button type="submit" :disabled="!inputMessage.trim() || editBusy || loading || (!(editTarget && selectedBlock) && (historyLoading || Boolean(historyError)))" class="btn-primary">{{ chatBusy ? '改稿中' : loading ? '发送中' : '发送' }}</button>
      </form>
      <p class="input-hint">Ctrl + Enter 发送；修改需预览并确认。目前仅支持重写现有段落，不支持拆段或修改标题。</p>
      <p v-if="feedback" class="pet-feedback" role="status">{{ feedback }}</p>
  </section>
</template>

<style scoped>
.pet-chat-panel { display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; max-height: 100%; overflow: auto; color: var(--color-text-primary); }
.article-context { flex: none; margin: 0; padding: 8px 12px; color: var(--color-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.skill-selector { display: flex; flex: none; align-items: flex-start; gap: 8px; max-height: 96px; overflow-y: auto; padding: 8px 12px; border-bottom: 1px solid var(--color-border); font-size: 12px; }
.label { flex: none; color: var(--color-text-secondary); }
.skill-tags, .draft-actions { display: flex; flex-wrap: wrap; gap: 5px; }
.tool-btn { padding: 4px 10px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-text-primary); font-size: 12px; cursor: pointer; }
.tool-btn[aria-pressed='true'] { border-color: var(--color-accent); background: var(--color-accent-soft); color: var(--color-accent); }
.message-container { display: flex; flex: 1 1 auto; flex-direction: column; gap: 8px; min-height: 6rem; overflow-y: auto; padding: 12px; }
.hotspot-context { padding: 8px 10px; border: 1px solid var(--color-border); border-radius: 6px; font-size: 12px; }
.hotspot-context summary { cursor: pointer; overflow-wrap: anywhere; }
.hotspot-context p { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.message { display: flex; gap: 8px; max-width: 100%; }
.message-avatar { display: grid; place-items: center; flex: 0 0 24px; height: 24px; border-radius: 50%; background: var(--color-surface-muted); color: var(--color-text-secondary); font-size: 11px; }
.message-body { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.message-text { margin: 0; padding: 8px 10px; border-radius: 14px; background: var(--color-surface-muted); font-size: 13px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.message-user { flex-direction: row-reverse; }
.message-user .message-text { background: var(--color-accent); color: var(--color-on-accent); border-bottom-right-radius: 4px; }
.message-pet .message-text { border-bottom-left-radius: 4px; }
.message-intent { color: var(--color-text-secondary); font-size: 11px; }
.card-preview { max-width: 100%; border: 1px solid var(--color-border); border-radius: 12px; }
.proposal { padding: 8px; border: 1px solid var(--color-border); border-radius: 8px; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.proposal-change { border-top: 1px solid var(--color-border); }
.receipts { margin: 0; padding-left: 16px; color: var(--color-text-secondary); font-size: 11px; }
.receipts-more { list-style: none; margin-left: -16px; }
.btn-primary { padding: 6px 14px; border: 0; border-radius: 999px; background: var(--color-accent); color: var(--color-on-accent); font-size: 12px; cursor: pointer; }
.btn-primary:disabled, .tool-btn:disabled { opacity: .55; cursor: not-allowed; }
.empty-hint { margin: auto; color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; text-align: center; }
.presets { display: flex; flex: none; flex-wrap: wrap; gap: 5px; padding: 8px 12px; border-top: 1px solid var(--color-border); }
.anchor { display: flex; flex: none; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px; border-top: 1px solid var(--color-border); background: var(--color-accent-soft); color: var(--color-accent); font-size: 12px; }
.anchor-summary { min-width: 0; overflow-wrap: anywhere; }
.anchor-off { border: 0; background: none; color: inherit; font-size: 16px; cursor: pointer; }
.input-area { display: flex; flex: none; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--color-border); }
.input-field { flex: 1; min-width: 0; max-height: 10rem; padding: 8px 10px; resize: vertical; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); color: var(--color-text-primary); font: inherit; font-size: 13px; line-height: 1.5; }
.input-area .btn-primary { flex: none; min-height: 36px; }
.input-hint, .pet-feedback { flex: none; margin: 0; padding: 0 12px 10px; color: var(--color-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
</style>
