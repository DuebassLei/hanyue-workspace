<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NDatePicker, NForm, NFormItem, NInput, NModal, NSelect, NSpace, NTag } from 'naive-ui'
import { api } from '../api/client'

const entries = ref([])
const loading = ref(true)
const error = ref('')
const showForm = ref(false)
const saving = ref(false)
const form = ref({ title: '', plannedAt: null, platform: null })
const currentMonth = ref(new Date(new Date().getFullYear(), new Date().getMonth(), 1))
const selectedDate = ref(toDateKey(new Date()))
const activeFilter = ref('all')
const router = useRouter()

const PLATFORMS = [{ code: '', label: '不指定' }, { code: 'wechat_official', label: '公众号' }]
const platformOptions = PLATFORMS.map((item) => ({ value: item.code || null, label: item.label }))
const FILTERS = [{ value: 'all', label: '全部' }, { value: 'planned', label: '待发布' }, { value: 'published', label: '已发布' }, { value: 'overdue', label: '异常' }]

function toDateKey(value) {
  const date = value instanceof Date ? value : new Date(value)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function startOfToday() { const date = new Date(); date.setHours(0, 0, 0, 0); return date }
function isOverdue(entry) { return entry.status !== 'published' && new Date(entry.plannedAt) < startOfToday() }
function statusKey(entry) { return isOverdue(entry) ? 'overdue' : (entry.status || 'planned') }
function statusLabel(entry) { const key = statusKey(entry); return key === 'published' ? '已发布' : key === 'overdue' ? '逾期' : '待发布' }
function statusType(entry) { const key = statusKey(entry); return key === 'published' ? 'success' : key === 'overdue' ? 'error' : 'warning' }
function formatDate(date) { return `${date.getFullYear()}年${date.getMonth() + 1}月` }
function formatDay(dateKey) { const date = new Date(`${dateKey}T12:00:00`); return `${date.getMonth() + 1}月${date.getDate()}日` }
function formatTime(value) { return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }
function platformLabel(code) { return PLATFORMS.find((p) => p.code === code)?.label || '' }

const entriesByDay = computed(() => {
  const map = new Map()
  for (const entry of entries.value) { const day = toDateKey(entry.plannedAt); if (!map.has(day)) map.set(day, []); map.get(day).push(entry) }
  return map
})
const calendarDays = computed(() => {
  const first = new Date(currentMonth.value.getFullYear(), currentMonth.value.getMonth(), 1)
  const mondayOffset = (first.getDay() + 6) % 7
  const start = new Date(first); start.setDate(first.getDate() - mondayOffset)
  return Array.from({ length: 42 }, (_, index) => { const date = new Date(start); date.setDate(start.getDate() + index); const key = toDateKey(date); return { date, key, current: date.getMonth() === currentMonth.value.getMonth(), entries: entriesByDay.value.get(key) || [] } })
})
const selectedEntries = computed(() => entriesByDay.value.get(selectedDate.value) || [])
const visibleSelectedEntries = computed(() => activeFilter.value === 'all' ? selectedEntries.value : selectedEntries.value.filter((entry) => statusKey(entry) === activeFilter.value))
const monthEntries = computed(() => entries.value.filter((entry) => { const date = new Date(entry.plannedAt); return date.getFullYear() === currentMonth.value.getFullYear() && date.getMonth() === currentMonth.value.getMonth() }))
const upcomingEntries = computed(() => { const today = startOfToday(); const end = new Date(today); end.setDate(end.getDate() + 7); return entries.value.filter((entry) => { const date = new Date(entry.plannedAt); return date >= today && date < end && entry.status !== 'published' }).sort((a, b) => a.plannedAt.localeCompare(b.plannedAt)) })
const overdueEntries = computed(() => entries.value.filter(isOverdue).sort((a, b) => a.plannedAt.localeCompare(b.plannedAt)))
const recentEntries = computed(() => [...overdueEntries.value, ...upcomingEntries.value.filter((entry) => !overdueEntries.value.some((item) => item.id === entry.id))].slice(0, 8))
const stats = computed(() => ({ upcoming: upcomingEntries.value.length, overdue: overdueEntries.value.length, month: monthEntries.value.length }))
const monthTitle = computed(() => formatDate(currentMonth.value))

function shiftMonth(amount) { currentMonth.value = new Date(currentMonth.value.getFullYear(), currentMonth.value.getMonth() + amount, 1) }
function goToday() { const now = new Date(); currentMonth.value = new Date(now.getFullYear(), now.getMonth(), 1); selectedDate.value = toDateKey(now) }
function selectDay(day) { selectedDate.value = day.key; if (!day.current) currentMonth.value = new Date(day.date.getFullYear(), day.date.getMonth(), 1) }
function openForm(dateKey = selectedDate.value) { const date = new Date(`${dateKey}T12:00:00`); date.setHours(18, 0, 0, 0); form.value = { title: '', plannedAt: date.getTime(), platform: null }; showForm.value = true }
async function load() { loading.value = true; error.value = ''; try { entries.value = await api.get('/calendar') } catch (err) { error.value = err.message } finally { loading.value = false } }
async function submit() { if (!form.value.plannedAt) return; saving.value = true; try { const created = await api.post('/calendar', { title: form.value.title || '未命名排期', plannedAt: new Date(form.value.plannedAt).toISOString(), platform: form.value.platform || null }); entries.value.push(created); entries.value.sort((a, b) => a.plannedAt.localeCompare(b.plannedAt)); selectedDate.value = toDateKey(created.plannedAt); showForm.value = false } catch (err) { error.value = err.message } finally { saving.value = false } }
function openStudio(articleId) { router.push({ name: 'paper', query: { id: articleId } }) }
onMounted(load)
</script>

<template>
  <div class="page calendar-page">
    <div class="page-head"><div><span class="eyebrow">PUBLISHING RHYTHM</span><h1 class="page-title">内容日历</h1><p class="page-sub">看清发布节奏，把近期要做的事排在前面。</p></div><n-button type="primary" @click="openForm()">＋ 安排一期</n-button></div>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <div v-if="loading" class="calendar-loading"><div class="skeleton" style="height: 520px" /><div class="skeleton" style="height: 520px" /></div>
    <template v-else>
      <section class="calendar-stats"><div class="calendar-stat"><span>未来 7 天待发布</span><strong>{{ stats.upcoming }}</strong><small>按计划时间执行</small></div><div class="calendar-stat calendar-stat-alert"><span>逾期 / 异常</span><strong>{{ stats.overdue }}</strong><small>需要尽快处理</small></div><div class="calendar-stat"><span>本月排期</span><strong>{{ stats.month }}</strong><small>{{ monthTitle }}</small></div></section>
      <div class="calendar-layout">
        <section class="card month-card">
          <div class="calendar-toolbar"><div class="month-nav"><n-button quaternary circle @click="shiftMonth(-1)">‹</n-button><h2>{{ monthTitle }}</h2><n-button quaternary circle @click="shiftMonth(1)">›</n-button><n-button secondary size="small" @click="goToday">回到今天</n-button></div><div class="calendar-legend"><span><i class="dot dot-planned" />待发布</span><span><i class="dot dot-published" />已发布</span><span><i class="dot dot-overdue" />逾期</span></div></div>
          <div class="weekday-row"><span v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day">周{{ day }}</span></div>
          <div class="month-grid"><button v-for="day in calendarDays" :key="day.key" class="day-cell" :class="{ 'is-muted': !day.current, 'is-today': day.key === toDateKey(new Date()), 'is-selected': day.key === selectedDate }" @click="selectDay(day)"><span class="day-number">{{ day.date.getDate() }}</span><span v-for="entry in day.entries.slice(0, 3)" :key="entry.id" class="day-entry" :class="`entry-${statusKey(entry)}`" :title="entry.title"><i class="dot" /> <b>{{ formatTime(entry.plannedAt) }}</b> {{ entry.title }}</span><span v-if="day.entries.length > 3" class="more-count">还有 {{ day.entries.length - 3 }} 条</span></button></div>
        </section>
        <aside class="calendar-side">
          <section class="card recent-card"><div class="section-heading"><div><span class="eyebrow">NEXT 7 DAYS</span><h2 class="card-title">近期排期</h2></div><span class="side-count">{{ recentEntries.length }} 条</span></div><div v-if="!recentEntries.length" class="side-empty">未来 7 天没有待发布内容，可以安排下一期了。</div><div v-else class="recent-list"><button v-for="entry in recentEntries" :key="entry.id" class="recent-item" @click="selectDay({ key: toDateKey(entry.plannedAt), date: new Date(entry.plannedAt), current: true })"><i class="dot" :class="`dot-${statusKey(entry)}`" /><span class="recent-main"><strong>{{ entry.title }}</strong><small>{{ isOverdue(entry) ? `逾期 · ${formatDay(toDateKey(entry.plannedAt))}` : `${formatDay(toDateKey(entry.plannedAt))} ${formatTime(entry.plannedAt)}` }}<template v-if="entry.platform"> · {{ platformLabel(entry.platform) }}</template></small></span><n-tag size="small" round :type="statusType(entry)">{{ statusLabel(entry) }}</n-tag></button></div></section>
          <section class="card selected-card"><div class="section-heading"><div><span class="eyebrow">SELECTED DATE</span><h2 class="card-title">{{ formatDay(selectedDate) }}</h2></div><n-button text type="primary" @click="openForm(selectedDate)">＋ 添加</n-button></div><div class="filter-chips"><button v-for="filter in FILTERS" :key="filter.value" :class="{ active: activeFilter === filter.value }" @click="activeFilter = filter.value">{{ filter.label }}</button></div><div v-if="!visibleSelectedEntries.length" class="side-empty">这一天还没有符合条件的排期。</div><div v-else class="selected-list"><div v-for="entry in visibleSelectedEntries" :key="entry.id" class="selected-item"><div class="selected-item-head"><i class="dot" :class="`dot-${statusKey(entry)}`" /><strong>{{ entry.title }}</strong><n-tag size="small" round :type="statusType(entry)">{{ statusLabel(entry) }}</n-tag></div><div class="selected-meta">{{ formatTime(entry.plannedAt) }}<template v-if="entry.platform"> · {{ platformLabel(entry.platform) }}</template><span v-if="entry.studioArticleId"> · 成稿台排期</span></div><n-button v-if="entry.studioArticleId" size="small" quaternary type="primary" @click="openStudio(entry.studioArticleId)">去成稿台 →</n-button></div></div></section>
        </aside>
      </div>
    </template>
    <n-modal v-model:show="showForm" preset="card" title="安排一期内容" style="width: min(460px, calc(100vw - 32px))" :mask-closable="!saving"><n-form label-placement="top"><n-form-item label="标题"><n-input v-model:value="form.title" placeholder="这期准备发什么" /></n-form-item><n-form-item label="计划时间"><n-date-picker v-model:value="form.plannedAt" type="datetime" clearable style="width: 100%" /></n-form-item><n-form-item label="平台"><n-select v-model:value="form.platform" :options="platformOptions" placeholder="不指定" clearable /></n-form-item></n-form><template #footer><n-space justify="end"><n-button quaternary :disabled="saving" @click="showForm = false">取消</n-button><n-button type="primary" :loading="saving" :disabled="!form.plannedAt" @click="submit">保存</n-button></n-space></template></n-modal>
  </div>
</template>

<style scoped>
.calendar-page {
  box-sizing: border-box;
  height: 100%;
  max-width: none;
  min-height: 0;
  overflow: hidden;
  gap: 12px;
  padding: 16px 20px;
}
.calendar-page :deep(.page-title) { font-size: 28px; line-height: 1.15; }
.calendar-page :deep(.page-sub) { margin: 2px 0 0; }
.calendar-loading,
.calendar-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, .78fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
}
.calendar-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; flex: none; }
.calendar-stat { padding: 8px 14px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface-raised); }
.calendar-stat span,
.calendar-stat small { display: block; color: var(--color-text-secondary); font-size: var(--font-xs); }
.calendar-stat strong { display: block; margin: 2px 0 0; color: var(--color-text-primary); font-size: 22px; line-height: 1; }
.calendar-stat-alert strong { color: var(--color-danger); }
.month-card,
.recent-card,
.selected-card { display: flex; flex-direction: column; min-height: 0; padding: 12px 14px; }
.month-card { height: 100%; }
.calendar-toolbar,
.section-heading,
.month-nav,
.calendar-legend { display: flex; align-items: center; flex: none; }
.calendar-toolbar,
.section-heading { justify-content: space-between; gap: 12px; }
.month-nav { gap: 4px; }
.month-nav h2 { min-width: 132px; margin: 0; text-align: center; color: var(--color-text-primary); font-size: 18px; }
.calendar-legend { gap: 12px; color: var(--color-text-secondary); font-size: var(--font-xs); }
.calendar-legend span { display: inline-flex; align-items: center; gap: 5px; }
.weekday-row,
.month-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.weekday-row { margin-top: 8px; border-bottom: 1px solid var(--color-border); color: var(--color-text-tertiary); font-size: var(--font-xs); }
.weekday-row span { padding: 4px 6px; text-align: right; }
.month-grid { flex: 1; min-height: 0; grid-template-rows: repeat(6, minmax(0, 1fr)); border-left: 1px solid var(--color-border); }
.day-cell { min-height: 0; overflow: hidden; padding: 4px 6px; border: 0; border-right: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text-primary); text-align: left; cursor: pointer; }
.day-cell:hover { background: var(--color-surface-hover); }
.day-cell.is-muted { color: var(--color-text-tertiary); background: color-mix(in srgb, var(--color-surface) 88%, var(--color-border)); }
.day-cell.is-selected { box-shadow: inset 0 0 0 2px var(--color-accent); }
.day-number { display: block; width: 20px; height: 20px; margin-left: auto; border-radius: 50%; color: inherit; font-size: 11px; line-height: 20px; text-align: center; }
.day-cell.is-today .day-number { background: var(--color-accent); color: white; font-weight: 700; }
.day-entry { display: block; overflow: hidden; margin-top: 3px; padding: 1px 4px; border-radius: 4px; background: color-mix(in srgb, var(--color-accent) 8%, transparent); color: var(--color-text-secondary); font-size: 11px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
.day-entry b { font-weight: 600; }
.entry-published { background: color-mix(in srgb, var(--color-success) 12%, transparent); }
.entry-overdue { background: color-mix(in srgb, var(--color-danger) 12%, transparent); }
.more-count { display: block; margin-top: 2px; color: var(--color-accent); font-size: 11px; }
.dot { display: inline-block; flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--color-warning); }
.dot-planned { background: var(--color-warning); }
.dot-published { background: var(--color-success); }
.dot-overdue { background: var(--color-danger); }
.calendar-side { display: grid; grid-template-rows: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; min-height: 0; }
.recent-card,
.selected-card { overflow: hidden; }
.side-count { color: var(--color-text-tertiary); font-size: var(--font-xs); }
.recent-list,
.selected-list { display: grid; align-content: start; gap: 2px; margin-top: 8px; min-height: 0; overflow: auto; }
.filter-chips { display: flex; flex: none; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.filter-chips button { padding: 4px 8px; border: 1px solid var(--color-border); border-radius: 999px; background: transparent; color: var(--color-text-secondary); font-size: var(--font-xs); cursor: pointer; }
.filter-chips button.active { border-color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 10%, transparent); color: var(--color-accent); font-weight: 600; }
.recent-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 4px; border: 0; border-bottom: 1px solid var(--color-border); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.recent-item:hover { background: var(--color-surface-hover); }
.recent-main { min-width: 0; flex: 1; }
.recent-main strong,
.recent-main small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-main strong { color: var(--color-text-primary); font-size: var(--font-sm); }
.recent-main small,
.selected-meta { color: var(--color-text-secondary); font-size: var(--font-xs); }
.selected-item { padding: 8px 0; border-bottom: 1px solid var(--color-border); }
.selected-item-head { display: flex; align-items: center; gap: 7px; }
.selected-item-head strong { min-width: 0; flex: 1; overflow: hidden; color: var(--color-text-primary); text-overflow: ellipsis; white-space: nowrap; }
.selected-meta { margin: 4px 0 4px 14px; }
.selected-item .n-button { margin-left: 14px; }
.side-empty { margin-top: 8px; padding: 16px 8px; color: var(--color-text-secondary); font-size: var(--font-sm); text-align: center; }
@media (max-width: 980px) {
  .calendar-page { height: auto; overflow: visible; }
  .calendar-layout,
  .calendar-loading { grid-template-columns: 1fr; flex: none; }
  .month-grid { grid-template-rows: repeat(6, 92px); }
  .calendar-side { grid-template-columns: 1fr 1fr; grid-template-rows: auto; }
  .recent-list,
  .selected-list { max-height: 280px; }
  .calendar-legend { display: none; }
}
@media (max-width: 640px) {
  .calendar-page { padding: 12px; }
  .calendar-stats { grid-template-columns: 1fr; }
  .calendar-side { grid-template-columns: 1fr; }
  .month-card,
  .recent-card,
  .selected-card { padding: 12px; }
  .month-grid { grid-template-rows: repeat(6, 72px); }
  .day-cell { padding: 4px; }
  .day-entry { margin-top: 2px; padding: 1px 3px; font-size: 10px; }
  .day-entry b { display: none; }
  .month-nav h2 { min-width: 112px; font-size: 17px; }
  .calendar-toolbar { align-items: flex-start; }
  .weekday-row span { padding: 4px 2px; font-size: 10px; }
}
</style>
