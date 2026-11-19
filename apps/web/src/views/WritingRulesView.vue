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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NRadio,
  NRadioGroup,
  NSpin,
  NTab,
  NTabs,
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

// ---------------------------------------------------------------- 列表与作用域

const loading = ref(true)
const error = ref('')
const skills = ref([])
const keyword = ref('')
const selectedKey = ref('')
const scope = ref('global')
const tab = ref('rules')

const flowSkills = computed(() => skills.value.filter((item) => item.category === 'flow'))
const styleSkills = computed(() => skills.value.filter((item) => item.category !== 'flow'))

function matchKeyword(item) {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return true
  return (
    (item.name || '').toLowerCase().includes(q) ||
    (item.skillKey || '').toLowerCase().includes(q)
  )
}

const visibleFlow = computed(() => flowSkills.value.filter(matchKeyword))
const visibleStyle = computed(() => styleSkills.value.filter(matchKeyword))

async function loadSkills() {
  error.value = ''
  try {
    skills.value = await api.get('/writing-rules/skills')
    if (!selectedKey.value && skills.value.length) selectedKey.value = skills.value[0].skillKey
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

/** 面板每次编辑都发完整新数组；结构校验交给后端，这里只记脏标记。 */
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
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

async function save() {
  if (!selectedKey.value) return
  error.value = ''
  const payload = {
    // 只提交服务端认识的字段，界面内部的临时字段（如 _localKey）不带上
    rules: rows.value.map((row) => ({
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
          语气、风格、审稿、工作流、字数、策略与提示词词表统一在这里维护；保存后下一次生成即生效。
        </p>
      </div>
      <div class="row" style="gap: var(--space-2)">
        <n-button secondary @click="showPreview = true" :disabled="!selectedKey">
          预览最终提示词
        </n-button>
        <n-button
          v-if="canManage"
          secondary
          type="warning"
          :disabled="!selectedKey || scope !== 'global'"
          @click="resetToFactory"
        >
          按出厂重建
        </n-button>
        <n-button
          type="primary"
          :disabled="readonly || !dirty"
          :loading="rulesLoading"
          @click="save"
        >
          保存{{ dirty ? '（有改动）' : '' }}
        </n-button>
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: var(--space-3)">
      {{ error }}
    </n-alert>

    <div class="rules-layout">
      <!-- 左：Skill 列表，按流程型 / 风格型分组，带两侧规则计数 -->
      <aside class="card rules-side">
        <n-input v-model:value="keyword" size="small" clearable placeholder="搜索 Skill 名称或标识" />
        <div v-if="loading" style="margin-top: var(--space-3)">
          <n-spin size="small" />
        </div>
        <template v-else>
          <div v-if="visibleFlow.length" class="skill-group">
            <div class="skill-group-title">流程型</div>
            <button
              v-for="item in visibleFlow"
              :key="item.skillKey"
              class="skill-item"
              :class="{ active: item.skillKey === selectedKey }"
              @click="switchSkill(item.skillKey)"
            >
              <span class="skill-name">{{ item.name || item.skillKey }}</span>
              <span class="skill-counts">
                全局 {{ item.globalCount }} · 个人 {{ item.personalCount }}
              </span>
            </button>
          </div>
          <div v-if="visibleStyle.length" class="skill-group">
            <div class="skill-group-title">风格型</div>
            <button
              v-for="item in visibleStyle"
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
            v-if="!visibleFlow.length && !visibleStyle.length"
            description="没有匹配的 Skill"
            size="small"
            style="margin-top: var(--space-3)"
          />
        </template>
      </aside>

      <!-- 右：作用域 + 规则/提示词两页签 -->
      <section class="rules-main">
        <div class="card">
          <div class="row" style="gap: var(--space-3); flex-wrap: wrap">
            <strong>{{ selectedSkill?.name || selectedKey || '未选择 Skill' }}</strong>
            <n-tag v-if="selectedSkill?.category === 'flow'" size="small" round>流程型</n-tag>
            <n-tag v-else-if="selectedSkill" size="small" round>风格型</n-tag>
            <span class="list-meta" style="margin: 0">
              全局 {{ selectedSkill?.globalCount ?? 0 }} 条 · 个人
              {{ selectedSkill?.personalCount ?? 0 }} 条
            </span>
            <div class="spacer" />
            <n-radio-group :value="scope" size="small" @update:value="switchScope">
              <n-radio value="global">全局</n-radio>
              <n-radio value="personal">我的副本</n-radio>
            </n-radio-group>
          </div>
          <p class="field-hint" style="margin-top: var(--space-2)">{{ scopeHint }}</p>
          <p v-if="readonly" class="field-hint" style="margin: 0">
            你没有「创作规则管理」权限：全局规则只读。要按自己的习惯写作，请切换到「我的副本」。
          </p>
          <p v-else-if="dirty" class="field-hint" style="margin: 0; color: var(--color-warning, inherit)">
            有未保存的改动：共 {{ rows.length }} 条，启用 {{ enabledCount }} 条。保存前不会影响生成。
          </p>
        </div>

        <n-tabs v-model:value="tab" type="line" animated style="margin-top: var(--space-3)">
          <n-tab name="rules">规则</n-tab>
          <n-tab name="prompts">提示词与词表</n-tab>
        </n-tabs>

        <div v-if="tab === 'rules'" style="margin-top: var(--space-3)">
          <SkillRulesPanel
            :rows="rows"
            :scope="scope"
            :readonly="readonly"
            :loading="rulesLoading"
            @update:rows="onRowsChange"
          />
          <div v-if="!readonly" class="row" style="margin-top: var(--space-3); gap: var(--space-2)">
            <n-button type="primary" :disabled="!dirty" :loading="rulesLoading" @click="save">
              保存规则
            </n-button>
            <n-button quaternary :disabled="!dirty" @click="loadRules">放弃改动</n-button>
          </div>
        </div>

        <div v-else style="margin-top: var(--space-3)">
          <PromptRulesPanel :can-manage="canManage" />
        </div>
      </section>
    </div>

    <PreviewDrawer
      v-model:show="showPreview"
      :skill-key="selectedKey"
      :scope="scope"
    />
  </div>
</template>

<style scoped>
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
