<script setup>
/**
 * PublishDrawer — 发布全屏抽屉
 *
 * Props:
 *   open        Boolean — 是否打开
 *   title       String  — 稿件标题
 *   body        String  — 稿件正文（Markdown）
 *   contentId   String  — 稿件 ID，用于推送后刷新
 *
 * Emits:
 *   close       — 关闭抽屉
 *   published   — 成功推送到草稿箱后
 */
import { ref, watch, onMounted } from 'vue'
import MarkdownEditor from '../../components/MarkdownEditor.vue'
import { useWechatPublish } from '../../composables/useWechatPublish'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  body: { type: String, default: '' },
  contentId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'published'])

const {
  wechat,
  wechatError,
  wechatForm,
  wechatAccountId,
  wechatPushError,
  pushAccountOptions,
  allThemes,
  wechatThemeId,
  layoutBusy,
  layoutHtml,
  layoutError,
  loadWechat,
  loadThemes,
  loadAssets,
  scheduleLayout,
  pushWechatDraft,
  initForm,
} = useWechatPublish()

const notice = ref('')
const pushing = ref(false)
let initialized = false

// 抽屉打开时初始化（懒加载）
watch(() => props.open, async (val) => {
  if (!val) return
  if (!initialized) {
    await Promise.all([loadWechat(), loadThemes(), loadAssets()])
    initialized = true
  }
  // 每次打开都用最新的 title/body 初始化表单
  initForm({ title: props.title, body: props.body })
  scheduleLayout()
})

async function handlePush() {
  pushing.value = true
  notice.value = ''
  try {
    const job = await pushWechatDraft()
    if (job) {
      notice.value = '草稿已推送到公众号草稿箱，请到后台预览并群发。'
      emit('published')
    }
  } finally {
    pushing.value = false
  }
}

function handleClose() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="publish-drawer-overlay" @click.self="handleClose">
      <div class="publish-drawer" role="dialog" aria-label="发布到公众号">
        <!-- 顶部栏 -->
        <header class="publish-drawer-header">
          <span class="publish-drawer-title">发布到公众号</span>
          <span class="spacer" />
          <button class="pub-close-btn" @click="handleClose">✕</button>
        </header>

        <div class="publish-drawer-body">
          <!-- 左：文章预览 -->
          <section class="publish-preview-pane">
            <div class="publish-pane-head">
              <span class="publish-pane-label">内容预览</span>
              <span class="publish-pane-sub">来自最新保存版本</span>
            </div>
            <div v-if="layoutHtml" class="publish-layout-preview" v-html="layoutHtml" />
            <div v-else-if="layoutBusy" class="publish-preview-loading">排版渲染中…</div>
            <div v-else-if="layoutError" class="publish-preview-error">排版失败：{{ layoutError }}</div>
            <div v-else class="publish-preview-fallback">
              <MarkdownEditor
                :model-value="body"
                :readonly="true"
                :toolbar="{}"
                class="publish-preview-md"
              />
            </div>
          </section>

          <!-- 右：发布配置 -->
          <aside class="publish-config-pane">
            <div class="publish-pane-head">
              <span class="publish-pane-label">发布配置</span>
            </div>

            <div v-if="wechatError" class="banner banner-danger">{{ wechatError }}</div>
            <div v-if="wechatPushError" class="banner banner-danger">{{ wechatPushError }}</div>
            <div v-if="notice" class="banner banner-info">{{ notice }}</div>

            <div v-if="!wechat" class="pub-loading">加载公众号配置中…</div>
            <template v-else>
              <div
                v-if="!wechat.configured || !wechat.account"
                class="banner banner-warning"
              >
                {{ !wechat.configured
                  ? '未配置公众号凭据。请前往「发布管理」绑定账号。'
                  : '尚未绑定公众号账号。请前往「发布管理」绑定账号。'
                }}
              </div>

              <!-- 目标公众号 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-account">目标公众号</label>
                <select id="pub-account" v-model="wechatAccountId" class="select">
                  <option v-if="!pushAccountOptions.length" value="">暂无已连接账号</option>
                  <option v-for="opt in pushAccountOptions" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
                </select>
              </div>

              <!-- 标题 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-title">标题</label>
                <input
                  id="pub-title"
                  v-model="wechatForm.title"
                  class="input"
                  maxlength="64"
                  placeholder="文章标题"
                />
                <span class="pub-hint">{{ wechatForm.title.length }} / 64</span>
              </div>

              <!-- 摘要 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-digest">摘要（可选）</label>
                <textarea
                  id="pub-digest"
                  v-model="wechatForm.digest"
                  class="textarea pub-textarea"
                  maxlength="120"
                  placeholder="不填则自动截取正文前 120 字"
                />
                <span class="pub-hint">{{ wechatForm.digest.length }} / 120</span>
              </div>

              <!-- 作者 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-author">作者（可选）</label>
                <input id="pub-author" v-model="wechatForm.author" class="input" placeholder="作者名" />
              </div>

              <!-- 排版主题 -->
              <div class="pub-field">
                <label class="pub-label">排版主题</label>
                <div class="pub-theme-list">
                  <button
                    v-for="theme in allThemes"
                    :key="theme.id"
                    class="pub-theme-btn"
                    :class="{ 'is-active': wechatThemeId === theme.id }"
                    @click="wechatThemeId = theme.id; scheduleLayout()"
                  >{{ theme.name }}</button>
                </div>
              </div>

              <!-- 底部推送按钮 -->
              <div class="pub-actions">
                <button
                  class="btn btn-sm"
                  :disabled="pushing || !wechat.configured"
                  @click="handlePush"
                >
                  {{ pushing ? '推送中…' : '推送到草稿箱' }}
                </button>
                <button class="btn btn-ghost btn-sm" @click="handleClose">取消</button>
              </div>
            </template>
          </aside>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.publish-drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 200;
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
}

.publish-drawer {
  width: min(960px, 100vw);
  height: 100vh;
  background: var(--color-surface);
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 32px rgba(0, 0, 0, 0.18);
}

.publish-drawer-header {
  display: flex;
  align-items: center;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.publish-drawer-title {
  font-size: var(--font-base);
  font-weight: 700;
  color: var(--color-text-primary);
}

.spacer { flex: 1; }

.pub-close-btn {
  background: none;
  border: none;
  font-size: 16px;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}

.pub-close-btn:hover { background: var(--color-surface-raised); }

.publish-drawer-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 380px;
}

.publish-preview-pane,
.publish-config-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}

.publish-preview-pane {
  border-right: 1px solid var(--color-border);
}

.publish-pane-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.publish-pane-label {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.publish-pane-sub {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.publish-layout-preview {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6);
  font-size: 15px;
  line-height: 1.85;
}

.publish-preview-loading,
.publish-preview-error {
  padding: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}

.publish-preview-fallback {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.publish-preview-md {
  min-height: 100%;
}

.publish-config-pane {
  padding: var(--space-4) var(--space-5);
  gap: var(--space-3);
}

.pub-loading {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  padding: var(--space-4) 0;
}

.pub-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pub-label {
  font-size: var(--font-xs);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.pub-hint {
  font-size: 11px;
  color: var(--color-text-muted);
  text-align: right;
}

.pub-textarea {
  min-height: 72px;
  resize: vertical;
}

.pub-theme-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.pub-theme-btn {
  padding: 4px 10px;
  font-size: var(--font-xs);
  font-weight: 500;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.pub-theme-btn.is-active {
  background: var(--color-text-primary);
  border-color: var(--color-text-primary);
  color: var(--color-surface);
}

.pub-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
</style>
