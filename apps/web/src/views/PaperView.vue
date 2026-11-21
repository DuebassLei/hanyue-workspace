<script setup>
/**
 * 成稿台：段落级编辑编排。
 *
 * 与「创作空间」的分工：
 * - 创作空间是一次性生成整篇、所见即所得地改；
 * - 成稿台把正文拆成有段号的段落，所有改动（对话改稿、标红修复、手工编辑）
 *   都落到具体段落上，能逐段对比、逐段退回，也能整篇撤销。
 *
 * 页面所有状态都来自后端返回的整篇快照；后台任务跑起来后按 job 字段轮询，
 * 不做乐观更新，避免和后台改稿互相覆盖。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { NButton, NInput, NSelect, NSwitch } from 'naive-ui'
import { studioApi, uploadReport } from '../api/studio'
import { toast } from '../ui/feedback'
import PaperLayoutPreview from '../components/paper/PaperLayoutPreview.vue'
import PaperLayoutPanel from '../components/paper/PaperLayoutPanel.vue'
import PaperMediaPanel from '../components/paper/PaperMediaPanel.vue'
import PaperPublishPanel from '../components/paper/PaperPublishPanel.vue'

const articles = ref([])
const article = ref(null)
const settings = ref({})
const archived = ref(false)
const loading = ref(false)
const error = ref('')
const tab = ref('chat')
const message = ref('')
const actionBusy = ref('')
const editing = reactive({})
const showCreate = ref(false)
const createForm = reactive({ topic: '', material: '', markdown: '' })
const reportText = ref('')
const qualityKey = ref('')
const qualityEndpoint = ref('')
const reportFile = ref(null)
// 中间栏两种查看方式：段号稿纸（改稿）/ 公众号排版预览（定稿后看效果）。
// 放在同一页而不是跳去「排版工作台」，是因为排版要跟着稿件走，跳走再回来容易丢上下文。
const sheetMode = ref('paper')
const layoutOptions = ref({})
const selectedBlockId = ref('')
let timer = null

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
const job = computed(() => article.value?.job || null)
// 就绪度前端算：接口只给正文/质检/标题/封面/横幅五项，不额外多一次请求
const readiness = computed(() => {
  const current = article.value
  if (!current) return { items: [], done: 0, total: 5 }
  const pending = (current.blocks || []).some((block) => ['flag', 'fixed'].includes(block.check?.status))
  const items = [
    { id: 'body', label: '正文', done: Boolean(current.blocks?.length) },
    { id: 'title', label: '标题', done: Boolean(current.title) },
    { id: 'quality', label: '质检', done: Boolean(current.zhuque?.round) && !pending },
    { id: 'cover', label: '封面', done: Boolean(current.cover?.assetId || current.cover?.id) },
    { id: 'banner', label: '横幅', done: Boolean(current.banners?.[current.bannerStyle]?.spec) },
  ]
  return { items, done: items.filter((item) => item.done).length, total: items.length }
})
const bannerStyle = computed({
  get: () => article.value?.bannerStyle || 'poster',
  set: (value) => value && article.value && (article.value.bannerStyle = value),
})
const bannerWarnings = computed(() => article.value?.banners?.[article.value?.bannerStyle]?.warnings || [])

const titleOptions = computed(() =>
  (article.value?.titles || []).map((item) => ({ label: `${item.text}${item.recommended ? '（推荐）' : ''}`, value: item.text })),
)

async function loadState() {
  try {
    const data = await studioApi.state(archived.value)
    articles.value = data.articles || []
    settings.value = data.settings || {}
  } catch (err) {
    error.value = err.message
  }
}

async function openArticle(id) {
  loading.value = true
  error.value = ''
  try {
    article.value = await studioApi.get(id)
    layoutOptions.value = { ...(article.value.layoutOptions || {}) }
    selectedBlockId.value = ''
    Object.keys(editing).forEach((key) => delete editing[key])
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function applySnapshot(next) {
  if (!next) return
  // 列表项只保留元信息，正文快照单独保存
  article.value = next
  const meta = articles.value.find((item) => item.id === next.id)
  if (meta) Object.assign(meta, { title: next.title, updatedAt: next.updatedAt })
}

async function run(label, work, { poll = true } = {}) {
  actionBusy.value = label
  error.value = ''
  try {
    const snapshot = await work()
    if (snapshot) applySnapshot(snapshot)
    if (poll && snapshot?.job) startPolling()
    else await loadState()
  } catch (err) {
    error.value = err.message
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
      const snapshot = await studioApi.get(article.value.id)
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

const createArticle = () =>
  run('create', async () => {
    const snapshot = await studioApi.create({
      topic: createForm.topic,
      material: createForm.material,
      markdown: createForm.markdown,
      draft: !createForm.markdown.trim(),
      titles: true,
    })
    showCreate.value = false
    createForm.topic = ''
    createForm.material = ''
    createForm.markdown = ''
    await loadState()
    return snapshot
  })

const sendChat = () =>
  run('chat', async () => {
    const text = message.value.trim()
    if (!text) return null
    message.value = ''
    return studioApi.chat(article.value.id, text)
  })

const saveBlock = (block) =>
  run('block', async () => {
    const text = editing[block.id]
    if (text === undefined) return null
    const snapshot = await studioApi.updateBlock(article.value.id, block.id, {
      text,
      expectedText: block.text,
    })
    delete editing[block.id]
    return snapshot
  })

const fixBlock = (block) => run('fix', () => studioApi.fixBlock(article.value.id, block.id))
const keepBlock = (block) => run('keep', () => studioApi.keepBlock(article.value.id, block.id), { poll: false })
const revertBlock = (block) => run('revert', () => studioApi.revertBlock(article.value.id, block.id), { poll: false })
const undo = () => run('undo', () => studioApi.undo(article.value.id), { poll: false })
const detect = () => run('detect', () => studioApi.detect(article.value.id))
const fixAll = () => run('fixAll', () => studioApi.fixAll(article.value.id))
const stop = () => run('stop', () => studioApi.stop(article.value.id), { poll: false })
const regenTitles = () => run('titles', () => studioApi.titles(article.value.id))
const pickTitle = (text) => run('title', () => studioApi.pickTitle(article.value.id, text), { poll: false })
const makeBanner = () => run('banner', () => studioApi.banner(article.value.id, bannerStyle.value))

function cancelEdit(block) {
  delete editing[block.id]
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
  if (next?.id) applySnapshot(next)
}

function selectBlock(block) {
  selectedBlockId.value = block.id
}

// 切到「排版预览」时顺手把右栏切到排版设置：用户点一次就能看到效果并调样式，
// 不用再自己找右边的页签。从排版切回稿纸则不动右栏，方便对照着改。
function openLayout() {
  sheetMode.value = 'layout'
  if (!['layout', 'media', 'publish'].includes(tab.value)) tab.value = 'layout'
}

function openMedia() {
  sheetMode.value = 'paper'
  tab.value = 'media'
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
      const result = await studioApi.exportToContent(article.value.id)
      toast(`已生成稿件版本：${result.title}`, 'success')
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
    if (!response.ok) throw new Error(payload?.detail?.message || '报告回填失败')
    startPolling()
    return payload
  })

function pickReport(event) {
  reportFile.value = event.target.files?.[0] || null
}

onMounted(async () => {
  await loadState()
  if (articles.value.length) await openArticle(articles.value[0].id)
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="paper-view">
    <header class="paper-head">
      <div>
        <p class="eyebrow">MANUSCRIPT WORKBENCH</p>
        <h1>成稿台 <span>逐段改，整篇不跑偏。</span></h1>
      </div>
      <div class="head-actions">
        <NSwitch v-model:value="archived" size="small" @update:value="loadState">显示归档</NSwitch>
        <NButton size="small" @click="showCreate = !showCreate">{{ showCreate ? '收起' : '新建稿件' }}</NButton>
        <NButton size="small" :disabled="!article" @click="undo">撤销一步</NButton>
        <NButton size="small" type="primary" :disabled="!article" @click="exportArticle">存入文章管理</NButton>
      </div>
    </header>

    <div v-if="error" class="paper-alert error" role="alert">{{ error }}</div>
    <div v-if="article?.notice" class="paper-alert" :class="{ error: article.notice.error }" role="status">
      <span>{{ article.notice.text }}</span>
      <button class="text-button" @click="studioApi.clearNotice(article.id).then(applySnapshot)">知道了</button>
    </div>

    <div class="paper-grid">
      <aside class="paper-list">
        <form v-if="showCreate" class="create-form" @submit.prevent="createArticle">
          <NInput v-model:value="createForm.topic" size="small" placeholder="选题（可留空，交给模型提炼）" />
          <NInput v-model:value="createForm.material" size="small" type="textarea" :rows="3" placeholder="素材：事实、数字、经历" />
          <NInput v-model:value="createForm.markdown" size="small" type="textarea" :rows="4" placeholder="已有 Markdown 稿？粘贴在这里，直接导入" />
          <div class="create-actions">
            <NButton size="tiny" attr-type="submit" type="primary" :loading="actionBusy === 'create'">
              {{ createForm.markdown.trim() ? '导入稿件' : '让模型写正文' }}
            </NButton>
          </div>
        </form>

        <button
          v-for="item in articles"
          :key="item.id"
          class="list-item"
          :class="{ active: article?.id === item.id }"
          @click="openArticle(item.id)"
        >
          <span class="list-title">{{ item.title || '未命名文章' }}</span>
          <span class="list-meta">
            {{ item.chars }} 字 · {{ item.blocks }} 段
            <span v-if="item.review" class="badge flag">{{ item.review }} 待改</span>
            <span v-else-if="item.checked" class="badge pass">已质检</span>
          </span>
        </button>
        <p v-if="!articles.length" class="empty">还没有稿件。点「新建稿件」开始。</p>
      </aside>

      <main class="paper-sheet" :aria-busy="loading">
        <div v-if="loading" class="sheet-loading">正在展开稿纸…</div>
        <template v-else-if="article">
          <div class="sheet-head">
            <NInput v-model:value="article.title" size="large" placeholder="给文章一个标题" @blur="changeTitle(article.title)" />
            <div class="sheet-meta">
              <span>{{ readiness.done }}/{{ readiness.total }} 就绪</span>
              <span v-for="item in readiness.items" :key="item.id" class="ready-chip" :class="{ done: item.done }">
                {{ item.label }}
              </span>
            </div>
          </div>

          <div class="sheet-switch" role="tablist" aria-label="查看方式">
            <button role="tab" :aria-selected="sheetMode === 'paper'" :class="{ active: sheetMode === 'paper' }" @click="sheetMode = 'paper'">段号稿纸</button>
            <button role="tab" :aria-selected="sheetMode === 'layout'" :class="{ active: sheetMode === 'layout' }" @click="openLayout">排版预览</button>
            <span class="hint">
              {{ sheetMode === 'layout' ? '这是公众号里的最终效果，右侧「排版」调样式。' : '点任意一段，右侧「素材」就能给它配插图。' }}
            </span>
            <button v-if="sheetMode === 'paper'" class="text-button" @click="openMedia">配插图 →</button>
          </div>

          <div v-if="job" class="job-bar" role="status">
            <span class="job-dot" />
            <span>{{ job.label }}{{ job.progress ? ` · ${job.progress.done}/${job.progress.total} 段` : '' }}{{ job.stopping ? '（正在停止）' : '' }}</span>
            <NButton v-if="job.kind === 'fixall'" size="tiny" @click="stop">停止</NButton>
          </div>

          <ol v-if="sheetMode === 'paper'" class="blocks">
            <li
              v-for="(block, index) in article.blocks"
              :key="block.id"
              class="block"
              :class="[block.check?.status || '', { editing: editing[block.id] !== undefined, selected: selectedBlockId === block.id }]"
              @click="selectBlock(block)"
            >
              <div class="block-head">
                <span class="block-no">{{ index + 1 }}</span>
                <span class="block-kind">{{ kindLabel[block.kind] || block.kind }}</span>
                <span v-if="block.check?.status" class="badge" :class="block.check.status">
                  {{ statusLabel[block.check.status] || block.check.status }}
                  <template v-if="block.check.score">· {{ block.check.score }}</template>
                  <template v-if="block.check.fixes">· 改过 {{ block.check.fixes }} 轮</template>
                </span>
                <span v-if="block.check?.worse" class="badge danger">比上一版更重</span>
                <span class="block-actions">
                  <button v-if="editing[block.id] === undefined" class="text-button" @click="editing[block.id] = block.text">改</button>
                  <template v-else>
                    <button class="text-button" :disabled="actionBusy === 'block'" @click="saveBlock(block)">保存</button>
                    <button class="text-button" @click="cancelEdit(block)">取消</button>
                  </template>
                  <button v-if="block.check?.status === 'flag'" class="text-button" :disabled="!!job" @click="fixBlock(block)">改这一段</button>
                  <button v-if="block.check?.status === 'flag'" class="text-button" @click="keepBlock(block)">就这样</button>
                  <button v-if="block.prevText" class="text-button" :disabled="!!job" @click="revertBlock(block)">退回上一版</button>
                </span>
              </div>
              <textarea
                v-if="editing[block.id] !== undefined"
                v-model="editing[block.id]"
                class="block-editor"
                rows="6"
                spellcheck="false"
              />
              <pre v-else class="block-text">{{ block.text }}</pre>
            </li>
          </ol>
          <PaperLayoutPreview
            v-else
            :article="article"
            :options="layoutOptions"
            :active="sheetMode === 'layout'"
          />
          <p v-if="sheetMode === 'paper' && !article.blocks.length" class="empty">
            还没有正文。右侧「质检」页可以点「让模型写正文」，或粘贴 Markdown 导入。
          </p>
        </template>
        <p v-else class="empty">左侧选一篇稿件。</p>
      </main>

      <aside class="paper-side">
        <div class="tabs">
          <button
            v-for="item in [['chat', '对话'], ['quality', '质检'], ['titles', '标题'], ['layout', '排版'], ['media', '素材'], ['publish', '发布']]"
            :key="item[0]"
            :class="{ active: tab === item[0] }"
            @click="tab = item[0]"
          >{{ item[1] }}</button>
        </div>

        <section v-if="tab === 'chat'" class="panel">
          <div class="chat-log">
            <div v-for="(turn, index) in article?.chat || []" :key="index" class="chat-turn" :class="turn.role">
              <p>{{ turn.text }}</p>
              <ul v-if="turn.receipts?.length" class="receipts">
                <li v-for="(receipt, i) in turn.receipts" :key="i">{{ receipt }}</li>
              </ul>
            </div>
            <p v-if="!(article?.chat || []).length" class="empty">用一句话说改哪里，比如「第二段太像总结，改成具体场景」。</p>
          </div>
          <NInput v-model:value="message" type="textarea" :rows="3" placeholder="想怎么改？只改你点名的段落" @keydown.ctrl.enter="sendChat" />
          <div class="panel-actions">
            <span class="hint">Ctrl + Enter 发送</span>
            <NButton size="small" type="primary" :disabled="!article || !!job" :loading="actionBusy === 'chat'" @click="sendChat">发送</NButton>
          </div>
        </section>

        <section v-else-if="tab === 'quality'" class="panel">
          <div class="quality-head">
            <div>
              <strong>{{ sourceLabel }}</strong>
              <span v-if="article?.zhuque?.round">第 {{ article.zhuque.round }} 轮 · {{ flagged }} 段待改 · {{ fixedCount }} 段已改</span>
              <span v-else>还没有检测记录</span>
            </div>
            <NButton size="tiny" :disabled="!article" :loading="actionBusy === 'detect'" @click="detect">检测</NButton>
          </div>
          <p v-if="article?.zhuque?.note" class="hint">{{ article.zhuque.note }}</p>
          <details v-if="!settings.zhuqueConfigured" class="report-box">
            <summary>配置朱雀 API Key（不配则走本地规则）</summary>
            <NInput v-model:value="qualityKey" size="small" type="password" show-password-on="click" placeholder="粘贴朱雀 API Key" />
            <NInput v-model:value="qualityEndpoint" size="small" :placeholder="settings.zhuqueEndpoint || '接口地址（可留空用默认）'" />
            <NButton size="tiny" :loading="actionBusy === 'quality-settings'" @click="saveQuality">保存</NButton>
          </details>
          <p v-else class="hint">朱雀 Key 已配置（{{ settings.zhuqueKeyHint }}）。检测会优先调用朱雀接口。</p>

          <div class="panel-row">
            <NButton size="small" :disabled="!article || !!job || !flagged" :loading="actionBusy === 'fixAll'" @click="fixAll">
              一键处理 {{ flagged ? `${flagged} 段` : '' }}
            </NButton>
            <NButton v-if="job?.kind === 'fixall'" size="small" @click="stop">停止</NButton>
          </div>

          <details class="report-box">
            <summary>用朱雀报告回填（PDF 或文字）</summary>
            <input type="file" accept="application/pdf" @change="pickReport" />
            <NInput v-model:value="reportText" type="textarea" :rows="4" placeholder="或者把报告文字粘贴在这里" />
            <NButton size="tiny" :disabled="!article || !!job" :loading="actionBusy === 'report'" @click="submitReport">回填</NButton>
          </details>
        </section>

        <section v-else-if="tab === 'titles'" class="panel">
          <NSelect
            v-if="titleOptions.length"
            :value="article?.title"
            :options="titleOptions"
            size="small"
            placeholder="从候选里挑一个"
            @update:value="pickTitle"
          />
          <ul class="title-list">
            <li v-for="(item, index) in article?.titles || []" :key="index" :class="{ active: item.text === article?.title }">
              <button class="title-pick" @click="pickTitle(item.text)">
                <span>{{ item.text }}</span>
                <em v-if="item.hook">{{ item.hook }}</em>
              </button>
              <p v-if="item.reason" class="hint">{{ item.reason }}</p>
            </li>
          </ul>
          <p v-if="!(article?.titles || []).length" class="empty">还没有候选标题。</p>
          <div class="panel-actions">
            <NButton size="small" :disabled="!article || !!job" :loading="actionBusy === 'titles'" @click="regenTitles">重新出 5 个</NButton>
          </div>
        </section>

        <section v-else-if="tab === 'layout'" class="panel">
          <PaperLayoutPanel
            :article="article"
            :busy="actionBusy"
            @change="onLayoutChange"
            @saved="applyPanelSnapshot"
            @notify="notifyPanel"
          />
          <details class="report-box">
            <summary>开头横幅（配色跟随上面选的排版主题）</summary>
            <label class="field">
              <span>横幅风格</span>
              <NSelect
                v-model:value="bannerStyle"
                size="small"
                :options="[{ label: '大字海报', value: 'poster' }, { label: '标题卡', value: 'card' }, { label: '动画场景', value: 'scene' }, { label: '不要横幅', value: 'none' }]"
              />
            </label>
            <div class="panel-actions">
              <NButton size="small" :disabled="!article || !!job || bannerStyle === 'none'" :loading="actionBusy === 'banner'" @click="makeBanner">
                生成横幅规格
              </NButton>
            </div>
            <ul v-if="bannerWarnings.length" class="warnings">
              <li v-for="(warning, index) in bannerWarnings" :key="index">{{ warning }}</li>
            </ul>
            <p v-else-if="article?.banners?.[article?.bannerStyle]?.spec" class="hint">这一档横幅规格已就绪，排版预览里会出现在正文最前面。</p>
            <p v-else class="hint">按正文内容生成开头横幅规格；配色跟随排版主题。</p>
          </details>
        </section>

        <section v-else-if="tab === 'media'" class="panel">
          <PaperMediaPanel
            :article="article"
            :selected-block-id="selectedBlockId"
            :busy="actionBusy"
            @saved="applyPanelSnapshot"
            @notify="notifyPanel"
          />
        </section>

        <section v-else class="panel">
          <PaperPublishPanel
            :article="article"
            :busy="actionBusy"
            @saved="applyPanelSnapshot"
            @notify="notifyPanel"
          />
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.paper-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
  min-height: 100%;
  color: var(--color-text-primary, #1c1c1c);
}
.paper-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.eyebrow { margin: 0 0 6px; font-size: 11px; letter-spacing: .18em; color: var(--color-text-tertiary, #8a8a8a); }
.paper-head h1 { margin: 0; font-size: 26px; font-weight: 650; }
.paper-head h1 span { margin-left: 10px; font-size: 13px; font-weight: 400; color: var(--color-text-tertiary, #8a8a8a); }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.paper-alert {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border-radius: 10px;
  background: var(--paper-accent-tint, #f4f0e6); border: 1px solid var(--paper-accent-soft, #e6ddc8);
  font-size: 13px;
}
.paper-alert.error { background: var(--paper-danger-soft, #fdeceb); border-color: var(--paper-danger-line, #f3c2be); color: var(--paper-danger, #a3271f); }

.paper-grid {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1180px) { .paper-grid { grid-template-columns: 1fr; } }

.paper-list { display: flex; flex-direction: column; gap: 6px; position: sticky; top: 16px; }
.list-item {
  display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; text-align: left;
  border: 1px solid var(--color-border, #e5e5e5); border-radius: 10px; background: var(--color-surface, #fff);
  cursor: pointer; color: inherit;
}
.list-item.active { border-color: var(--paper-accent, #b08d57); background: var(--paper-accent-tint, #f7f3e8); }
.list-title { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.list-meta { font-size: 11px; color: var(--color-text-tertiary, #8a8a8a); display: flex; gap: 6px; align-items: center; }

.create-form { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px dashed var(--paper-line-strong, #d8cdb6); border-radius: 10px; background: var(--paper-header, #fbf8f1); }
.create-actions { display: flex; justify-content: flex-end; }

.paper-sheet {
  background: var(--paper-bg, #fffdf8);
  border: 1px solid var(--paper-line, #ece3d3);
  border-radius: 14px;
  padding: 22px 26px 30px;
  min-height: 520px;
  box-shadow: 0 12px 30px rgba(80, 62, 30, .05);
}
.sheet-head { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
.sheet-switch { display: flex; align-items: center; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
.sheet-switch button {
  padding: 4px 14px; font-size: 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--paper-line-strong, #d8cdb6); background: transparent;
  color: var(--color-text-secondary, #555);
}
.sheet-switch button.active {
  background: var(--paper-accent, #b08d57); border-color: var(--paper-accent, #b08d57); color: #fff;
}
.sheet-switch .hint { margin-left: 4px; }
.sheet-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: 11px; color: var(--color-text-tertiary, #8a8a8a); align-items: center; }
.ready-chip { padding: 1px 8px; border-radius: 999px; border: 1px solid var(--paper-line, #ece3d3); }
.ready-chip.done { color: var(--paper-success, #3f7a4f); border-color: var(--paper-success, #3f7a4f); }

.job-bar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; margin-bottom: 12px; border-radius: 8px; background: var(--paper-accent-tint, #f7f3e8); font-size: 12px; }
.job-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--paper-accent, #b08d57); animation: pulse 1.2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }

.blocks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.block { position: relative; padding: 10px 12px 10px 44px; border-radius: 8px; border: 1px solid transparent; }
.block:hover { background: var(--paper-hover, #faf6ec); }
.block.flag { background: var(--paper-danger-soft, #fdf1f0); border-color: var(--paper-danger-line, #f3c2be); }
.block.fixed { background: var(--paper-accent-tint, #fbf6e9); border-color: var(--paper-accent-soft, #e6ddc8); }
.block.editing { background: var(--color-surface, #fff); border-color: var(--paper-accent, #b08d57); }
.block.selected { background: var(--paper-accent-tint, #f7f3e8); border-color: var(--paper-accent-soft, #e6ddc8); }
.block-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 11px; color: var(--color-text-tertiary, #8a8a8a); }
.block-no { position: absolute; left: 12px; top: 10px; font-variant-numeric: tabular-nums; font-weight: 600; color: var(--paper-ink-soft, #9a8f7a); }
.block-kind { letter-spacing: .04em; }
.block-actions { margin-left: auto; display: flex; gap: 8px; opacity: 0; transition: opacity .15s; }
.block:hover .block-actions, .block.flag .block-actions { opacity: 1; }
.block-text { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: var(--font-body, inherit); font-size: 15px; line-height: 1.85; color: var(--paper-ink, #2b2b2b); }
.block-editor {
  width: 100%; box-sizing: border-box; resize: vertical; padding: 10px 12px;
  font-family: var(--font-body, inherit); font-size: 15px; line-height: 1.8;
  border: 1px solid var(--paper-line-strong, #d8cdb6); border-radius: 8px; background: #fff; color: inherit;
}

.paper-side { display: flex; flex-direction: column; gap: 10px; position: sticky; top: 16px; }
.tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; padding: 4px; border-radius: 10px; background: var(--color-surface-muted, #f2f2f2); }
.tabs button { padding: 6px 4px; font-size: 12px; border: none; border-radius: 7px; background: transparent; cursor: pointer; color: var(--color-text-secondary, #555); }
.tabs button.active { background: var(--color-surface, #fff); color: var(--color-text-primary, #111); box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.panel { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px solid var(--color-border, #e5e5e5); border-radius: 12px; background: var(--color-surface, #fff); }
.panel-actions, .panel-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }

.chat-log { display: flex; flex-direction: column; gap: 8px; max-height: 340px; overflow: auto; }
.chat-turn { padding: 8px 10px; border-radius: 8px; font-size: 13px; line-height: 1.6; background: var(--color-surface-muted, #f5f5f5); }
.chat-turn.user { background: var(--paper-accent-tint, #f7f3e8); }
.chat-turn p { margin: 0; }
.receipts { margin: 6px 0 0; padding-left: 16px; font-size: 11px; color: var(--color-text-tertiary, #8a8a8a); }

.quality-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; font-size: 12px; }
.quality-head div { display: flex; flex-direction: column; gap: 2px; }
.quality-head span { color: var(--color-text-tertiary, #8a8a8a); }

.report-box { display: flex; flex-direction: column; gap: 8px; font-size: 12px; }
.report-box summary { cursor: pointer; color: var(--color-text-secondary, #555); }
.title-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.title-list li { padding: 8px 10px; border: 1px solid var(--color-border, #e5e5e5); border-radius: 8px; }
.title-list li.active { border-color: var(--paper-accent, #b08d57); background: var(--paper-accent-tint, #f7f3e8); }
.title-pick { display: flex; justify-content: space-between; gap: 8px; width: 100%; border: none; background: none; font-size: 13px; text-align: left; cursor: pointer; color: inherit; }
.title-pick em { font-style: normal; color: var(--paper-accent, #b08d57); }
.warnings { margin: 0; padding-left: 16px; font-size: 12px; color: var(--paper-danger, #a3271f); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }

.badge { padding: 1px 7px; border-radius: 999px; font-size: 10px; border: 1px solid currentColor; }
.badge.flag { color: var(--paper-danger, #a3271f); }
.badge.fixed, .badge.danger { color: var(--color-warning, #b26a00); }
.badge.pass { color: var(--paper-success, #3f7a4f); }
.badge.kept { color: var(--color-text-tertiary, #8a8a8a); }
.hint { margin: 0; font-size: 11px; color: var(--color-text-tertiary, #8a8a8a); line-height: 1.6; }
.empty { font-size: 12px; color: var(--color-text-tertiary, #8a8a8a); }
.sheet-loading { font-size: 13px; color: var(--color-text-tertiary, #8a8a8a); }
.text-button { border: none; background: none; padding: 0; font-size: 12px; color: var(--paper-accent, #b08d57); cursor: pointer; }
.text-button:disabled { color: var(--color-text-tertiary, #aaa); cursor: not-allowed; }
</style>
