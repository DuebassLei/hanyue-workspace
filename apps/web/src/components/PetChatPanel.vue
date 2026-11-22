<template>
  <div class="pet-chat-panel" :class="{ minimized, dragged: dragPos }" :style="dragStyle">
    <header class="pet-header" @pointerdown="startDrag">
      <div class="pet-avatar" aria-hidden="true">月</div>
      <div class="pet-info">
        <h3 class="pet-name">创作小助手</h3>
        <p class="pet-status">{{ statusText }}</p>
      </div>
      <div class="pet-actions">
        <button class="btn-icon" type="button" :title="minimized ? '展开' : '最小化'" :aria-label="minimized ? '展开' : '最小化'" @click="toggleMinimize">
          <svg width="16" height="16" viewBox="0 0 16 16"><line x1="2" y1="8" x2="14" y2="8" stroke="currentColor" stroke-width="2" /></svg>
        </button>
        <button class="btn-icon" type="button" title="关闭" aria-label="关闭" @click="closePet">
          <svg width="16" height="16" viewBox="0 0 16 16"><line x1="2" y1="2" x2="14" y2="14" stroke="currentColor" stroke-width="2" /><line x1="14" y1="2" x2="2" y2="14" stroke="currentColor" stroke-width="2" /></svg>
        </button>
      </div>
    </header>

    <template v-if="!minimized">
      <div class="pet-modes" role="tablist" aria-label="助手模式">
        <button type="button" role="tab" :aria-selected="mode === 'create'" :class="{ active: mode === 'create' }" @click="mode = 'create'">创作</button>
        <button type="button" role="tab" :aria-selected="mode === 'edit'" :class="{ active: mode === 'edit' }" @click="mode = 'edit'">改稿</button>
      </div>

      <!-- 创作：选技能 → 生成初稿 → 显式新建成稿 -->
      <template v-if="mode === 'create'">
        <div v-if="availableSkills.length" class="skill-selector">
          <span class="label">技能</span>
          <div class="skill-tags">
            <button v-for="skill in availableSkills" :key="skill.id" type="button" :title="`${skillTypeLabel(skill.skillType)}｜${skill.description || ''}`" :class="['skill-tag', { active: activeSkillIds.includes(skill.id) }]" @click="toggleSkill(skill.id)">
              {{ skill.displayName }}
            </button>
          </div>
        </div>

        <div class="message-container">
          <div v-for="(msg, idx) in messages" :key="idx" :class="['message', `message-${msg.role === 'assistant' ? 'pet' : msg.role}`]">
            <div class="message-avatar" aria-hidden="true">{{ msg.role === 'user' ? '我' : '月' }}</div>
            <div class="message-body">
              <p class="message-text">{{ msg.content }}</p>
              <img v-if="msg.metadata?.card?.preview_url" class="card-preview" :src="msg.metadata.card.preview_url" alt="生成的动态卡片预览" />
              <div v-if="msg.metadata?.action" class="message-intent">{{ actionLabel(msg.metadata.action) }}</div>
              <button v-if="msg.role === 'assistant' && msg.metadata?.result" class="btn-primary" type="button" :disabled="applying" @click="applyGeneration(msg.metadata.result)">
                {{ applying ? '正在新建…' : '新建成稿' }}
              </button>
            </div>
          </div>
          <div v-if="loading" class="message message-pet">
            <div class="message-avatar" aria-hidden="true">月</div>
            <div class="typing-indicator"><span></span><span></span><span></span></div>
          </div>
          <p v-if="!messages.length && !loading" class="empty-hint">给我标题、描述或公开链接，我按选中的技能写初稿。</p>
        </div>

        <div v-if="selectedText" class="quick-tools">
          <span class="label">选中文字</span>
          <button class="tool-btn" type="button" @click="refineSelected('polish')">润色</button>
          <button class="tool-btn" type="button" @click="refineSelected('rewrite')">改写</button>
          <button class="tool-btn" type="button" @click="refineSelected('remove_ai_taste')">去 AI 味</button>
          <button class="tool-btn" type="button" @click="generateCard">生成卡</button>
        </div>

        <form class="input-area" @submit.prevent="sendMessage">
          <input v-model="inputMessage" @select="onTextSelect" type="text" placeholder="想写什么？链接、标题或一段描述…" class="input-field" :disabled="loading" />
          <button type="submit" :disabled="!inputMessage.trim() || loading" class="btn-send">{{ loading ? '发送中' : '发送' }}</button>
        </form>
        <div v-if="error" class="pet-feedback" role="status">{{ error }}</div>
      </template>

      <!-- 改稿：直接作用于当前稿件，可锚定到具体段落 -->
      <template v-else>
        <div class="message-container">
          <div v-for="(turn, index) in chatTurns" :key="index" :class="['message', `message-${turn.role === 'assistant' ? 'pet' : 'user'}`]">
            <div class="message-avatar" aria-hidden="true">{{ turn.role === 'assistant' ? '月' : '我' }}</div>
            <div class="message-body">
              <p class="message-text">{{ turn.text }}</p>
              <ul v-if="turn.receipts?.length" class="receipts">
                <li v-for="(receipt, i) in turn.receipts.slice(0, 5)" :key="i">{{ receipt }}</li>
                <li v-if="turn.receipts.length > 5" class="receipts-more">等 {{ turn.receipts.length }} 项改动</li>
              </ul>
            </div>
          </div>
          <p v-if="chatBusy" class="empty-hint">正在按你的要求改稿，改完会直接刷新正文。</p>
          <p v-else-if="!chatTurns.length" class="empty-hint">
            说一句要改哪里。先在正文点一段再发，就只改那一段；下面的常用说法点一下就能用。
          </p>
        </div>

        <div class="presets" role="group" aria-label="常用改稿指令">
          <button v-for="preset in presets" :key="preset.label" type="button" class="tool-btn" :disabled="chatBusy" :title="preset.text" @click="usePreset(preset)">{{ preset.label }}</button>
        </div>

        <div v-if="selectedBlock" class="anchor">
          <span>只改第 {{ selectedBlock.number }} 段 · {{ selectedBlock.text || '空段' }}</span>
          <button type="button" class="anchor-off" aria-label="取消指定段落，改成整篇指令" @click="$emit('clearSelection')">×</button>
        </div>

        <form class="input-area" @submit.prevent="sendEdit">
          <textarea
            v-model="editInput"
            class="input-field edit-area"
            rows="3"
            :placeholder="selectedBlock ? `想怎么改第 ${selectedBlock.number} 段？` : '想怎么改？点了正文某一段就只改那一段'"
            :disabled="chatBusy"
            @keydown.ctrl.enter.prevent="sendEdit"
          />
          <button type="submit" :disabled="!editInput.trim() || chatBusy" class="btn-send">{{ chatBusy ? '改稿中' : '发送' }}</button>
        </form>
        <p class="input-hint">Ctrl + Enter 发送；改稿结果直接写入当前稿件。</p>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { studioApi } from '../api/studio'

// 拖动：按住标题栏移动，位置限制在视口内；按钮上的点击不触发拖动
const dragPos = ref(null)
const dragStyle = computed(() => (dragPos.value ? { left: `${dragPos.value.x}px`, top: `${dragPos.value.y}px` } : {}))
let dragOffset = null

function startDrag(event) {
  if (event.button !== 0 || event.target.closest('button, input, textarea, a')) return
  const panel = event.currentTarget.parentElement
  const rect = panel.getBoundingClientRect()
  dragOffset = { dx: event.clientX - rect.left, dy: event.clientY - rect.top, w: rect.width, h: rect.height }
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', stopDrag, { once: true })
  event.currentTarget.setPointerCapture?.(event.pointerId)
}

function onDrag(event) {
  if (!dragOffset) return
  const maxX = window.innerWidth - dragOffset.w
  const maxY = window.innerHeight - dragOffset.h
  const x = Math.min(Math.max(0, event.clientX - dragOffset.dx), Math.max(0, maxX))
  const y = Math.min(Math.max(0, event.clientY - dragOffset.dy), Math.max(0, maxY))
  dragPos.value = { x, y }
}

function stopDrag() {
  dragOffset = null
  window.removeEventListener('pointermove', onDrag)
}

onUnmounted(() => window.removeEventListener('pointermove', onDrag))

const props = defineProps({
  studioArticleId: { type: String, required: true },
  chatTurns: { type: Array, default: () => [] },
  chatBusy: { type: Boolean, default: false },
  selectedBlock: { type: Object, default: null },
})
const emit = defineEmits(['close', 'chat', 'clearSelection', 'created'])

const PRESETS = [
  { label: '去 AI 味', text: '把 AI 味重的句子改成本人平时说话的样子，意思和信息都不变。' },
  { label: '开头换场景', text: '开头太像总结，换成一个具体的场景或动作。' },
  { label: '压短', text: '太长了，压到三句以内，关键信息保留。' },
  { label: '补例子', text: '补一个具体的例子或数字，别只讲道理。' },
  { label: '拆段', text: '太挤了，拆成两段，每段只说一件事。' },
  { label: '收结尾', text: '结尾别喊口号，用一句具体的话收住。' },
]

const TYPE_LABELS = { writing: '写作风格', persona: '人物思维', knowledge: '领域知识' }
const ACTION_LABELS = {
  create_article: '初稿',
  refine_paragraph: '改稿',
  generate_svg_card: '动态卡',
  select_skill: '技能',
  query_help: '说明',
}

const mode = ref('create')
const inputMessage = ref('')
const editInput = ref('')
const loading = ref(false)
const selectedText = ref('')
const minimized = ref(false)
const conversationId = ref('')
const messages = ref([])
const activeSkillIds = ref([])
const availableSkills = ref([])
const lastGeneration = ref(null)
const error = ref('')
const applying = ref(false)

const presets = PRESETS
const statusText = computed(() => {
  if (mode.value === 'edit') {
    if (props.chatBusy) return '改稿中…'
    return props.selectedBlock ? `已选中第 ${props.selectedBlock.number} 段` : '告诉我改哪里'
  }
  return loading.value ? '思考中…' : messages.value.length ? '随时待命' : '准备就绪'
})

function skillTypeLabel(value) {
  return TYPE_LABELS[value] || value
}
function actionLabel(value) {
  return ACTION_LABELS[value] || value
}

async function loadContext() {
  if (!props.studioArticleId) return
  error.value = ''
  try {
    const [skills, conversations] = await Promise.all([
      api.get('/skill-center/skills'),
      api.get(`/pet/conversations?studio_article_id=${encodeURIComponent(props.studioArticleId)}`),
    ])
    availableSkills.value = (skills || []).map((skill) => ({
      id: skill.id,
      displayName: skill.name,
      description: skill.summary || skill.description,
      skillType: skill.skillType,
    }))
    const conversation = conversations?.[0]
    if (conversation) {
      conversationId.value = conversation.id
      messages.value = conversation.messages || []
      activeSkillIds.value = conversation.activeSkillIds || []
      lastGeneration.value = conversation.lastGeneration || null
    } else {
      conversationId.value = ''
      messages.value = []
      activeSkillIds.value = []
      lastGeneration.value = null
    }
  } catch (err) {
    error.value = err.message || '加载创作助手失败'
  }
}

// 切稿时重新载入：不能把上一篇的对话留在这一篇下面
watch(() => props.studioArticleId, loadContext)

async function sendMessage() {
  const text = inputMessage.value.trim()
  if (!text || loading.value) return
  const articleId = props.studioArticleId
  loading.value = true
  error.value = ''
  try {
    const result = conversationId.value
      ? await api.post(`/pet/conversations/${encodeURIComponent(conversationId.value)}/message`, { message: text, activeSkillIds: activeSkillIds.value })
      : await api.post('/pet/conversations', { studioArticleId: articleId, message: text, activeSkillIds: activeSkillIds.value })
    // 等待期间用户可能已切稿：只把结果写回原来的稿件
    if (props.studioArticleId !== articleId) return
    conversationId.value = result.id
    messages.value = result.messages || []
    activeSkillIds.value = result.activeSkillIds || activeSkillIds.value
    lastGeneration.value = result.lastGeneration || null
    inputMessage.value = ''
  } catch (err) {
    if (props.studioArticleId === articleId) error.value = err.message || '发送失败，请稍后重试'
  } finally {
    if (props.studioArticleId === articleId) loading.value = false
  }
}

function toggleSkill(skillId) {
  activeSkillIds.value = activeSkillIds.value.includes(skillId)
    ? activeSkillIds.value.filter((id) => id !== skillId)
    : [...activeSkillIds.value, skillId]
}
function toggleMinimize() { minimized.value = !minimized.value }
function closePet() { emit('close') }
function onTextSelect(event) {
  const target = event.target
  selectedText.value = target.value.substring(target.selectionStart || 0, target.selectionEnd || 0)
}
function refineSelected(type) {
  if (!selectedText.value) return
  const label = { polish: '润色', rewrite: '改写', remove_ai_taste: '去AI味' }[type] || type
  inputMessage.value = `请帮我${label}这段话：\n"${selectedText.value}"`
}
function generateCard() {
  if (selectedText.value) inputMessage.value = `根据这段话生成一个动态卡片：\n${selectedText.value}`
}

function usePreset(preset) {
  if (props.chatBusy) return
  editInput.value = preset.text
}
function sendEdit() {
  const text = editInput.value.trim()
  if (!text || props.chatBusy) return
  editInput.value = ''
  emit('chat', text)
}

async function applyGeneration(generation) {
  if (!generation || applying.value) return
  const content = typeof generation === 'string' ? generation : generation.content || ''
  if (!content.trim()) return
  applying.value = true
  error.value = ''
  try {
    const created = await studioApi.create({
      topic: typeof generation === 'object' ? generation.title || '' : '',
      material: '',
      markdown: content,
      draft: false,
      titles: false,
    })
    error.value = '已新建成稿，可从稿件列表打开继续编辑。'
    if (created?.id) emit('created', created.id)
  } catch (err) {
    error.value = err.message || '新建成稿失败'
  } finally {
    applying.value = false
  }
}

onMounted(loadContext)
</script>

<style scoped>
.pet-chat-panel { display: flex; flex-direction: column; position: fixed; right: 20px; bottom: 20px; width: min(560px, calc(100vw - 28px)); height: min(860px, calc(100vh - 32px)); max-height: calc(100vh - 32px); background: var(--color-surface); border: 1.5px solid var(--color-accent); border-radius: 20px; box-shadow: 0 0 0 4px var(--color-accent-soft), var(--shadow-lg); overflow: hidden; color: var(--color-text-primary); z-index: 61; }
.pet-chat-panel.dragged { right: auto; bottom: auto; }
.pet-header { cursor: move; user-select: none; touch-action: none; }
.pet-header button, .pet-header .btn-icon { cursor: pointer; }
.pet-chat-panel.minimized { width: auto; max-height: none; }
.pet-header { display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: linear-gradient(180deg, var(--color-accent-soft), var(--color-surface-muted)); border-bottom: 1px solid var(--color-border); }
.pet-avatar { display: grid; place-items: center; flex: 0 0 36px; width: 36px; height: 36px; border-radius: 50%; background: var(--color-accent); color: var(--color-on-accent, #fff); font-weight: 700; }
.pet-info { flex: 1; min-width: 0; }
.pet-name { margin: 0; font-size: 14px; font-weight: 600; }
.pet-status { margin: 2px 0 0; color: var(--color-text-secondary); font-size: 12px; }
.pet-actions { display: flex; gap: 4px; }
.btn-icon { display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); color: var(--color-text-primary); cursor: pointer; }
.pet-modes { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; padding: 6px 12px; border-bottom: 1px solid var(--color-border); background: var(--color-surface-muted); }
.pet-modes button { padding: 5px 0; border: 0; border-radius: 4px; background: transparent; color: var(--color-text-secondary); font-size: 12px; cursor: pointer; }
.pet-modes button.active { background: var(--color-surface); color: var(--color-text-primary); box-shadow: var(--shadow-sm); }
.skill-selector { display: flex; align-items: flex-start; gap: 8px; max-height: 96px; overflow-y: auto; padding: 8px 12px; border-bottom: 1px solid var(--color-border); font-size: 12px; }
.skill-selector .label, .quick-tools .label { flex: 0 0 auto; color: var(--color-text-secondary); }
.skill-tags { display: flex; flex: 1; flex-wrap: wrap; gap: 5px; }
.skill-tag, .tool-btn { padding: 4px 10px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-text-secondary); font-size: 11px; cursor: pointer; }
.skill-tag.active { border-color: var(--color-accent); background: var(--color-accent-soft); color: var(--color-accent); }
.message-container { display: flex; flex: 1; flex-direction: column; gap: 8px; min-height: 170px; overflow-y: auto; padding: 12px; background: var(--color-surface); }
.message { display: flex; gap: 8px; max-width: 100%; }
.message-avatar { display: grid; place-items: center; flex: 0 0 24px; width: 24px; height: 24px; border-radius: 50%; background: var(--color-surface-muted); color: var(--color-text-secondary); font-size: 11px; }
.message-body { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.message-text { max-width: 100%; margin: 0; padding: 8px 10px; border-radius: 14px; background: var(--color-surface-muted); font-size: 13px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
.message-user { flex-direction: row-reverse; }
.message-user .message-text { background: var(--color-accent); color: var(--color-on-accent, #fff); border-bottom-right-radius: 4px; }
.message-pet .message-text { border-bottom-left-radius: 4px; }
.message-intent { color: var(--color-text-secondary); font-size: 11px; }
.card-preview { max-width: 100%; border: 1px solid var(--color-border); border-radius: 12px; }
.receipts { margin: 0; padding-left: 16px; color: var(--color-text-secondary); font-size: 11px; }
.receipts-more { list-style: none; margin-left: -16px; color: var(--color-text-secondary); }
.btn-primary { align-self: flex-start; padding: 6px 14px; border: 0; border-radius: 999px; background: var(--color-accent); color: var(--color-on-accent, #fff); font-size: 12px; cursor: pointer; }
.btn-primary:disabled, .btn-send:disabled { opacity: .55; cursor: not-allowed; }
.empty-hint { margin: auto; color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; text-align: center; }
.quick-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; padding: 8px 12px; border-top: 1px solid var(--color-border); }
.presets { display: flex; flex-wrap: wrap; gap: 5px; padding: 8px 12px; border-top: 1px solid var(--color-border); }
.anchor { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px; border-top: 1px solid var(--color-border); background: var(--color-accent-soft); color: var(--color-accent); font-size: 11px; }
.anchor-off { border: 0; background: none; color: inherit; font-size: 14px; cursor: pointer; }
.input-area { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--color-border); background: var(--color-surface); }
.input-field { flex: 1; min-width: 0; height: 38px; padding: 0 14px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-text-primary); font: inherit; font-size: 13px; }
.edit-area { height: auto; padding: 10px 14px; resize: vertical; line-height: 1.5; border-radius: 14px !important; }
.btn-send { min-width: 56px; height: 38px; padding: 0 14px; border: 0; border-radius: 999px; background: var(--color-accent); color: var(--color-on-accent, #fff); cursor: pointer; }
.input-hint { margin: 0; padding: 0 12px 10px; color: var(--color-text-secondary); font-size: 11px; }
.pet-feedback { padding: 8px 12px; border-top: 1px solid var(--color-border); color: var(--color-text-secondary); font-size: 12px; }
.typing-indicator { display: flex; gap: 4px; padding: 8px 10px; }
.typing-indicator span { width: 6px; height: 6px; border-radius: 50%; background: var(--color-text-secondary); animation: bounce 1.4s infinite; }
.typing-indicator span:nth-child(2) { animation-delay: .2s; }
.typing-indicator span:nth-child(3) { animation-delay: .4s; }
@keyframes bounce { 0%, 80%, 100% { transform: scale(.8); opacity: .5; } 40% { transform: scale(1); opacity: 1; } }
</style>
