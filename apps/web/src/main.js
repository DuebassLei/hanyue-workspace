import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import 'vfonts/Lato.css'
import 'vfonts/FiraCode.css'
import './styles/theme.css'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
