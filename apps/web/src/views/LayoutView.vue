<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useLayoutStudio } from '../features/layout/useLayoutStudio'
import { STYLE_PRESETS, COLOR_PRESETS, BANNER_MODES, SVG_CARD_PRESETS } from '../features/layout/studioPresets'

const route = useRoute()
const router = useRouter()
const studio = useLayoutStudio(route, router)
const { title, body, options, articleId, versionId, loading, saving, rendering, error, previewError, renderWarnings, renderStats,
  notice, html, dirty, savedAt, ready, load, save, copyHtml, copyRichText, previewDocument,
  cardPanel, cardPreviewDocument, openCardPanel, closeCardPanel, generateCard, renderCardPreview, insertCard } = studio
const editing = ref(false)
const currentStyle = computed(() => STYLE_PRESETS.find(item => item.id === options.style))
const currentColor = computed(() => COLOR_PRESETS.find(item => item.id === options.theme))
const currentCardPreset = computed(() => SVG_CARD_PRESETS.find(item => item.id === cardPanel.kind))
const wordCount = computed(() => body.value.replace(/\s/g, '').length)
const saveStatus = computed(() => saving.value ? '正在保存…' : dirty.value ? '有未保存的修改' : savedAt.value ? '已保存到当前版本' : articleId.value ? '当前版本已载入' : '样文体验 · 未关联文章')
onBeforeRouteLeave(() => !dirty.value || !articleId.value || window.confirm('排版或正文尚未保存，确定离开吗？'))
onBeforeRouteUpdate((to, from) => {
  if (saving.value || (to.query.articleId === from.query.articleId && to.query.versionId === from.query.versionId)) return true
  return !dirty.value || !articleId.value || window.confirm('排版或正文尚未保存，确定切换文章版本吗？')
})
</script>

<template>
  <div class="layout-studio">
    <header class="studio-heading">
      <div><p class="eyebrow">WECHAT EDITORIAL STUDIO</p><h1>微信排版 <span>让表达，恰到好处。</span></h1></div>
      <button class="studio-button subtle" @click="router.push('/articles')">返回文章管理 <span aria-hidden="true">↗</span></button>
    </header>

    <div v-if="error" class="studio-message error" role="alert">{{ error }} <button class="text-button" @click="load">重新加载</button></div>
    <div v-if="notice" class="studio-message success" role="status">{{ notice }}</div>

    <div class="studio-grid" :aria-busy="loading">
      <main class="manuscript">
        <div class="paper-toolbar">
          <div class="document-state"><span class="state-dot" :class="{ unsaved: dirty }" /><span>{{ saveStatus }}</span></div>
          <div class="toolbar-actions">
            <button class="studio-button" :class="{ active: editing }" :disabled="loading || !ready" @click="editing = !editing">{{ editing ? '完成编辑' : '编辑正文' }}</button>
            <button v-if="editing" class="studio-button" :disabled="saving" title="选中一段正文后生成 SVG 动画卡" @click="openCardPanel">✦ 动画卡</button>
            <button class="studio-button" :disabled="!html || rendering || loading" @click="copyHtml">复制 HTML</button>
            <button class="studio-button" :disabled="!html || rendering || loading" @click="copyRichText">复制到公众号</button>
            <button class="studio-button primary" :disabled="!articleId || !ready || loading || saving || !dirty" @click="save">{{ saving ? '保存中…' : '保存排版' }}</button>
          </div>
        </div>
        <div class="paper-meta"><span>{{ articleId ? '文章稿纸' : '排版样文' }} <span v-if="versionId">/ 版本 {{ versionId.slice(0, 8) }}</span></span><span>{{ wordCount.toLocaleString() }} 字 <span class="meta-divider">/</span> {{ currentStyle?.name }} · {{ currentColor?.name }}</span></div>
        <details v-if="html && !rendering && !previewError" class="render-warnings" :class="{ 'has-warnings': renderWarnings.length }">
          <summary>自查报告 <span>{{ renderWarnings.length ? `· ${renderWarnings.length} 项提醒` : '· 渲染完成' }}</span></summary>
          <div v-if="renderStats" class="report-stats">
            <span v-if="renderStats.sourceChars != null">正文 {{ renderStats.sourceChars }} 字</span>
            <span v-if="renderStats.images != null">图片 {{ renderStats.images }} 张</span>
            <span v-if="renderStats.readingMinutes != null">约 {{ renderStats.readingMinutes }} 分钟阅读</span>
            <span v-if="renderStats.htmlChars != null">HTML {{ renderStats.htmlChars.toLocaleString() }} 字符</span>
          </div>
          <ul v-if="renderWarnings.length"><li v-for="(warning, index) in renderWarnings" :key="index">{{ warning }}</li></ul>
          <p v-else class="report-note">本次渲染未返回提醒，请发布前核对全文、图片与链接。</p>
        </details>
        <div v-if="!articleId" class="sample-note">当前为样文体验。要保存排版并用于发布，请从文章管理打开一篇文章。</div>
        <div v-if="loading" class="paper-loading" role="status"><span class="loading-line" /><span>正在展开文章稿纸…</span></div>
        <div v-else-if="!ready" class="paper-loading">文章未能载入，请检查上方提示后重试。</div>
        <template v-else>
          <div v-if="editing" class="markdown-editor">
            <label for="layout-title">文章标题</label>
            <input id="layout-title" v-model="title" placeholder="为文章写一个标题" maxlength="200" :disabled="saving" />
            <div class="editor-label"><label for="layout-body">正文 · Markdown</label><span>支持标题、引用、列表、图片与动画卡语法</span></div>
            <textarea id="layout-body" v-model="body" spellcheck="false" placeholder="从这里开始，写下你的内容…" :disabled="saving" />
            <p class="editor-hint">编辑完成后点击「保存排版」。修改正文将生成新文章版本，旧版本不会被覆盖。</p>
          </div>
          <div v-else class="paper-stage">
            <div class="render-status" role="status" :class="{ visible: rendering }">正在更新排版…</div>
            <div v-if="previewError" class="preview-failure" role="alert"><strong>预览暂不可用</strong><p>{{ previewError }}</p><button class="studio-button" @click="studio.renderPreview()">重新渲染</button></div>
            <iframe v-else-if="html" class="article-frame" title="微信公众号正文排版预览" :srcdoc="previewDocument" sandbox="" />
            <div v-else class="paper-loading">{{ rendering ? '正在渲染文章…' : '正文为空，请点击「编辑正文」添加内容。' }}</div>
          </div>
        </template>
        <footer class="paper-footer"><span>所见即所得 · 正文与发布使用同一渲染</span><span>微信公众号</span></footer>
      </main>

      <aside class="settings-panel" aria-label="文章排版设置">
        <div class="settings-title"><span>排版设置</span><span class="live-label">实时预览</span></div>
        <fieldset :disabled="loading || !ready || saving">
          <section class="setting-section">
            <div class="section-heading"><h2>正文风格</h2><span>10 种表达</span></div>
            <div class="style-grid"><button v-for="style in STYLE_PRESETS" :key="style.id" class="style-option" :class="{ selected: options.style === style.id }" :aria-pressed="options.style === style.id" :title="style.desc" @click="options.style = style.id">{{ style.name }}</button></div>
            <p class="setting-caption style-description">{{ currentStyle?.desc }}</p>
          </section>
          <section class="setting-section">
            <div class="section-heading"><h2>主题配色</h2><span>{{ currentColor?.name }}</span></div>
            <div class="color-grid"><button v-for="color in COLOR_PRESETS" :key="color.id" class="color-option" :class="{ selected: options.theme === color.id }" :style="{ '--swatch': color.color }" :title="color.name" :aria-label="color.name" :aria-pressed="options.theme === color.id" @click="options.theme = color.id"><span v-if="options.theme === color.id" aria-hidden="true">✓</span></button></div>
          </section>
          <section class="setting-section">
            <div class="section-heading"><h2>开篇横幅</h2><span>第一眼的印象</span></div>
            <div class="banner-grid"><button v-for="banner in BANNER_MODES" :key="banner.id" class="banner-option" :class="{ selected: options.banner === banner.id }" :aria-pressed="options.banner === banner.id" :title="banner.desc" @click="options.banner = banner.id"><span class="banner-sketch" :class="`sketch-${banner.id}`" aria-hidden="true"><i /><i /></span><span>{{ banner.label }}</span></button></div>
            <p class="setting-caption">横幅随文章标题与主题配色一起变化。</p>
          </section>
          <section class="setting-section brand-section">
            <div class="section-heading"><h2>文末关注</h2><label class="toggle"><input v-model="options.outro" type="checkbox" role="switch" aria-label="显示文末关注卡" /><span /></label></div>
            <p class="setting-caption">在文章结尾，留下你的品牌印记。</p>
            <label class="field-label" for="brand-author">品牌 / 作者</label><input id="brand-author" v-model="options.brandAuthor" class="setting-input" maxlength="80" placeholder="你的品牌或作者名" :disabled="!options.outro" />
            <label class="field-label" for="brand-avatar">头像地址</label><input id="brand-avatar" v-model="options.avatarUrl" class="setting-input" type="url" maxlength="2000" placeholder="https://mmbiz.qpic.cn/…" :disabled="!options.outro" />
            <label class="field-label" for="brand-slogan">品牌标语</label><textarea id="brand-slogan" v-model="options.brandSlogan" class="setting-input slogan" maxlength="200" placeholder="一句话，让读者记住你" :disabled="!options.outro" />
          </section>
        </fieldset>
        <div class="settings-footnote"><span aria-hidden="true">↳</span><p>配置随文章版本保存。发布时将沿用这份排版，无需重复设置。</p></div>
      </aside>
    </div>

    <div v-if="cardPanel.open" class="card-modal-backdrop" @click.self="closeCardPanel">
      <section class="card-modal" role="dialog" aria-modal="true" aria-label="生成 SVG 动画卡">
        <header class="card-modal-head">
          <div><p class="eyebrow">SVG ANIMATION CARD</p><h2>生成动画卡</h2></div>
          <button class="studio-button subtle" aria-label="关闭动画卡面板" @click="closeCardPanel">✕ 关闭</button>
        </header>
        <div class="card-modal-body">
          <section class="card-step">
            <label class="field-label" for="card-source">目标段落 <span>在正文中选中的文字会带到这里，可修改</span></label>
            <textarea id="card-source" v-model="cardPanel.text" rows="5" maxlength="8000" placeholder="粘贴或输入一段正文（至少 8 个字）。柱状图数值只能取自这段文字里已经出现的数值。" />
          </section>
          <section class="card-step">
            <div class="section-heading"><h2>卡型</h2><span>8 种动画</span></div>
            <div class="card-kind-grid">
              <button v-for="preset in SVG_CARD_PRESETS" :key="preset.id" class="card-kind-option" :class="{ selected: cardPanel.kind === preset.id }" :aria-pressed="cardPanel.kind === preset.id" :title="preset.lead" @click="cardPanel.kind = preset.id">
                <strong>{{ preset.name }}</strong><span>{{ preset.lead }}</span>
              </button>
            </div>
          </section>
          <div class="card-actions-row">
            <button class="studio-button primary" :disabled="cardPanel.generating || cardPanel.text.trim().length < 8" @click="generateCard">{{ cardPanel.generating ? '生成中…' : '生成候选' }}</button>
            <p v-if="cardPanel.error" class="card-error" role="alert">{{ cardPanel.error }}</p>
          </div>
          <template v-if="cardPanel.result">
            <section class="card-step">
              <label class="field-label" for="card-title">卡片标题</label>
              <input id="card-title" v-model="cardPanel.title" maxlength="40" placeholder="不超过 40 字" />
              <label class="field-label" for="card-lines">卡片行（每行一条）</label>
              <textarea id="card-lines" v-model="cardPanel.lines" rows="6" spellcheck="false" :placeholder="currentCardPreset?.hint" />
              <div class="card-edit-actions">
                <button class="studio-button" :disabled="cardPanel.previewing" @click="renderCardPreview">{{ cardPanel.previewing ? '预览中…' : '刷新预览' }}</button>
                <button class="studio-button primary" :disabled="cardPanel.previewing" @click="insertCard">插入正文</button>
              </div>
              <p v-if="cardPanel.previewError" class="card-error" role="alert">{{ cardPanel.previewError }}</p>
              <ul v-if="cardPanel.warnings.length" class="card-warnings"><li v-for="(warning, index) in cardPanel.warnings" :key="index">{{ warning }}</li></ul>
            </section>
            <section class="card-step">
              <iframe v-if="cardPanel.previewHtml" class="card-preview-frame" title="动画卡预览" :srcdoc="cardPreviewDocument" sandbox="" />
            </section>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.render-warnings{padding:10px 20px;font-size:11px;color:var(--color-text-secondary);border-bottom:1px solid var(--color-border)}.render-warnings.has-warnings{color:var(--color-warning)}.render-warnings summary{cursor:pointer}.render-warnings summary span{color:var(--color-text-muted);font-size:10px}.render-warnings ul{padding-left:18px;margin-bottom:0;line-height:1.8}.report-stats{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:12px;color:var(--color-text-muted);font-size:10px}.report-note{color:var(--color-text-muted);line-height:1.7;margin-bottom:0}
.layout-studio{max-width:1440px;margin:0 auto;color:var(--color-text-primary);font-family:'PingFang SC','Microsoft YaHei',sans-serif;--studio-line:var(--color-border-strong)}
.studio-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:24px}.eyebrow{font-family:Georgia,serif;font-size:10px;letter-spacing:2.4px;color:var(--color-text-muted);margin:0 0 8px}.studio-heading h1{font-size:25px;letter-spacing:-.6px;font-weight:650;margin:0}.studio-heading h1 span{font-size:13px;letter-spacing:.4px;font-weight:400;color:var(--color-text-muted);margin-left:18px}.studio-grid{display:grid;grid-template-columns:minmax(0,1fr) 296px;gap:22px;align-items:start}.manuscript{min-width:0;border:1px solid var(--studio-line);border-radius:12px;overflow:hidden;background:var(--color-surface);box-shadow:var(--shadow-sm)}.paper-toolbar{padding:14px 17px;display:flex;align-items:center;flex-wrap:wrap;gap:12px;border-bottom:1px solid var(--color-border)}.document-state{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--color-text-muted);margin-right:auto}.state-dot{width:6px;height:6px;flex-shrink:0;background:var(--color-success);border-radius:50%}.state-dot.unsaved{background:var(--color-warning)}.toolbar-actions{display:flex;gap:7px;flex-wrap:wrap}.studio-button{background:var(--color-surface);color:var(--color-text-secondary);border:1px solid var(--studio-line);border-radius:6px;font:inherit;font-size:12px;padding:7px 11px;cursor:pointer;transition:background .15s,border-color .15s;white-space:nowrap}.studio-button:hover:not(:disabled){background:var(--color-surface-muted);border-color:var(--color-accent)}.studio-button.primary{background:var(--color-accent);border-color:var(--color-accent);color:var(--color-on-accent)}.studio-button.active{color:var(--color-accent);border-color:var(--color-accent)}button:disabled{opacity:.4;cursor:not-allowed}.studio-button.subtle{border:0;background:transparent;padding-right:0;color:var(--color-text-muted)}.subtle span{margin-left:10px}.paper-meta{display:flex;justify-content:space-between;gap:10px;padding:12px 22px;font-size:10px;letter-spacing:.3px;color:var(--color-text-muted);background:var(--color-surface-raised)}.meta-divider{margin:0 6px;opacity:.4}.sample-note{padding:9px 22px;background:var(--color-accent-soft);font-size:11px;color:var(--color-text-secondary)}.paper-stage{position:relative;background:#eeede9;padding:28px clamp(12px,3vw,36px);height:calc(100vh - 280px);min-height:570px;max-height:1100px}.article-frame{display:block;background:#fff;width:100%;height:100%;border:0;border-radius:2px;box-shadow:0 4px 22px #27241c0d;color-scheme:light}.render-status{position:absolute;top:6px;right:20px;font-size:10px;color:#68645b;opacity:0;pointer-events:none}.render-status.visible{opacity:1}.paper-footer{display:flex;justify-content:space-between;gap:12px;padding:11px 18px;color:var(--color-text-muted);font-size:10px;border-top:1px solid var(--color-border)}.settings-panel{border:1px solid var(--studio-line);border-radius:12px;background:var(--color-surface);position:sticky;top:20px;overflow:hidden}.settings-title{display:flex;justify-content:space-between;align-items:center;padding:17px 19px;border-bottom:1px solid var(--color-border);font-size:14px;font-weight:600}.live-label{font-size:10px;font-weight:400;color:var(--color-accent);display:flex;align-items:center;gap:6px}.live-label:before{content:'';width:5px;height:5px;background:currentColor;border-radius:50%}fieldset{padding:0;margin:0;border:0;min-width:0}.setting-section{padding:18px 19px;border-bottom:1px solid var(--color-border)}.section-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.section-heading h2{font-size:12px;font-weight:600;margin:0}.section-heading>span{font-size:10px;color:var(--color-text-muted)}.style-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}.style-option{padding:8px 0;background:transparent;border:1px solid var(--studio-line);border-radius:5px;color:var(--color-text-secondary);font:inherit;font-size:11px;cursor:pointer}.style-option.selected{background:var(--color-accent-soft);color:var(--color-accent);border-color:var(--color-accent);font-weight:600}.setting-caption{font-size:10px;line-height:1.7;color:var(--color-text-muted);margin:10px 0 0}.style-description{min-height:17px}.color-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:11px 7px;padding:1px 0}.color-option{width:23px;height:23px;justify-self:center;border:2px solid var(--color-surface);background:var(--swatch);box-shadow:0 0 0 1px transparent;border-radius:50%;cursor:pointer;display:grid;place-items:center;color:#fff;font-size:11px;padding:0}.color-option.selected{box-shadow:0 0 0 1.5px var(--swatch)}.banner-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.banner-option{background:var(--color-surface-raised);border:1px solid var(--color-border);border-radius:6px;padding:10px 8px 8px;display:flex;flex-direction:column;align-items:center;gap:7px;font:inherit;font-size:10px;color:var(--color-text-secondary);cursor:pointer}.banner-option.selected{border-color:var(--color-accent);background:var(--color-accent-soft);color:var(--color-accent)}.banner-sketch{width:63px;height:28px;border:1px solid currentColor;opacity:.6;display:flex;flex-direction:column;justify-content:center;gap:4px;padding:5px 8px;box-sizing:border-box}.banner-sketch i{display:block;height:3px;width:80%;background:currentColor}.banner-sketch i+ i{width:55%;height:2px}.sketch-poster{border:0;background:var(--color-text-tertiary);color:var(--color-surface);align-items:center}.sketch-poster i{height:5px}.sketch-neo{border-width:2px;transform:skew(-5deg);border-radius:3px}.sketch-card{border-radius:4px}.sketch-none{border-style:dashed;align-items:center}.sketch-none i{height:1px;width:20px;transform:rotate(-25deg)}.sketch-none i+i{display:none}.toggle{display:block;position:relative;width:29px;height:17px;cursor:pointer}.toggle input{opacity:0;position:absolute;width:100%;height:100%;margin:0;cursor:pointer}.toggle span{display:block;width:29px;height:17px;border-radius:12px;background:var(--color-border-strong);pointer-events:none;transition:background .2s}.toggle span:after{content:'';display:block;position:absolute;left:2px;top:2px;background:#fff;width:13px;height:13px;border-radius:50%;transition:transform .2s}.toggle input:checked+span{background:var(--color-accent)}.toggle input:checked+span:after{transform:translateX(12px)}.toggle input:focus-visible+span{outline:2px solid var(--color-accent);outline-offset:3px}.brand-section .section-heading{margin-bottom:0}.field-label{display:block;font-size:10px;color:var(--color-text-muted);margin:14px 0 6px}.setting-input{box-sizing:border-box;width:100%;border:1px solid var(--studio-line);border-radius:5px;background:var(--color-bg);padding:8px 9px;font:inherit;font-size:11px;color:var(--color-text-primary);outline:0}.setting-input:focus{border-color:var(--color-accent)}.setting-input:disabled{opacity:.4}.slogan{height:59px;resize:vertical}.settings-footnote{display:flex;gap:9px;padding:14px 19px;background:var(--color-surface-raised);color:var(--color-text-muted)}.settings-footnote>span{font-size:18px;color:var(--color-accent)}.settings-footnote p{font-size:10px;line-height:1.8;margin:0}.studio-message{border-radius:6px;padding:11px 14px;font-size:12px;margin-bottom:14px}.studio-message.error{color:var(--color-danger);border:1px solid currentColor}.studio-message.success{color:var(--color-success);background:var(--color-accent-soft)}.text-button{font:inherit;border:0;background:none;color:inherit;text-decoration:underline;cursor:pointer;margin-left:10px}.paper-loading{min-height:500px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:20px;font-size:12px;color:var(--color-text-muted)}.loading-line{width:65px;height:2px;background:var(--color-accent);animation:pulse 1s infinite alternate}.preview-failure{padding:65px 25px;text-align:center;background:#fff;color:#6d5148;font-size:12px;min-height:250px}.preview-failure strong{font-size:16px}.preview-failure p{line-height:1.8}.markdown-editor{background:#fff;color:#252525;padding:28px;min-height:540px;color-scheme:light}.markdown-editor>label,.editor-label label{font-size:11px;color:#817a70}.markdown-editor>input{display:block;box-sizing:border-box;width:100%;font:600 23px 'Songti SC',Georgia,serif;line-height:1.6;color:#282722;border:0;border-bottom:1px solid #e8e5df;padding:10px 0 16px;margin-bottom:23px;outline:0;background:#fff}.editor-label{display:flex;gap:15px;align-items:center;justify-content:space-between;margin-bottom:13px}.editor-label span{font-size:10px;color:#a49b90}.markdown-editor>textarea{box-sizing:border-box;width:100%;min-height:450px;height:calc(100vh - 485px);resize:vertical;font:13px/2 'SFMono-Regular',Consolas,'Microsoft YaHei',monospace;border:0;color:#423f39;background:#fff;outline:none}.editor-hint{color:#999086;font-size:10px;border-top:1px solid #e8e5df;padding-top:15px;margin-bottom:0}button:focus-visible{outline:2px solid var(--color-accent);outline-offset:3px}@keyframes pulse{to{opacity:.2;transform:scaleX(.6)}}
@media(min-width:1600px){.studio-grid{grid-template-columns:minmax(0,1fr) 314px}.setting-section{padding:20px 22px}.paper-stage{padding:34px 55px}}@media(max-width:1100px){.studio-grid{gap:14px;grid-template-columns:minmax(0,1fr) 270px}.setting-section{padding:16px}.studio-heading h1 span{display:none}.paper-toolbar{padding:12px}.document-state{width:100%}.paper-stage{padding:20px 14px}.toolbar-actions{gap:5px}.studio-button{font-size:11px;padding:7px 9px}}@media(max-width:780px){.studio-grid{grid-template-columns:minmax(0,1fr)}.settings-panel{position:static}.settings-panel fieldset{display:grid;grid-template-columns:1fr 1fr}.setting-section:nth-child(odd){border-right:1px solid var(--color-border)}.paper-stage{height:700px;min-height:400px}.studio-heading{margin-bottom:18px}.studio-heading h1{font-size:22px}.eyebrow{font-size:8px;letter-spacing:1.6px}.paper-meta{padding:12px;font-size:9px}.paper-footer{font-size:9px}.markdown-editor{padding:20px}}@media(max-width:440px){.settings-panel fieldset{grid-template-columns:1fr}.setting-section:nth-child(odd){border-right:0}.toolbar-actions{width:100%}.toolbar-actions .studio-button{flex:1}.studio-heading{gap:5px}.studio-button.subtle{font-size:10px}.paper-footer span:last-child{display:none}.editor-label span{display:none}.paper-stage{padding:16px 8px}.studio-heading h1{font-size:20px}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
/* SVG 动画卡生成面板 */
.card-modal-backdrop{position:fixed;inset:0;z-index:60;background:var(--color-overlay);display:flex;align-items:center;justify-content:center;padding:16px}
.card-modal{width:min(720px,100%);max-height:min(88vh,820px);display:flex;flex-direction:column;background:var(--color-surface);border:1px solid var(--studio-line);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);overflow:hidden}
.card-modal-head{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:16px 20px;border-bottom:1px solid var(--color-border)}
.card-modal-head h2{font-size:17px;margin:0;letter-spacing:-.3px}
.card-modal-head .eyebrow{margin:0 0 4px}
.card-modal-body{padding:16px 20px 22px;overflow:auto;display:flex;flex-direction:column;gap:16px}
.card-step{display:flex;flex-direction:column;gap:8px}
.card-step .section-heading{margin:0}
.field-label{display:flex;justify-content:space-between;align-items:baseline;font-size:11px;color:var(--color-text-muted)}
.field-label span{font-size:10px;color:var(--color-text-muted)}
#card-source,#card-lines,#card-title{width:100%;font:inherit;font-size:12px;color:var(--color-text-primary);background:var(--color-surface-muted);border:1px solid var(--studio-line);border-radius:8px;padding:9px 11px}
#card-source,#card-lines{resize:vertical}
.card-kind-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.card-kind-option{display:flex;flex-direction:column;gap:3px;align-items:flex-start;padding:9px 10px;font:inherit;text-align:left;background:var(--color-surface);border:1px solid var(--studio-line);border-radius:8px;cursor:pointer;transition:border-color .15s}
.card-kind-option strong{font-size:12px}
.card-kind-option span{font-size:10px;color:var(--color-text-muted)}
.card-kind-option.selected{border-color:var(--color-accent);box-shadow:inset 0 0 0 1px var(--color-accent)}
.card-kind-option.selected strong{color:var(--color-accent)}
.card-actions-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.card-error{margin:0;font-size:11px;color:var(--color-warning)}
.card-warnings{margin:0;padding-left:18px;font-size:11px;color:var(--color-warning);line-height:1.7}
.card-edit-actions{display:flex;gap:8px}
.card-preview-frame{width:100%;height:320px;border:1px solid var(--color-border);border-radius:10px;background:#fff}
@media(max-width:560px){.card-kind-grid{grid-template-columns:repeat(2,1fr)}.card-modal-backdrop{padding:10px}}
</style>
