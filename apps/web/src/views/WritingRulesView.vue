<script setup>
/**
 * 创作规则页（计划 T5）：Skill 规则行（全局 / 个人副本）与全局提示词、词表的统一编辑入口。
 *
 * 职责边界：
 * - 本页只做「加载 / 保存 / 切换作用域 / 重建 / 预览」这些整体动作；
 *   单个规则行的编辑界面在 SkillRulesPanel，提示词与词表的界面在 PromptRulesPanel。
 * - 保存是整组 PUT：后端先校验全组再换行，失败时库不变（前端保留本地改动不清空）。
 * - 全局写入需要 writing_rules.manage；普通成员只能维护「我的副本」。
 *
 * R4 语义提醒：全局规则「清空后保存」在后端落成全部禁用而不是删行——
 * 删掉的行下次启动会被出厂导入重新补齐，所以界面上的「清空」等于「全部关掉」。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSpin,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'
import { usePermission } from '../composables/usePermission'
import { confirm, toast } from '../ui/feedback'
import PreviewDrawer from './writing-rules/PreviewDrawer.vue'
import PromptRulesPanel from './writing-rules/PromptRulesPanel.vue'
import SkillRulesPanel from './writing-rules/SkillRulesPanel.vue'

const { can } = usePermission()
const canManage = computed(() => can('writing_rules.manage'))
const route = useRoute()
const router = useRouter()

// ---------------------------------------------------------------- 列表与作用域

const loading = ref(true)
const error = ref('')
const skills = ref([])
const keyword = ref('')
const selectedKey = ref('')
const scope = ref('global')
const tab = ref('rules')

// 创作空间「流程」面板的 deep-link：?skill=&tab=rules|prompts&focus=&from=studio&item=
// focus 在 rules 页签下是 ruleType（review_rule / image_policy…），在 prompts 页签下是分类 key（tone_lexicon）。
const deepSkill = typeof route.query.skill === 'string' ? route.query.skill : ''
const focusType = typeof route.query.focus === 'string' ? route.query.focus : ''
const backFromStudio = route.query.from === 'studio'
if (route.query.tab === 'prompts' || route.query.tab === 'rules') tab.value = route.query.tab
if (route.query.scope === 'personal') scope.value = 'personal'

function goBackToStudio() {
  const query = { tab: 'flow' }
  if (typeof route.query.item === 'string' && route.query.item) query.id = route.query.item
  router.push({ name: 'studio', query })
}

// 分类仅保留为后端执行元数据，编辑入口统一展示所有 Skill。

function matchKeyword(item) {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return true
  return (
    (item.name || '').toLowerCase().includes(q) ||
    (item.skillKey || '').toLowerCase().includes(q)
  )
}

const visibleSkills = computed(() => skills.value.filter(matchKeyword))

async function loadSkills() {
  error.value = ''
  try {
    skills.value = await api.get('/writing-rules/skills')
    if (!selectedKey.value && skills.value.length) {
      const wanted = deepSkill && skills.value.find((item) => item.skillKey === deepSkill)
      selectedKey.value = (wanted || skills.value[0]).skillKey
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 规则行

const rows = ref([])
const counts = ref({})
const effectiveManifest = ref({})
const rulesLoading = ref(false)
const dirty = ref(false)

const readonly = computed(() => scope.value === 'global' && !canManage.value)
const enabledCount = computed(() => rows.value.filter((row) => row.enabled !== false).length)

async function loadRules() {
  if (!selectedKey.value) return
  rulesLoading.value = true
  error.value = ''
  try {
    const data = await api.get(
      `/writing-rules/skills/${encodeURIComponent(selectedKey.value)}?scope=${scope.value}`,
    )
    rows.value = data.rules || []
    counts.value = data.counts || {}
    effectiveManifest.value = data.effectiveManifest || {}
    dirty.value = false
  } catch (err) {
    error.value = err.message
    rows.value = []
  } finally {
    rulesLoading.value = false
  }
}

watch([selectedKey, scope], loadRules)

/** 预填占位产生的空规则不入库：「没填」等于「未配置、走继承」，不能落成空行。
 *  判定与后端校验对齐（services/writing_rules.py 的 _require_text / _check_single）：
 *  空文本、缺 value 的字数、空对象的策略会被后端整组拒绝，必须在这里提前丢弃。 */
function isEmptyRule(row) {
  const payload = row.payload || {}
  if (row.ruleType === 'tone' || row.ruleType === 'prompt_template' || row.ruleType === 'theme' || row.ruleType === 'style_rule') {
    return !String(payload.text || '').trim()
  }
  if (row.ruleType === 'target_length') return !Number.isInteger(payload.value)
  if (row.ruleType === 'workflow_step') return !String(payload.tool || '').trim()
  if (row.ruleType === 'exemplar') return !payload.title && !payload.excerpt
  if (row.ruleType === 'fact_policy' || row.ruleType === 'image_policy' || row.ruleType === 'repair_policy') {
    return !Object.keys(payload).length
  }
  return false
}
function onRowsChange(next) {
  rows.value = next
  dirty.value = true
}

async function switchScope(next) {
  if (next === scope.value) return
  if (dirty.value) {
    const ok = await confirm('当前有未保存的改动，切换作用域会丢弃它们。继续？', '未保存的改动')
    if (!ok) return
  }
  scope.value = next
}

async function switchSkill(key) {
  if (key === selectedKey.value) return
  if (dirty.value) {
    const ok = await confirm('当前有未保存的改动，切换 Skill 会丢弃它们。继续？', '未保存的改动')
    if (!ok) return
  }
  selectedKey.value = key
}

// 浏览器刷新/关闭时的兜底提醒：改动只在内存里，刷新即丢
function beforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => {
  loadSkills()
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

async function save() {
  if (!selectedKey.value) return
  error.value = ''
  const payload = {
    // 只提交服务端认识的字段，界面内部的临时字段（如 _localKey）不带上。
    // 预填后仍为空的行在这里丢弃，不入库。
    rules: rows.value.filter((row) => !isEmptyRule(row)).map((row) => ({
      ruleType: row.ruleType,
      ruleKey: row.ruleKey,
      payload: row.payload || {},
      sortOrder: row.sortOrder || 0,
      enabled: row.enabled !== false,
    })),
  }
  if (!payload.rules.length && scope.value === 'global') {
    const ok = await confirm(
      '全局规则不能真正清空：保存空内容等于「全部停用」，重启后这些行仍会存在但处于停用状态。继续？',
      '清空全局规则',
    )
    if (!ok) return
  }
  try {
    const data = await api.put(
      `/writing-rules/skills/${encodeURIComponent(selectedKey.value)}/rules?scope=${scope.value}`,
      payload,
    )
    rows.value = data.rules || []
    counts.value = data.counts || {}
    effectiveManifest.value = data.effectiveManifest || {}
    dirty.value = false
    toast(scope.value === 'global' ? '全局规则已保存，下次生成即生效' : '我的副本已保存')
    // 左侧列表的「全局 N · 个人 M」计数要跟着刷新
    await loadSkills()
  } catch (err) {
    // 后端整组校验失败时库不变，本地改动保留，用户可继续修正
    error.value = err.message
  }
}

async function resetToFactory() {
  const ok = await confirm(
    '将按出厂数据重建这个 Skill 的全部全局规则，管理员之前的修改会被覆盖，且无法撤销。',
    '按出厂重建',
  )
  if (!ok) return
  error.value = ''
  try {
    const data = await api.post(
      `/writing-rules/skills/${encodeURIComponent(selectedKey.value)}/reset`,
    )
    rows.value = data.rules || []
    counts.value = data.counts || {}
    effectiveManifest.value = data.effectiveManifest || {}
    dirty.value = false
    if (scope.value !== 'global') scope.value = 'global'
    toast('已按出厂数据重建全局规则')
    await loadSkills()
  } catch (err) {
    error.value = err.message
  }
}

// ---------------------------------------------------------------- 左侧 Skill 维护
//
// 只有 source=custom 的全局条目（管理员新建）可以改名/删除；
// 内置条目每次启动会被出厂定义覆盖，界面改了也会被打回，所以只读。

const showSkillModal = ref(false)
const editingSkill = ref(null) // null=新增；否则是被改名的条目
const skillForm = reactive({ skillKey: '', displayName: '', description: '' })
const skillSaving = ref(false)

function openCreate() {
  editingSkill.value = null
  skillForm.skillKey = ''
  skillForm.displayName = ''
  skillForm.description = ''
  showSkillModal.value = true
}

function openEdit() {
  if (!selectedSkill.value) return
  editingSkill.value = selectedSkill.value
  skillForm.skillKey = selectedSkill.value.skillKey
  skillForm.displayName = selectedSkill.value.name || ''
  skillForm.description = selectedSkill.value.description || ''
  showSkillModal.value = true
}

function validateSkillForm() {
  const name = skillForm.displayName.trim()
  if (!name) return '名称不能为空'
  if (!editingSkill.value) {
    const key = skillForm.skillKey.trim()
    if (key.length < 3) return '标识至少 3 个字符'
    if (/\s/.test(key)) return '标识不能包含空白字符'
  }
  return ''
}

async function submitSkillModal() {
  const invalid = validateSkillForm()
  if (invalid) {
    toast(invalid, 'warning')
    return
  }
  skillSaving.value = true
  error.value = ''
  try {
    if (editingSkill.value) {
      await api.patch(`/writing-rules/skills/${encodeURIComponent(editingSkill.value.skillKey)}`, {
        displayName: skillForm.displayName.trim(),
        description: skillForm.description,
      })
      toast('已保存')
    } else {
      const created = await api.post('/writing-rules/skills', {
        skillKey: skillForm.skillKey.trim(),
        displayName: skillForm.displayName.trim(),
        description: skillForm.description,
        // 兼容现有接口；分类不再作为用户需要选择的配置。
        category: 'style',
      })
      selectedKey.value = created.skillKey
      toast('已新增，规则行保存后下一次生成即生效')
    }
    showSkillModal.value = false
    await loadSkills()
  } catch (err) {
    error.value = err.message
  } finally {
    skillSaving.value = false
  }
}

async function removeSelectedSkill() {
  const skill = selectedSkill.value
  if (!skill) return
  const ok = await confirm(
    `将删除「${skill.name}」及其全部规则行（含成员的个人副本），删除后无法恢复。继续？`,
    '删除全局 Skill',
  )
  if (!ok) return
  error.value = ''
  try {
    await api.del(`/writing-rules/skills/${encodeURIComponent(skill.skillKey)}`)
    selectedKey.value = ''
    toast('已删除')
    await loadSkills()
  } catch (err) {
    error.value = err.message
  }
}

// ---------------------------------------------------------------- 预览

const showPreview = ref(false)

const selectedSkill = computed(() =>
  skills.value.find((item) => item.skillKey === selectedKey.value),
)

const scopeHint = computed(() =>
  scope.value === 'global'
    ? '全局规则对所有成员生效；修改需要「创作规则管理」权限。'
    : '我的副本只对当前账号生效：按类型覆盖全局，保存后立即用于你自己的生成。',
)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">创作规则</h1>
        <p class="page-sub">
          Skill 规则与全局提示词词表分开维护；规则按 Skill 生效，提示词与词表对整个创作系统生效。
        </p>
      </div>
      <div class="row" style="gap: var(--space-2)">
        <n-button v-if="tab === 'rules'" secondary @click="showPreview = true" :disabled="!selectedKey">
          预览最终提示词
        </n-button>
        <n-button
          v-if="tab === 'rules' && canManage && selectedSkill?.source !== 'custom'"
          secondary
          type="warning"
          :disabled="!selectedKey || scope !== 'global'"
          @click="resetToFactory"
        >
          按出厂重建
        </n-button>
        <n-button
          v-if="tab === 'rules'"
          type="primary"
          :disabled="readonly || !dirty"
          :loading="rulesLoading"
          @click="save"
        >
          保存{{ dirty ? '（有改动）' : '' }}
        </n-button>
        <n-button v-if="backFromStudio" quaternary @click="goBackToStudio">返回创作空间</n-button>
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: var(--space-3)">
      {{ error }}
    </n-alert>

    <div class="rules-entry-tabs" role="tablist" aria-label="创作配置类型">
      <button type="button" role="tab" :aria-selected="tab === 'rules'" :class="{ active: tab === 'rules' }" @click="tab = 'rules'">Skill 规则</button>
      <button type="button" role="tab" :aria-selected="tab === 'prompts'" :class="{ active: tab === 'prompts' }" @click="tab = 'prompts'">全局提示词与词表</button>
    </div>

    <div v-if="tab === 'prompts'" class="global-prompts">
      <div class="card global-prompts-head">
        <div>
          <h2>全局提示词与词表</h2>
          <p class="field-hint">此处配置对所有 Skill 共用，不需要选择 Skill；具体按对应生成环节生效。</p>
        </div>
        <n-tag size="small" :bordered="false">全局共用</n-tag>
      </div>
      <PromptRulesPanel :can-manage="canManage" :focus-type="focusType" />
    </div>

    <div v-else class="rules-layout">
      <!-- Skill 列表与对应规则 -->
      <aside class="card rules-side">
        <n-input v-model:value="keyword" size="small" clearable placeholder="搜索 Skill 名称或标识" />
        <n-button
          v-if="canManage"
          block
          secondary
          size="small"
          style="margin-top: var(--space-2)"
          @click="openCreate"
        >
          ＋ 新增 Skill
        </n-button>
        <div v-if="loading" style="margin-top: var(--space-3)">
          <n-spin size="small" />
        </div>
        <div v-else class="rules-side-scroll">
          <div class="skill-group">
            <div class="skill-group-title">全部 Skill · {{ visibleSkills.length }}</div>
            <button
              v-for="item in visibleSkills"
              :key="item.skillKey"
              class="skill-item"
              :class="{ active: item.skillKey === selectedKey }"
              @click="switchSkill(item.skillKey)"
            >
              <span class="skill-name">
                {{ item.name || item.skillKey }}
                <n-tag v-if="item.hasPersonalCopy" size="small" round type="warning">已定制</n-tag>
              </span>
              <span class="skill-counts">
                全局 {{ item.globalCount }} · 个人 {{ item.personalCount }}
              </span>
            </button>
          </div>
          <n-empty
            v-if="!visibleSkills.length"
            description="没有匹配的 Skill"
            size="small"
            style="margin-top: var(--space-3)"
          />
        </div>
      </aside>

      <!-- 右：Skill 信息卡 + 规则面板 -->
      <section class="rules-main">
        <div class="card skill-header">
          <div class="row" style="gap: var(--space-3); flex-wrap: wrap">
            <strong>{{ selectedSkill?.name || selectedKey || '未选择 Skill' }}</strong>
            <n-tag v-if="selectedSkill?.source === 'custom'" size="small" round type="info">自定义</n-tag>
            <span class="list-meta" style="margin: 0">全局 {{ selectedSkill?.globalCount ?? 0 }} 条 · 个人 {{ selectedSkill?.personalCount ?? 0 }} 条</span>
            <template v-if="canManage && selectedSkill?.source === 'custom'">
              <n-button quaternary size="small" @click="openEdit">编辑</n-button>
              <n-button quaternary size="small" type="error" @click="removeSelectedSkill">删除</n-button>
            </template>
            <div class="spacer" />
            <n-radio-group :value="scope" size="small" @update:value="switchScope">
              <n-radio value="global">全局</n-radio>
              <n-radio value="personal">我的副本</n-radio>
            </n-radio-group>
          </div>
          <p v-if="selectedSkill?.description" class="field-hint">{{ selectedSkill.description }}</p>
          <p class="field-hint" style="margin-top: var(--space-2)">{{ scopeHint }}</p>
          <p v-if="readonly" class="field-hint" style="margin: 0">你没有「创作规则管理」权限：全局规则只读。要按自己的习惯写作，请切换到「我的副本」。</p>
          <p v-else-if="dirty" class="field-hint" style="margin: 0; color: var(--color-warning, inherit)">有未保存的改动：共 {{ rows.length }} 条，启用 {{ enabledCount }} 条。保存前不会影响生成。</p>
        </div>
        <div style="margin-top: var(--space-3)">
          <SkillRulesPanel
            :key="`${selectedKey}:${scope}`"
            :rows="rows"
            :scope="scope"
            :readonly="readonly"
            :loading="rulesLoading"
            :focus-type="focusType"
            @update:rows="onRowsChange"
          />
          <div v-if="!readonly" class="rules-savebar">
            <n-button type="primary" :disabled="!dirty" :loading="rulesLoading" @click="save">
              保存规则
            </n-button>
            <n-button quaternary :disabled="!dirty" @click="loadRules">放弃改动</n-button>
          </div>
        </div>
      </section>
    </div>

    <PreviewDrawer
      v-model:show="showPreview"
      :skill-key="selectedKey"
      :scope="scope"
    />

    <!-- 新增 / 编辑全局 Skill：内置条目不可改，这里只服务 source=custom 的条目 -->
    <n-modal
      v-model:show="showSkillModal"
      preset="card"
      :title="editingSkill ? '编辑 Skill 信息' : '新增全局 Skill'"
      style="max-width: 480px"
    >
      <n-form label-placement="top">
        <n-form-item v-if="!editingSkill" label="标识（Skill key）">
          <n-input
            v-model:value="skillForm.skillKey"
            :disabled="skillSaving"
            placeholder="如 team-review-style，3 字符以上、不含空格"
          />
        </n-form-item>
        <n-form-item label="名称">
          <n-input v-model:value="skillForm.displayName" :disabled="skillSaving" maxlength="60" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input
            v-model:value="skillForm.description"
            type="textarea"
            :rows="2"
            :disabled="skillSaving"
            placeholder="给成员看的用途说明，可留空"
          />
        </n-form-item>
      </n-form>
      <p v-if="!editingSkill" class="field-hint" style="margin: 0 0 var(--space-2)">
        新增后是空的规则表：在「Skill 规则」里添加语气、模板等规则行并保存，下一次生成即生效。
      </p>
      <template #footer>
        <div class="row" style="justify-content: flex-end; gap: var(--space-2)">
          <n-button quaternary :disabled="skillSaving" @click="showSkillModal = false">取消</n-button>
          <n-button type="primary" :loading="skillSaving" @click="submitSkillModal">
            {{ editingSkill ? '保存' : '新增' }}
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.rules-savebar {
  position: sticky;
  bottom: var(--space-3);
  z-index: 2;
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.rules-main {
  min-width: 0;
}
.rules-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}
.rules-side {
  position: sticky;
  top: var(--space-3);
  max-height: calc(100vh - 160px);
  display: flex;
  flex-direction: column;
}
/* 搜索框和「新增 Skill」固定在滚动区域之外，列表再长也不用滚到底找按钮 */
.rules-side > :not(.skill-group):not(.rules-side-scroll) {
  flex: 0 0 auto;
}
/* 两个分组包进同一个滚动容器，侧栏超出视口时一起滚，搜索框和新增按钮留在外面 */
.rules-side-scroll {
  min-height: 0;
  overflow: auto;
}
.skill-group {
  margin-top: var(--space-3);
}
.skill-group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.04em;
  margin-bottom: var(--space-1);
}
.skill-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm, 6px);
  background: none;
  text-align: left;
  cursor: pointer;
  color: inherit;
}
.skill-item:hover {
  background: var(--color-fill-1, rgba(0, 0, 0, 0.03));
}
.skill-item.active {
  border-color: var(--color-primary, currentColor);
  background: var(--color-fill-1, rgba(0, 0, 0, 0.03));
}
.skill-name {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 13px;
  font-weight: 500;
}
.skill-counts {
  font-size: 12px;
  color: var(--color-text-secondary);
}
/* 页面级双入口：Skill 规则（需选择 Skill）与全局提示词与词表（不依赖 Skill） */
.rules-entry-tabs {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.rules-entry-tabs button {
  margin-bottom: -1px;
  padding: var(--space-2) var(--space-3);
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}
.rules-entry-tabs button:hover {
  color: var(--color-text);
}
.rules-entry-tabs button.active {
  border-bottom-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 600;
}
.global-prompts-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.global-prompts-head h2 {
  margin: 0 0 var(--space-1);
  font-size: var(--font-lg, 18px);
}
@media (max-width: 960px) {
  .rules-layout {
    grid-template-columns: 1fr;
  }
  .rules-side {
    position: static;
    max-height: none;
  }
}
</style>
