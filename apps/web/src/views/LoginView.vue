<script setup>
import { computed, ref } from 'vue'
import { NModal } from 'naive-ui'
import { NAlert, NButton, NInput } from 'naive-ui'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()

// login | register
const mode = ref('login')
const email = ref('')
const password = ref('')
const confirm = ref('')
const displayName = ref('')
const busy = ref(false)
const error = ref('')
const activeStep = ref(0)
const steps = [
  { icon: '✳', title: '发现灵感', heading: '从好灵感开始', description: '发现值得关注的话题，找到适合自己的创作方向。', chips: ['发现热点话题', '收集创作灵感', '探索内容方向'], cards: [{ title: '热点发现', lines: ['发现正在发生的新鲜事', '浏览值得关注的话题', '寻找适合你的内容方向'] }, { title: '灵感收集', lines: ['记录有价值的想法', '整理你的创作素材', '让灵感随时可以继续'] }] },
  { icon: '✎', title: '构思内容', heading: '把想法梳理成思路', description: '让 AI 帮你梳理思路，从一个主题开始构思。', chips: ['梳理创作思路', '丰富内容角度', '保留你的表达'], cards: [{ title: '内容构思', lines: ['确定主题与创作方向', '寻找独特的切入角度', '组织内容结构'] }, { title: '创作大纲', lines: ['梳理重点与逻辑', '生成内容框架', '随时调整你的思路'] }] },
  { icon: '▧', title: '创作成稿', heading: '让好想法变成作品', description: '借助 AI 完成初稿，在工作台中继续打磨。', chips: ['辅助撰写', '调整内容', '打磨表达'], cards: [{ title: '内容初稿', lines: ['从大纲开始创作', '丰富细节和表达', '保持内容自然流畅'] }, { title: '编辑完善', lines: ['修改你关心的内容', '调整文章结构', '完成专属你的作品'] }] },
  { icon: '↗', title: '管理发布', heading: '让作品顺利抵达读者', description: '整理创作成果，管理内容和发布流程。', chips: ['管理创作内容', '安排发布', '持续积累作品'], cards: [{ title: '内容管理', lines: ['整理已完成的内容', '回顾创作过程', '管理你的作品'] }, { title: '发布准备', lines: ['检查内容信息', '安排发布计划', '分享你的作品'] }] },
]

const minLength = computed(() => session.authConfig?.passwordMinLength || 8)
const canRegister = computed(() => Boolean(session.allowRegistration))
const isRegister = computed(() => mode.value === 'register')

const hint = computed(() =>
  isRegister.value
    ? session.bootstrapAvailable
      ? '首次部署：创建第一个账号后即可使用'
      : '注册后自动登录'
    : '使用邮箱与密码登录',
)

function switchMode() {
  mode.value = isRegister.value ? 'login' : 'register'
  error.value = ''
  confirm.value = ''
}

function openAuth(nextMode = 'login') {
  mode.value = nextMode
  error.value = ''
  showAuth.value = true
}

const showAuth = ref(false)

function validate() {
  if (!email.value.trim()) return '请输入邮箱'
  if (!isRegister.value && !password.value) return '请输入密码'
  if (isRegister.value) {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) return '邮箱格式不正确'
    if (password.value.length < minLength.value) return `密码至少 ${minLength.value} 位`
    if (password.value !== confirm.value) return '两次输入的密码不一致'
  }
  return ''
}

async function submit() {
  if (busy.value) return
  const problem = validate()
  if (problem) {
    error.value = problem
    return
  }
  busy.value = true
  error.value = ''
  try {
    if (isRegister.value) {
      await session.register({
        email: email.value.trim(),
        password: password.value,
        displayName: displayName.value.trim(),
      })
    } else {
      await session.loginWithPassword(email.value.trim(), password.value)
    }
    // 成功后 session.needsLogin 变 false，App.vue 自动切到主界面
    password.value = ''
    confirm.value = ''
  } catch (err) {
    error.value = err?.message || '登录失败，请稍后再试'
  } finally {
    busy.value = false
  }
}

async function devEnter() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const subject = localStorage.getItem('hanyue-subject') || 'local-creator'
    await session.loginWithSubject(subject, '创作者')
  } catch (err) {
    error.value = err?.message || '开发态进入失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="landing">
    <header class="landing-header">
      <nav class="landing-nav" aria-label="主导航">
        <a class="landing-brand" href="#home"><img class="brand-icon" src="/mascot-plane.png" alt="" /> AI工作台</a>
        <div class="nav-links"><a class="nav-active" href="#home">首页</a><a href="#workflow">怎么用</a><a href="#features">功能</a></div>
        <a class="pill pill-lime nav-cta" href="#login" @click.prevent="openAuth('login')">开始创作 <span>→</span></a>
      </nav>
    </header>
    <main id="home" class="landing-main">
      <section class="hero">
        <div class="hero-copy">
          <div class="eyebrow">你的 AI 内容创作工作台</div>
          <h1>从选题、创作到发布，<br>让 <mark>AI 助手</mark> 帮你做完。</h1>
          <p>把灵感变成作品。从热点发现、内容构思到文章创作与发布，在一个工作台里完成。</p>
          <div class="hero-actions"><a class="pill pill-dark" href="#login" @click.prevent="openAuth('login')">开始创作 <span>→</span></a><a class="pill pill-outline" href="#workflow">看看怎么用 <span>↓</span></a></div>
          <small>为创作者打造，让每一次表达更从容。</small>
        </div>
        <div class="hero-art" aria-hidden="true">
          <div class="art-orbit"></div><div class="art-spark spark-one">✦</div><div class="art-spark spark-two">✳</div>
          <div class="art-note"><b>创作灵感</b><span>●　发现值得写的故事</span><span>●　把想法变成内容</span><span>●　让好内容被看见</span></div>
          <div class="art-mascot"><img src="/mascot-plane.png" alt="" /></div>
          <div class="art-cover"><span>CONTENT STUDIO</span><b>让每个<br>好想法<br>都被看见。</b><em>✳ 创作灵感</em></div>
        </div>
      </section>
      <section id="workflow" class="workflow">
        <div class="section-heading"><span class="section-tag">怎么用</span><h2>一条内容，四步完成</h2><p>从灵感到发布，每一步都有 AI 助手陪你一起完成。</p></div>
        <div class="step-tabs"><button v-for="(step, index) in steps" :key="step.title" type="button" :class="{ active: activeStep === index }" :aria-pressed="activeStep === index" @click="activeStep = index"><span class="step-symbol">{{ step.icon }}</span><span><small>0{{ index + 1 }}</small><b>{{ step.title }}</b></span></button></div>
        <div class="step-intro"><div><h3>{{ steps[activeStep].heading }}</h3><p>{{ steps[activeStep].description }}</p><div class="feature-chips"><span v-for="item in steps[activeStep].chips" :key="item">✓　{{ item }}</span></div></div><div class="step-controls"><small>0{{ activeStep + 1 }} / 04</small><button type="button" aria-label="上一步" @click="activeStep = (activeStep + 3) % 4">←</button><button type="button" aria-label="下一步" @click="activeStep = (activeStep + 1) % 4">→</button></div></div>
        <div class="demo-window"><div class="demo-bar"><span><img class="demo-logo" src="/mascot-plane.png" alt="" /> AI工作台 <em>{{ steps[activeStep].title }}</em></span><span class="demo-status">AI 创作助手</span></div><div class="demo-body"><aside><div class="demo-side-title"><span>✳</span><div><b>AI 创作助手</b><small>让创意自然发生</small></div></div><label>创作主题</label><div class="demo-input">输入你的想法或灵感</div><div class="demo-action">开始创作</div><div class="demo-progress">准备就绪 <b>✓</b><i></i></div></aside><div class="demo-content"><div class="demo-heading"><b>{{ steps[activeStep].heading }}</b><span>准备好了</span></div><div class="demo-cards"><div v-for="(card, index) in steps[activeStep].cards" :key="card.title" class="demo-card"><small>第 {{ index + 1 }} 步</small><b>{{ card.title }}</b><div v-for="line in card.lines" :key="line" class="demo-line">{{ line }}</div></div></div><div class="demo-bottom"><span>从一个想法开始，创造属于你的内容。</span><b>继续创作　→</b></div></div></div></div>
      </section>
      <section id="features" class="features"><div class="section-heading"><span class="section-tag">创作工具</span><h2>灵感不止，创作不停</h2><p>将常用工具放在一起，专注于真正重要的内容。</p></div><div class="feature-grid"><article><span>✳</span><h3>发现灵感</h3><p>关注热点与趋势，让每一次创作都有方向。</p></article><article><span>✎</span><h3>高效创作</h3><p>从构思到成稿，借助 AI 打磨你的表达。</p></article><article><span>↗</span><h3>轻松发布</h3><p>管理内容与发布流程，让作品顺利抵达读者。</p></article></div></section>
      <section class="closing"><div><h2>今天就让 <mark>AI 助手</mark> 开工</h2><p>从你的下一个想法开始，把灵感变成作品。</p><a class="pill pill-lime" href="#login" @click.prevent="openAuth('register')">开始创作　→</a></div><div class="closing-art" aria-hidden="true"><img src="/mascot-plane.png" alt="" /></div></section>
      <n-modal v-model:show="showAuth" preset="card" :title="isRegister ? '创建账号' : '登录工作台'" class="auth-modal" :mask-closable="!busy" :close-on-esc="!busy" style="width: min(440px, calc(100vw - 32px))">
        <div class="login-card">
      <header class="login-head">
        <div class="brand">汉月</div>
        <h1 class="login-title">{{ isRegister ? '创建账号' : '登录工作台' }}</h1>
        <p class="page-sub">{{ hint }}</p>
      </header>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">邮箱</span>
          <n-input
            v-model:value="email"
            placeholder="you@example.com"
            :input-props="{ autocomplete: 'username', name: 'email' }"
            :disabled="busy"
          />
        </label>

        <label v-if="isRegister" class="field">
          <span class="field-label">显示名</span>
          <n-input
            v-model:value="displayName"
            placeholder="留空则用邮箱前缀"
            :input-props="{ autocomplete: 'nickname' }"
            :disabled="busy"
          />
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :placeholder="isRegister ? `至少 ${minLength} 位` : '请输入密码'"
            :input-props="{
              autocomplete: isRegister ? 'new-password' : 'current-password',
              name: 'password',
            }"
            :disabled="busy"
          />
        </label>

        <label v-if="isRegister" class="field">
          <span class="field-label">确认密码</span>
          <n-input
            v-model:value="confirm"
            type="password"
            show-password-on="click"
            placeholder="再输一次"
            :input-props="{ autocomplete: 'new-password' }"
            :disabled="busy"
          />
        </label>

        <n-alert v-if="error" type="error" :bordered="false" class="login-error">
          {{ error }}
        </n-alert>

        <n-button type="primary" attr-type="submit" :loading="busy" block size="large">
          {{ isRegister ? '注册并进入' : '登录' }}
        </n-button>
      </form>

      <div class="login-foot">
        <button v-if="canRegister" type="button" class="link" @click="switchMode">
          {{ isRegister ? '已有账号？去登录' : '没有账号？立即注册' }}
        </button>
        <button
          v-if="session.devLoginAvailable"
          type="button"
          class="link link-muted"
          :disabled="busy"
          @click="devEnter"
        >
          开发态免密进入
        </button>
      </div>

      <p v-if="session.devLoginAvailable" class="login-tip">
        开发态入口不校验密码，仅建议在本人机器或内网使用；公网部署请关闭
        <code>ALLOW_DEV_LOGIN</code>。
      </p>
    </div>
      </n-modal>
    </main>
    <footer class="landing-footer"><span><img class="footer-icon" src="/mascot-plane.png" alt="" /> AI工作台</span><span>为每一个好想法，创造更多可能。</span><a href="#home">回到顶部 ↑</a></footer>
  </div>
</template>

<style scoped>
.landing { --landing-bg:#f3f3ef; --landing-ink:#161616; --landing-lime:#eaff70; --landing-muted:#85857f; background:var(--landing-bg); color:var(--landing-ink); min-height:100vh; overflow:auto; height:100vh; font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; scroll-behavior:smooth; }
.landing * { box-sizing:border-box; }
.landing a { color:inherit; text-decoration:none; }
.landing-header { background:var(--landing-ink); color:white; }
.landing-nav { max-width:1120px; height:64px; margin:auto; display:flex; align-items:center; gap:46px; padding:0 20px; }
.landing-brand { font-weight:850; font-size:20px; white-space:nowrap; display:flex; align-items:center; gap:8px; }
.brand-icon,.footer-icon,.demo-logo { display:inline-block; object-fit:cover; vertical-align:middle; border-radius:7px; width:29px; height:29px; }
.nav-links { display:flex; align-items:center; gap:12px; font-size:13px; }
.nav-links a { padding:8px 16px; color:#bcbcb6; }
.nav-links .nav-active { background:var(--landing-lime); border-radius:100px; color:var(--landing-ink); font-weight:700; }
.pill { display:inline-flex; justify-content:center; align-items:center; gap:16px; padding:13px 21px; border-radius:100px; font-weight:750; font-size:13px; border:1px solid var(--landing-ink); transition:transform .2s; }
.pill:hover { transform:translateY(-2px); }
.pill-lime { background:var(--landing-lime); color:var(--landing-ink) !important; }
.nav-cta { margin-left:auto; padding:9px 17px; }
.pill-dark { background:var(--landing-ink); color:var(--landing-lime) !important; }
.pill-outline { background:white; }
.landing-main { max-width:1120px; margin:auto; padding:0 20px; }
.hero { min-height:570px; display:grid; grid-template-columns:1fr 1fr; align-items:center; gap:32px; }
.hero-copy h1 { font-size:clamp(34px,3.5vw,52px); line-height:1.14; letter-spacing:-.045em; margin:12px 0 22px; font-weight:900; }
.landing mark { background:var(--landing-lime); color:inherit; padding:0 3px; }
.eyebrow { color:var(--landing-muted); font-size:12px; font-weight:700; letter-spacing:.09em; }
.hero-copy p,.section-heading p,.step-intro p,.features p,.closing p { color:var(--landing-muted); line-height:1.8; font-size:14px; }
.hero-copy p { max-width:500px; }
.hero-actions { display:flex; gap:10px; margin:26px 0 18px; }
.hero-copy small { font-size:11px; color:#aaa9a3; }
.hero-art { height:410px; position:relative; }
.art-orbit { position:absolute; width:315px; height:315px; border-radius:50%; background:white; top:45px; left:80px; }
.art-mascot { position:absolute; top:110px; left:155px; width:190px; height:190px; background:var(--landing-lime); border:3px solid var(--landing-ink); border-radius:43% 48% 45% 40%; transform:rotate(-9deg); box-shadow:14px 18px 0 #d0e963; display:grid; place-items:center; }
.art-mascot { overflow:hidden; }
.art-mascot img { display:block; width:100%; height:100%; object-fit:cover; }
.art-note,.art-cover { position:absolute; background:white; border:2px solid var(--landing-ink); box-shadow:5px 6px 0 #d4d4ce; border-radius:15px; z-index:1; }
.art-note { top:35px; left:10px; width:200px; padding:16px; transform:rotate(-5deg); display:grid; gap:8px; font-size:11px; }.art-note b { font-size:13px; }.art-note span { background:#f3f3ef; padding:5px; border-radius:4px; }
.art-cover { right:15px; bottom:30px; width:146px; height:205px; transform:rotate(5deg); padding:16px; display:flex; flex-direction:column; color:white; background:#424d40; }.art-cover span { font-size:9px; color:var(--landing-lime); }.art-cover b { margin:auto 0; font-size:22px; line-height:1.25; }.art-cover em { font-size:10px; font-style:normal; }
.art-spark { position:absolute; color:#cbe956; font-size:40px; }.spark-one { top:105px; right:55px; }.spark-two { bottom:65px; left:70px; }
.workflow,.features { padding:55px 0 45px; }.section-heading { text-align:center; margin-bottom:40px; }.section-heading h2 { font-size:36px; letter-spacing:-.04em; margin:12px 0 8px; font-weight:900; }.section-heading p { margin:0; }.section-tag { background:var(--landing-ink); color:var(--landing-lime); padding:5px 11px; font-size:11px; font-weight:800; border-radius:30px; }
.step-tabs { display:grid; grid-template-columns:repeat(4,1fr); background:#e7e7e1; padding:4px; border-radius:15px; gap:4px; }.step-tabs button { border:0; border-radius:11px; background:transparent; color:var(--landing-ink); display:flex; align-items:center; gap:12px; padding:10px 14px; text-align:left; cursor:pointer; }.step-tabs button.active { background:var(--landing-ink); color:var(--landing-lime); }.step-tabs small,.step-tabs b { display:block; }.step-tabs small { color:#a1a19b; font-size:10px; }.step-tabs b { font-size:13px; }.step-symbol { font-size:27px; color:#b4d438; }
.step-intro { display:flex; align-items:end; justify-content:space-between; gap:20px; margin:28px 0 22px; }.step-intro h3 { font-size:24px; margin:0; font-weight:850; }.step-intro p { margin:8px 0; }.feature-chips { display:flex; flex-wrap:wrap; gap:7px; }.feature-chips span { background:white; border-radius:30px; padding:7px 12px; font-size:11px; font-weight:700; }.step-controls { display:flex; align-items:center; gap:8px; white-space:nowrap; }.step-controls small { color:#999; margin-right:8px; }.step-controls button { border:1px solid #161616; background:white; border-radius:50%; height:37px; width:37px; cursor:pointer; }.step-controls button:last-child { background:#161616; color:var(--landing-lime); }
.demo-window { border:1px solid #161616; border-radius:14px; overflow:hidden; background:white; box-shadow:0 24px 50px #00000012; }.demo-bar { height:46px; background:#161616; color:white; display:flex; align-items:center; justify-content:space-between; padding:0 16px; font-size:13px; font-weight:800; }.demo-logo { width:22px; height:22px; font-size:12px; margin-right:6px; }.demo-bar em,.demo-status { border-radius:20px; padding:5px 9px; font-size:10px; font-style:normal; color:#161616; background:var(--landing-lime); margin-left:12px; }.demo-status { color:var(--landing-lime); background:transparent; border:1px solid var(--landing-lime); }.demo-body { display:grid; grid-template-columns:27% 1fr; min-height:350px; }.demo-body aside { padding:20px; border-right:1px solid #ddd; }.demo-side-title { display:flex; align-items:center; gap:10px; margin-bottom:28px; }.demo-side-title>span { font-size:33px; color:#a3c52a; }.demo-side-title b,.demo-side-title small { display:block; }.demo-side-title small,.demo-card small { color:#aaa; font-size:10px; }.demo-body label { font-size:11px; font-weight:700; }.demo-input,.demo-action { border:1px solid #222; border-radius:9px; padding:12px; font-size:11px; margin-top:8px; }.demo-input { color:#aaa; }.demo-action { background:#161616; color:var(--landing-lime); text-align:center; font-weight:800; }.demo-progress { background:#f3f3ef; margin-top:18px; border-radius:8px; padding:12px; font-size:10px; }.demo-progress b { float:right; }.demo-progress i { display:block; height:5px; margin-top:10px; background:#161616; border-radius:4px; }.demo-content { padding:18px; background:#f3f3ef; }.demo-heading { display:flex; justify-content:space-between; font-size:13px; margin-bottom:15px; }.demo-heading span { background:var(--landing-lime); border-radius:10px; padding:3px 8px; font-size:10px; }.demo-cards { display:grid; grid-template-columns:1fr 1fr; gap:10px; }.demo-card { border:1px solid #161616; border-radius:12px; background:white; padding:16px; min-height:230px; }.demo-card b,.demo-card small { display:block; }.demo-card b { font-size:13px; margin:2px 0 15px; }.demo-line { background:#f3f3ef; border-radius:4px; padding:9px; font-size:11px; margin:6px 0; }.demo-bottom { display:flex; justify-content:space-between; align-items:center; margin-top:12px; font-size:11px; }.demo-bottom b { padding:12px; background:#161616; color:var(--landing-lime); border-radius:8px; }
.features { padding-top:100px; }.feature-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }.feature-grid article { background:white; border:1px solid #161616; border-radius:15px; padding:24px; }.feature-grid article>span { font-size:28px; background:var(--landing-lime); border-radius:12px; display:grid; place-items:center; width:50px; height:50px; }.feature-grid h3 { margin:20px 0 5px; }.feature-grid p { margin:0; }
.closing { margin:70px 0; background:#161616; border-radius:24px; color:white; min-height:265px; display:flex; align-items:center; justify-content:space-between; padding:48px 55px; }.closing h2 { font-size:36px; margin:0; }.closing p { margin:14px 0 24px; }.closing-art { width:155px; height:155px; border-radius:50%; display:grid; place-items:center; background:var(--landing-lime); color:#161616; font-size:35px; transform:rotate(-8deg); }.closing-art strong { font-size:75px; line-height:.7; }
.closing-art { flex:0 0 auto; overflow:hidden; }
.closing-art img { display:block; width:100%; height:100%; object-fit:cover; }
.landing-footer { border-top:1px solid #e4e4de; max-width:1120px; margin:auto; padding:30px 20px; display:flex; justify-content:space-between; align-items:center; color:#999; font-size:11px; }.landing-footer>span:first-child { color:#161616; font-weight:800; }.footer-icon { width:22px; height:22px; font-size:12px; }
@media (max-width:700px) { .landing-nav { gap:10px; height:58px; }.landing-brand { font-size:16px; }.nav-links { gap:0; }.nav-links a { padding:7px; font-size:11px; }.nav-cta { padding:8px 10px; font-size:11px; }.hero { grid-template-columns:1fr; padding-top:55px; gap:0; }.hero-copy h1 { font-size:34px; }.hero-art { height:340px; transform:scale(.8); transform-origin:top center; }.workflow { padding-top:10px; }.section-heading h2 { font-size:28px; }.step-tabs button { padding:8px 4px; gap:3px; }.step-symbol { font-size:20px; }.step-tabs b { font-size:11px; }.step-intro { align-items:start; }.step-intro h3 { font-size:20px; }.feature-chips span { font-size:10px; }.demo-body { grid-template-columns:1fr; }.demo-body aside { border-right:0; border-bottom:1px solid #ddd; }.demo-card { padding:10px; min-height:190px; }.demo-line { font-size:10px; }.feature-grid { grid-template-columns:1fr; }.features { padding-top:70px; }.closing { padding:30px; min-height:200px; }.closing h2 { font-size:27px; }.closing-art { width:95px; height:95px; flex:none; font-size:20px; }.closing-art strong { font-size:45px; }.landing-footer { gap:12px; flex-wrap:wrap; } }

.login-card {
  width: 100%;
  max-width: 400px;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  background: white;
  border: 1px solid var(--landing-ink);
  border-radius: 16px;
  padding: 28px;
  color: var(--landing-ink);
}

.login-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.brand {
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: var(--font-sm);
  letter-spacing: 0.32em;
  color: var(--color-text-secondary);
  text-transform: uppercase;
}

.login-title {
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: var(--font-display);
  font-weight: 650;
  line-height: 1.2;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field-label {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.login-error {
  margin: 0;
}

.login-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.link {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: var(--font-sm);
  color: var(--color-accent);
}

.link:hover {
  text-decoration: underline;
}

.link-muted {
  color: var(--color-text-secondary);
}

.link:disabled {
  opacity: 0.5;
  cursor: default;
}

.login-tip {
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-tertiary, var(--color-text-secondary));
  margin: 0;
}

.login-tip code {
  font-family: 'Fira Code', monospace;
  font-size: 0.92em;
}
</style>
