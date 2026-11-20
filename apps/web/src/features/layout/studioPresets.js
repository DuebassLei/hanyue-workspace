// 使用原始工作室风格标识，正文结构由服务端完整渲染，不转换为旧主题 token。
export const STYLE_PRESETS = [
  { id: 'std', name: '标准', desc: '分节标题 · 荧光强调 · 圆角卡片' },
  { id: 'mag', name: '杂志', desc: '宋体标题 · 居中细线 · 舒展留白' },
  { id: 'min', name: '极简', desc: '黑白为主 · 纯净线条 · 左对齐' },
  { id: 'geek', name: '极客', desc: '等宽标签 · 虚线框 · 终端感' },
  { id: 'bold', name: '醒目', desc: '大色块标题 · 粗圆角 · 强对比' },
  { id: 'neo', name: '质感', desc: '数字底纹 · 细线描边 · 柔和阴影' },
  { id: 'note', name: '手账', desc: '便签编号 · 荧光笔 · 纸胶带' },
  { id: 'zen', name: '雅致', desc: '印章编号 · 宋体标题 · 双线金句' },
  { id: 'bento', name: '卡片', desc: '大圆角色块 · 胶囊编号 · 轻盈扁平' },
  { id: 'dark', name: '暗夜', desc: '深色正文 · 亮色强调 · 科技感' },
]

export const COLOR_PRESETS = [
  { id: 'orange', name: '品牌橙', color: '#C9643E' },
  { id: 'blue', name: '科技蓝', color: '#2952B6' },
  { id: 'teal', name: '薄荷绿', color: '#0C7D6C' },
  { id: 'ink', name: '墨黑', color: '#1A1D23' },
  { id: 'navy', name: '藏青金', color: '#183053' },
  { id: 'violet', name: '葡萄紫', color: '#5F3F98' },
  { id: 'rose', name: '胭脂红', color: '#A6344C' },
  { id: 'amber', name: '琥珀', color: '#9D6311' },
  { id: 'morandi', name: '莫兰迪', color: '#7D6560' },
  { id: 'forest', name: '松柏金', color: '#184731' },
  { id: 'petrol', name: '孔雀青', color: '#0A5765' },
  { id: 'mist', name: '雾霾蓝', color: '#4B6D8A' },
  { id: 'matcha', name: '橄榄绿', color: '#596B3A' },
  { id: 'cocoa', name: '可可棕', color: '#603F2C' },
  { id: 'dusty', name: '豆沙粉', color: '#9D5C61' },
]

export const BANNER_MODES = [
  { id: 'poster', label: '大字海报', desc: '大标题，开篇即焦点' },
  { id: 'neo', label: '潮酷横幅', desc: '场景感与鲜明层次' },
  { id: 'card', label: '标题卡', desc: '克制、清晰的文章开场' },
  { id: 'none', label: '无横幅', desc: '直接进入正文' },
]

// 8 种 SVG 动画卡（与后端 wechat_layout_engine.SVG_CARD_INFO 对齐）。
// hint 提示每卡的行语法，会作为占位文案出现在生成面板的行编辑框里。
export const SVG_CARD_PRESETS = [
  { id: 'svg-table', name: '表格', lead: '一张表看懂', hint: '列1|列2|…（第一行表头，之后每行一条数据）' },
  { id: 'svg-bars', name: '柱状图', lead: '数据对比', hint: '标签|数值|显示文案（数值必须取自正文原文）' },
  { id: 'svg-steps', name: '步骤', lead: '照着做', hint: '步骤标题|说明' },
  { id: 'svg-timeline', name: '时间线', lead: '时间线', hint: '时间|事件' },
  { id: 'svg-breakdown', name: '拆解', lead: '拆开看', hint: '词条|解释' },
  { id: 'svg-compare', name: '对比', lead: '对比', hint: '左表头|右表头，之后每行「旧做法|新做法」' },
  { id: 'svg-concept', name: '概念', lead: '一张图看懂', hint: '第一行总述，之后「以前|节点|节点」轨道' },
  { id: 'svg-phone', name: '手机演示', lead: '演示', hint: '第一行副标题，之后「场景名|说话人|消息」' },
]

export const DEFAULT_OPTIONS = {
  style: 'std', theme: 'orange', banner: 'none', outro: true,
  brandAuthor: '', brandSlogan: '', avatarUrl: '', title: '',
}

export const SAMPLE_BODY = '# 让好内容，拥有自己的表达\n\n排版不是装饰，而是为阅读建立秩序。用适合的节奏，让每一个观点被认真看见。\n\n## 先把注意力，留给内容\n\n好的文章从清晰开始。**重点值得被强调**，段落之间也需要呼吸的空间。\n\n> 把复杂的事情说清楚，就是一种力量。\n\n## 再找到属于你的风格\n\n- 正文风格，决定文章的阅读气质\n- 主题配色，贯穿标题与重点强调\n- 开篇横幅与文末关注，让表达完整\n\n这是排版样文。你可以直接编辑体验；如需保存并发布，请从「文章管理」选择一篇真实文章进入排版。'
