/**
 * 扫描前端源码里绕过主题变量的颜色。
 * 不进构建门禁：导出成品、设备外壳、色板数据和代码块深底有意保留。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const root = new URL('../src/', import.meta.url)
const src = root.pathname.replace(/^\/([A-Za-z]:)/, '$1')

const allow = new Set([
  'styles/theme.css',
  'ui/theme.js',
  'features/shell/AccentMenu.vue',
  'features/sticker/StickerIdeateStep.vue',
  'features/cards/constants.js',
  'features/layout/PhonePreview.vue',
  'features/layout/ThemeThumb.vue',
  'features/layout/ComponentLibraryTab.vue',
  'features/cards/MarkCardStudio.vue',
  'features/cards/BackendContractModal.vue',
  'features/video/VideoWorkspace.vue',
  'views/StoryboardsView.vue',
  'views/StudioView.vue',
  'views/ImageGenView.vue',
  'composables/useWechatPublish.js',
])

const color = /#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.(vue|css|js)$/.test(name)) out.push(abs)
  }
  return out
}

const hits = []
for (const file of walk(src)) {
  const rel = relative(src, file).split(sep).join('/')
  if (allow.has(rel)) continue
  const lines = readFileSync(file, 'utf8').split(/\r?\n/)
  lines.forEach((line, index) => {
    if (color.test(line)) hits.push(`${rel}:${index + 1}: ${line.trim()}`)
  })
}

if (hits.length) {
  console.error(`发现 ${hits.length} 处未跟随主题的颜色：\n${hits.join('\n')}`)
  process.exit(1)
}
console.log('主题颜色检查通过')
