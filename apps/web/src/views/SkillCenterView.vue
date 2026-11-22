<template>
  <div class="skill-center">
    <header class="sc-head">
      <div>
        <h1>技能中心</h1>
        <p class="sc-sub">沉淀写作风格、人物思维与领域知识；宠物与改稿工具按需调用。</p>
      </div>
      <div class="sc-head-actions">
        <button class="btn" type="button" @click="openImport">导入</button>
        <button class="btn" type="button" :disabled="!skills.length" @click="exportAll">导出全部</button>
        <button class="btn btn-primary" type="button" @click="openCreate">新建技能</button>
      </div>
    </header>

    <div class="sc-toolbar">
      <div class="sc-tabs">
        <button
          v-for="tab in typeTabs"
          :key="tab.value"
          type="button"
          :class="['sc-tab', { active: activeType === tab.value }]"
          @click="activeType = tab.value"
        >
          {{ tab.label }}<span class="sc-count">{{ countOf(tab.value) }}</span>
        </button>
      </div>
      <input v-model="keyword" class="sc-search" type="search" placeholder="搜索名称或定位" />
    </div>

    <p v-if="notice" class="sc-notice" :class="noticeKind">{{ notice }}</p>
    <p v-if="loading" class="sc-empty">加载中…</p>
    <p v-else-if="!filtered.length" class="sc-empty">
      还没有技能。点「新建技能」手动创建，或用「资料蒸馏」从样文、链接里提炼。
    </p>

    <div v-else class="sc-grid">
      <article v-for="skill in filtered" :key="skill.id" class="sc-card">
        <div class="sc-card-top">
          <span class="sc-badge" :class="`type-${skill.skillType}`">{{ typeLabel(skill.skillType) }}</span>
          <span class="sc-source">{{ sourceLabel(skill.source) }}</span>
        </div>
        <h3 class="sc-name">{{ skill.name }}</h3>
        <p class="sc-summary">{{ skill.summary || skill.description || '暂无定位说明' }}</p>
        <dl class="sc-meta">
          <div><dt>规则</dt><dd>{{ contentCount(skill) }}</dd></div>
          <div><dt>版本</dt><dd>v{{ skill.version }}</dd></div>
          <div><dt>使用</dt><dd>{{ skill.usageCount }}</dd></div>
        </dl>
        <div class="sc-card-actions">
          <button class="link" type="button" @click="openEdit(skill)">编辑</button>
          <button class="link" type="button" @click="duplicate(skill)">复制</button>
          <button class="link" type="button" @click="exportOne(skill)">导出</button>
          <button class="link danger" type="button" @click="remove(skill)">删除</button>
        </div>
      </article>
    </div>

    <!-- 新建 / 编辑 -->
    <div v-if="editorOpen" class="sc-drawer-mask" @click.self="closeEditor">
      <section class="sc-drawer" role="dialog" aria-modal="true" :aria-label="form.id ? '编辑技能' : '新建技能'">
        <header class="sc-drawer-head">
          <h2>{{ form.id ? '编辑技能' : '新建技能' }}</h2>
          <button class="close" type="button" aria-label="关闭" @click="closeEditor">×</button>
        </header>
        <div class="sc-form">
          <label class="field">
            <span>名称</span>
            <input v-model="form.name" maxlength="80" placeholder="例如：我的公众号短句风格" />
          </label>
          <label class="field">
            <span>类型</span>
            <select v-model="form.skillType" :disabled="!!form.id">
              <option value="writing">写作风格</option>
              <option value="persona">人物思维</option>
              <option value="knowledge">领域知识</option>
            </select>
          </label>
          <label class="field">
            <span>一句话定位</span>
            <input v-model="form.summary" maxlength="400" placeholder="选它时你期望得到什么" />
          </label>
          <label class="field">
            <span>使用说明</span>
            <textarea v-model="form.description" rows="2" maxlength="4000" placeholder="适用场景、边界、注意事项" />
          </label>

          <template v-if="form.skillType === 'writing'">
            <label class="field">
              <span>语气</span>
              <input v-model="form.tone" maxlength="300" placeholder="例如：克制、短句、不煽情" />
            </label>
            <label class="field">
              <span>写作规则（一行一条，3 条以上更稳定）</span>
              <textarea v-model="form.rules" rows="5" placeholder="先给结论&#10;每段只讲一件事&#10;数据必须可核实" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>开头偏好（一行一条）</span>
                <textarea v-model="form.openings" rows="3" />
              </label>
              <label class="field">
                <span>结构偏好（一行一条）</span>
                <textarea v-model="form.structures" rows="3" />
              </label>
            </div>
            <div class="field-row">
              <label class="field">
                <span>优先用词（逗号分隔）</span>
                <input v-model="form.vocabulary" placeholder="拆解、落地、复盘" />
              </label>
              <label class="field">
                <span>禁用表达（逗号分隔）</span>
                <input v-model="form.taboos" placeholder="赋能、闭环" />
              </label>
            </div>
            <label class="field">
              <span>素材使用原则</span>
              <textarea v-model="form.materialPolicy" rows="2" placeholder="只写可核实的数据，不编造来源" />
            </label>
          </template>

          <template v-else-if="form.skillType === 'persona'">
            <label class="field">
              <span>心智模型（一行一条：名称 | 解释 | 应用）</span>
              <textarea v-model="form.mentalModels" rows="5" placeholder="逆向思考 | 先想怎么会失败 | 投资决策前先列失败清单" />
            </label>
            <label class="field">
              <span>决策启发（一行一条）</span>
              <textarea v-model="form.heuristics" rows="3" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>价值取向（一行一条）</span>
                <textarea v-model="form.values" rows="3" />
              </label>
              <label class="field">
                <span>诚实边界（一行一条）</span>
                <textarea v-model="form.boundaries" rows="3" placeholder="材料未覆盖的领域不强行给结论" />
              </label>
            </div>
            <div class="field-row">
              <label class="field">
                <span>表达风格</span>
                <input v-model="form.expressionStyle" maxlength="300" />
              </label>
              <label class="field">
                <span>表达语气</span>
                <input v-model="form.expressionTone" maxlength="300" />
              </label>
            </div>
          </template>

          <template v-else>
            <label class="field">
              <span>知识概览</span>
              <textarea v-model="form.overview" rows="3" />
            </label>
            <label class="field">
              <span>能力卡（一行一条：名称 | 认知 | 应用 | 实例用分号隔开）</span>
              <textarea v-model="form.capabilities" rows="5" placeholder="需求拆解 | 先分角色再分场景 | 写前列三条验收 | 案例A;案例B" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>术语表（一行一条：术语 | 定义）</span>
                <textarea v-model="form.glossary" rows="3" />
              </label>
              <label class="field">
                <span>案例（一行一条：标题 | 摘要）</span>
                <textarea v-model="form.cases" rows="3" />
              </label>
            </div>
            <label class="field">
              <span>反例（一行一条）</span>
              <textarea v-model="form.counterexamples" rows="3" />
            </label>
          </template>

          <!-- 新建时可直接蒸馏：填了资料就先建技能，再蒸馏写入；不填则只新建 -->
          <template v-if="!form.id">
            <label class="field">
              <span>资料蒸馏（可选，新建时一并提炼）</span>
              <textarea
                v-model="distillForm.materials"
                rows="4"
                placeholder="第一篇文章全文…&#10;---&#10;第二篇文章全文…（至少 200 字）"
              />
            </label>
            <label class="field">
              <span>参考链接（可选，一行一个）</span>
              <textarea v-model="distillForm.urls" rows="2" placeholder="https://example.com/article" />
            </label>
            <label v-if="form.skillType === 'persona'" class="field">
              <span>人物名（可选）</span>
              <input v-model="distillForm.personName" maxlength="80" />
            </label>
          </template>

          <p v-if="formError" class="sc-error">{{ formError }}</p>
        </div>
        <footer class="sc-drawer-foot">
          <button class="btn" type="button" @click="closeEditor">取消</button>
          <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </footer>
      </section>
    </div>

    <!-- 资料蒸馏 -->
    <div v-if="distillTarget" class="sc-drawer-mask" @click.self="closeDistill">
      <section class="sc-drawer" role="dialog" aria-modal="true" aria-label="资料蒸馏">
        <header class="sc-drawer-head">
          <h2>资料蒸馏 · {{ distillTarget.name }}</h2>
          <button class="close" type="button" aria-label="关闭" @click="closeDistill">×</button>
        </header>
        <div class="sc-form">
          <p class="sc-hint">
            投喂代表作、人物材料或知识原文，系统按「{{ typeLabel(distillTarget.skillType) }}」提炼。
            资料越充分越稳定，少于 200 字会被拒绝；蒸馏结果会覆盖当前技能内容并保留历史版本。
          </p>
          <label class="field">
            <span>粘贴材料（多篇用单独一行 --- 分隔）</span>
            <textarea v-model="distillForm.materials" rows="8" placeholder="第一篇文章全文…&#10;---&#10;第二篇文章全文…" />
          </label>
          <label class="field">
            <span>公开链接（一行一个，可选）</span>
            <textarea v-model="distillForm.urls" rows="3" placeholder="https://example.com/article" />
          </label>
          <label v-if="distillTarget.skillType === 'persona'" class="field">
            <span>人物名（可选）</span>
            <input v-model="distillForm.personName" maxlength="80" />
          </label>
          <p v-if="distillError" class="sc-error">{{ distillError }}</p>
          <p v-if="distillResult" class="sc-success">
            蒸馏完成：提炼出 {{ distillCount }} 条内容，已写入技能。
          </p>
        </div>
        <footer class="sc-drawer-foot">
          <button class="btn" type="button" @click="closeDistill">取消</button>
          <button class="btn btn-primary" type="button" :disabled="distilling" @click="runDistill">
            {{ distilling ? '蒸馏中…' : '开始蒸馏' }}
          </button>
        </footer>
      </section>
    </div>

    <!-- 导入 -->
    <div v-if="importOpen" class="sc-drawer-mask" @click.self="importOpen = false">
      <section class="sc-drawer" role="dialog" aria-modal="true" aria-label="导入技能">
        <header class="sc-drawer-head">
          <h2>导入技能</h2>
          <button class="close" type="button" aria-label="关闭" @click="importOpen = false">×</button>
        </header>
        <div class="sc-form">
          <p class="sc-hint">粘贴导出的技能 JSON（单张或整包）。同名技能不会覆盖，会自动改名。</p>
          <label class="field">
            <span>技能 JSON</span>
            <textarea v-model="importText" rows="10" placeholder='{"format":"hanyue.author-skill/v1", ...}' />
          </label>
          <p v-if="importError" class="sc-error">{{ importError }}</p>
        </div>
        <footer class="sc-drawer-foot">
          <button class="btn" type="button" @click="importOpen = false">取消</button>
          <button class="btn btn-primary" type="button" :disabled="importing" @click="runImport">
            {{ importing ? '导入中…' : '导入' }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, downloadAuthenticated } from '../api/client'

const TYPE_LABELS = { writing: '写作风格', persona: '人物思维', knowledge: '领域知识' }
const SOURCE_LABELS = { manual: '手动', distilled: '蒸馏', imported: '导入' }
const typeTabs = [
  { value: '', label: '全部' },
  { value: 'writing', label: '写作风格' },
  { value: 'persona', label: '人物思维' },
  { value: 'knowledge', label: '领域知识' },
]

const skills = ref([])
const loading = ref(true)
const activeType = ref('')
const keyword = ref('')
const notice = ref('')
const noticeKind = ref('')
const saving = ref(false)
const formError = ref('')

const editorOpen = ref(false)
const form = reactive(emptyForm())

const distillTarget = ref(null)
const distillForm = reactive({ materials: '', urls: '', personName: '' })
const distilling = ref(false)
const distillError = ref('')
const distillResult = ref(null)

const importOpen = ref(false)
const importText = ref('')
const importError = ref('')
const importing = ref(false)

function emptyForm() {
  return {
    id: '',
    name: '',
    skillType: 'writing',
    summary: '',
    description: '',
    tone: '',
    rules: '',
    openings: '',
    structures: '',
    vocabulary: '',
    taboos: '',
    materialPolicy: '',
    mentalModels: '',
    heuristics: '',
    values: '',
    boundaries: '',
    expressionStyle: '',
    expressionTone: '',
    overview: '',
    capabilities: '',
    glossary: '',
    cases: '',
    counterexamples: '',
  }
}

function typeLabel(value) {
  return TYPE_LABELS[value] || value
}
function sourceLabel(value) {
  return SOURCE_LABELS[value] || value
}
function lines(text) {
  return String(text || '')
    .split('\n')
    .map((row) => row.trim())
    .filter(Boolean)
}
function csv(text) {
  return String(text || '')
    .split(/[,，、]/)
    .map((row) => row.trim())
    .filter(Boolean)
}
function pairs(text, keys) {
  return lines(text).map((row) => {
    const cells = row.split('|').map((cell) => cell.trim())
    const item = {}
    keys.forEach((key, index) => {
      item[key] = cells[index] || ''
    })
    return item
  })
}
function pairsWithList(text, keys) {
  return lines(text).map((row) => {
    const cells = row.split('|').map((cell) => cell.trim())
    const item = {}
    keys.forEach((key, index) => {
      item[key] = key === 'examples' ? (cells[index] || '').split(';').map((v) => v.trim()).filter(Boolean) : cells[index] || ''
    })
    return item
  })
}
function joinPairs(items, keys) {
  return (items || [])
    .map((item) => keys.map((key) => (Array.isArray(item[key]) ? item[key].join(';') : item[key] || '')).join(' | '))
    .join('\n')
}

function countOf(type) {
  return type ? skills.value.filter((skill) => skill.skillType === type).length : skills.value.length
}
function contentCount(skill) {
  const config = skill.config || {}
  if (skill.skillType === 'writing') return (config.styleRules || []).length
  if (skill.skillType === 'persona') return (config.mentalModels || []).length
  return (config.capabilities || []).length
}

const filtered = computed(() => {
  const need = keyword.value.trim().toLowerCase()
  return skills.value.filter((skill) => {
    if (activeType.value && skill.skillType !== activeType.value) return false
    if (!need) return true
    return `${skill.name} ${skill.summary} ${skill.description}`.toLowerCase().includes(need)
  })
})

function flash(message, kind = 'ok') {
  notice.value = message
  noticeKind.value = kind
  setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 4000)
}

async function load() {
  loading.value = true
  try {
    skills.value = await api.get('/skill-center/skills')
  } catch (error) {
    flash(error.message || '加载技能失败', 'error')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, emptyForm())
  formError.value = ''
  editorOpen.value = true
}

function openEdit(skill) {
  Object.assign(form, emptyForm())
  const config = skill.config || {}
  form.id = skill.id
  form.name = skill.name
  form.skillType = skill.skillType
  form.summary = skill.summary || ''
  form.description = skill.description || ''
  if (skill.skillType === 'writing') {
    form.tone = config.tone || ''
    form.rules = (config.styleRules || []).join('\n')
    form.openings = (config.openings || []).join('\n')
    form.structures = (config.structures || []).join('\n')
    form.vocabulary = (config.vocabulary || []).join('、')
    form.taboos = (config.taboos || []).join('、')
    form.materialPolicy = config.materialPolicy || ''
  } else if (skill.skillType === 'persona') {
    form.mentalModels = joinPairs(config.mentalModels, ['name', 'description', 'application'])
    form.heuristics = (config.heuristics || []).join('\n')
    form.values = (config.values || []).join('\n')
    form.boundaries = (config.boundaries || []).join('\n')
    form.expressionStyle = config.expression?.style || ''
    form.expressionTone = config.expression?.tone || ''
  } else {
    form.overview = config.overview || ''
    form.capabilities = joinPairs(config.capabilities, ['name', 'cognition', 'application', 'examples'])
    form.glossary = joinPairs(config.glossary, ['term', 'definition'])
    form.cases = joinPairs(config.cases, ['title', 'summary'])
    form.counterexamples = (config.counterexamples || []).join('\n')
  }
  formError.value = ''
  editorOpen.value = true
}

function closeEditor() {
  editorOpen.value = false
}

function buildConfig() {
  if (form.skillType === 'writing') {
    return {
      tone: form.tone,
      styleRules: lines(form.rules),
      openings: lines(form.openings),
      structures: lines(form.structures),
      vocabulary: csv(form.vocabulary),
      taboos: csv(form.taboos),
      materialPolicy: form.materialPolicy,
    }
  }
  if (form.skillType === 'persona') {
    return {
      mentalModels: pairs(form.mentalModels, ['name', 'description', 'application']),
      heuristics: lines(form.heuristics),
      values: lines(form.values),
      boundaries: lines(form.boundaries),
      expression: { style: form.expressionStyle, tone: form.expressionTone },
    }
  }
  return {
    overview: form.overview,
    capabilities: pairsWithList(form.capabilities, ['name', 'cognition', 'application', 'examples']),
    glossary: pairs(form.glossary, ['term', 'definition']),
    cases: pairs(form.cases, ['title', 'summary']),
    counterexamples: lines(form.counterexamples),
  }
}

async function save() {
  if (!form.name.trim()) {
    formError.value = '请填写技能名称'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    const config = buildConfig()
    if (form.id) {
      await api.put(`/skill-center/skills/${form.id}`, {
        name: form.name.trim(),
        summary: form.summary,
        description: form.description,
        config,
      })
      flash('技能已更新')
    } else {
      const created = await api.post('/skill-center/skills', {
        name: form.name.trim(),
        skillType: form.skillType,
        summary: form.summary,
        description: form.description,
        config,
      })
      // 新建时填了资料：技能已落库，接着用它的 id 蒸馏；失败只提示，技能本身保留
      const materials = (distillForm.materials || '').trim()
      const urls = lines(distillForm.urls)
      if (materials || urls.length) {
        try {
          const run = await api.post(`/skill-center/skills/${created.id}/distill`, {
            materials,
            urls,
            personName: distillForm.personName,
          })
          flash(`技能已创建，并蒸馏出 ${(run?.result?.styleRules || run?.result?.mentalModels || []).length || 0} 条内容`)
        } catch (error) {
          flash(`技能已创建，但蒸馏失败：${error.message || '未知错误'}`, 'error')
        }
      } else {
        flash('技能已创建')
      }
      Object.assign(distillForm, { materials: '', urls: '', personName: '' })
    }
    editorOpen.value = false
    await load()
  } catch (error) {
    formError.value = error.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function openDistill(skill) {
  distillTarget.value = skill
  Object.assign(distillForm, { materials: '', urls: '', personName: '' })
  distillError.value = ''
  distillResult.value = null
}

function closeDistill() {
  distillTarget.value = null
}

const distillCount = computed(() => {
  // 蒸馏接口返回的是任务记录，技能内容在 result 里
  const config = distillResult.value?.result || {}
  if (distillTarget.value?.skillType === 'writing') return (config.styleRules || []).length
  if (distillTarget.value?.skillType === 'persona') return (config.mentalModels || []).length
  return (config.capabilities || []).length
})

async function runDistill() {
  const materials = distillForm.materials
    .split(/\n\s*---\s*\n/)
    .map((item) => item.trim())
    .filter(Boolean)
  const urls = lines(distillForm.urls)
  if (!materials.length && !urls.length) {
    distillError.value = '请粘贴材料或提供公开链接'
    return
  }
  distilling.value = true
  distillError.value = ''
  distillResult.value = null
  try {
    const run = await api.post(`/skill-center/skills/${distillTarget.value.id}/distill`, {
      materials,
      urls,
      personName: distillForm.personName,
    })
    distillResult.value = run
    flash('蒸馏完成，技能已更新')
    await load()
  } catch (error) {
    distillError.value = error.message || '蒸馏失败'
  } finally {
    distilling.value = false
  }
}

async function duplicate(skill) {
  try {
    await api.post(`/skill-center/skills/${skill.id}/duplicate`, {})
    flash(`已复制「${skill.name}」`)
    await load()
  } catch (error) {
    flash(error.message || '复制失败', 'error')
  }
}

async function remove(skill) {
  if (!window.confirm(`删除技能「${skill.name}」？历史版本会一并删除，已启用它的对话不受影响。`)) return
  try {
    await api.del(`/skill-center/skills/${skill.id}`)
    flash('技能已删除')
    await load()
  } catch (error) {
    flash(error.message || '删除失败', 'error')
  }
}

async function exportOne(skill) {
  try {
    await downloadAuthenticated(`/skill-center/skills/${skill.id}/export`, `${skill.name}.skill.json`)
  } catch (error) {
    flash(error.message || '导出失败', 'error')
  }
}

async function exportAll() {
  try {
    await downloadAuthenticated('/skill-center/export', 'author-skills.skill.json')
  } catch (error) {
    flash(error.message || '导出失败', 'error')
  }
}

function openImport() {
  importText.value = ''
  importError.value = ''
  importOpen.value = true
}

async function runImport() {
  let payload
  try {
    payload = JSON.parse(importText.value)
  } catch {
    importError.value = 'JSON 格式不正确'
    return
  }
  importing.value = true
  importError.value = ''
  try {
    const created = await api.post('/skill-center/import', payload)
    importOpen.value = false
    flash(`已导入 ${created.length} 个技能`)
    await load()
  } catch (error) {
    importError.value = error.message || '导入失败'
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.skill-center { display: flex; flex-direction: column; gap: 14px; padding: 18px 22px 32px; color: var(--color-text-primary); }
.sc-head { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px; }
.sc-head h1 { margin: 0; font-size: 20px; }
.sc-sub { margin: 4px 0 0; max-width: 640px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.sc-head-actions { display: flex; gap: 8px; }
.btn { padding: 6px 14px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-text-primary); font-size: 12px; cursor: pointer; }
.btn-primary { border-color: var(--color-accent); background: var(--color-accent); color: var(--color-on-accent, #fff); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.sc-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.sc-tabs { display: flex; gap: 4px; padding: 3px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface-muted); }
.sc-tab { display: flex; align-items: center; gap: 6px; padding: 5px 12px; border: 0; border-radius: 999px; background: transparent; color: var(--color-text-secondary); font-size: 12px; cursor: pointer; }
.sc-tab.active { background: var(--color-surface); color: var(--color-text-primary); box-shadow: var(--shadow-sm); }
.sc-count { padding: 0 5px; border-radius: 8px; background: var(--color-surface-muted); font-size: 11px; }
.sc-search { width: 220px; height: 34px; padding: 0 14px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-text-primary); font-size: 12px; }
.sc-notice { margin: 0; padding: 8px 12px; border: 1px solid var(--color-border); border-left: 3px solid var(--color-accent); border-radius: 10px; background: var(--color-surface); font-size: 12px; }
.sc-notice.error { border-left-color: var(--color-danger, #dc2626); }
.sc-empty { padding: 40px 12px; border: 1px dashed var(--color-border); border-radius: 16px; color: var(--color-text-secondary); font-size: 13px; text-align: center; }
.sc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.sc-card { display: flex; flex-direction: column; gap: 8px; padding: 16px; border: 1px solid var(--color-border); border-radius: 16px; background: var(--color-surface); transition: box-shadow .18s ease, transform .18s ease; }
.sc-card:hover { box-shadow: var(--shadow-md); transform: translateY(-1px); }
.sc-card-top { display: flex; align-items: center; justify-content: space-between; }
.sc-badge { padding: 2px 7px; border-radius: 10px; font-size: 11px; }
.type-writing { background: var(--color-accent-soft); color: var(--color-accent); }
.type-persona { background: rgba(240, 160, 32, 0.16); color: var(--color-warning); }
.type-knowledge { background: var(--color-accent-soft); color: var(--color-accent); }
.sc-source { color: var(--color-text-secondary); font-size: 11px; }
.sc-name { margin: 0; font-size: 14px; }
.sc-summary { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.55; min-height: 34px; }
.sc-meta { display: flex; gap: 16px; margin: 0; font-size: 11px; }
.sc-meta div { display: flex; gap: 4px; }
.sc-meta dt { color: var(--color-text-secondary); }
.sc-meta dd { margin: 0; }
.sc-card-actions { display: flex; flex-wrap: wrap; gap: 10px; padding-top: 8px; border-top: 1px solid var(--color-border); }
.link { padding: 0; border: 0; background: none; color: var(--color-accent); font-size: 12px; cursor: pointer; }
.link.danger { color: var(--color-danger, #dc2626); }
.sc-drawer-mask { position: fixed; inset: 0; z-index: 60; display: flex; justify-content: flex-end; background: rgba(15, 23, 42, .28); backdrop-filter: blur(3px); }
.sc-drawer { display: flex; flex-direction: column; width: min(560px, 100vw); height: 100%; background: var(--color-surface); border-radius: 20px 0 0 20px; box-shadow: var(--shadow-lg); }
.sc-drawer-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--color-border); }
.sc-drawer-head h2 { margin: 0; font-size: 15px; }
.close { width: 28px; height: 28px; border: 0; border-radius: 50%; background: var(--color-surface-muted); color: var(--color-text-primary); font-size: 16px; cursor: pointer; }
.sc-form { display: flex; flex: 1; flex-direction: column; gap: 12px; overflow-y: auto; padding: 16px 18px; }
.sc-hint { margin: 0; padding: 8px 10px; border-radius: 4px; background: var(--color-surface-muted); color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.field { display: flex; flex-direction: column; gap: 5px; font-size: 12px; }
.field > span { color: var(--color-text-secondary); }
.field input, .field select, .field textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); color: var(--color-text-primary); font: inherit; font-size: 13px; }
.field textarea { resize: vertical; line-height: 1.55; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sc-error { margin: 0; color: var(--color-danger, #dc2626); font-size: 12px; }
.sc-success { margin: 0; color: var(--color-success); font-size: 12px; }
.sc-drawer-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--color-border); }
@media (max-width: 720px) {
  .skill-center { padding: 14px; }
  .sc-search { width: 100%; }
  .field-row { grid-template-columns: 1fr; }
}
</style>
