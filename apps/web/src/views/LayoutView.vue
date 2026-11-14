<script setup>
import { onMounted, ref } from 'vue'
import { NAlert, NSkeleton, NTabs, NTabPane } from 'naive-ui'
import { useWechatLayout } from '../composables/useWechatLayout'
import ThemeTab from '../features/layout/ThemeTab.vue'
import ThemeEditorModal from '../features/layout/ThemeEditorModal.vue'
import ComponentLibraryTab from '../features/layout/ComponentLibraryTab.vue'
import PreviewTab from '../features/layout/PreviewTab.vue'

/**
 * 微信排版管理页：主题 / 组件库 / 样文预览。
 * 排版数据（主题、偏好、schema）集中在 useWechatLayout，三个 Tab 共享一份状态。
 */
const store = useWechatLayout()

const activeTab = ref('themes')
const editorOpen = ref(false)
const editorInitial = ref({})

onMounted(() => store.load())

function openCreate(baseThemeId = '') {
  editorInitial.value = { baseThemeId }
  editorOpen.value = true
}

function openEdit(theme) {
  editorInitial.value = { theme }
  editorOpen.value = true
}
</script>

<template>
  <div class="page layout-page">
    <div class="page-head">
      <div>
        <h1 class="page-title">微信排版</h1>
        <p class="page-sub">管理公众号排版主题与小组件：主题在这里改，发文时在创作空间的「发布」里用。</p>
      </div>
    </div>

    <n-alert v-if="store.error.value" type="error" :bordered="false">{{ store.error.value }}</n-alert>

    <div v-if="store.loading.value && !store.themes.value.presets.length" class="card">
      <n-skeleton height="20px" width="40%" />
      <div style="height: 12px" />
      <n-skeleton :repeat="2" height="88px" />
    </div>

    <n-tabs v-else v-model:value="activeTab" type="line" animated>
      <n-tab-pane name="themes" tab="排版主题">
        <ThemeTab :store="store" @create="openCreate" @edit="openEdit" />
      </n-tab-pane>
      <n-tab-pane name="components" tab="小组件库">
        <ComponentLibraryTab :store="store" />
      </n-tab-pane>
      <n-tab-pane name="preview" tab="样文预览">
        <PreviewTab :store="store" />
      </n-tab-pane>
    </n-tabs>

    <ThemeEditorModal
      :open="editorOpen"
      :store="store"
      :initial="editorInitial"
      @close="editorOpen = false"
    />
  </div>
</template>

<style scoped>
.layout-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}
</style>
