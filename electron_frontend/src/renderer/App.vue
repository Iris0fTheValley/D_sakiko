<script setup lang="ts">
import { shallowRef, ref, onUnmounted, computed, onMounted, provide } from 'vue'
import { refDebounced } from '@vueuse/core'
import Live2DStage from './components/Live2DStage.vue'
import ResizeHandler from './components/ResizeHandler.vue'
import ControlsIsland from './components/controls-island/index.vue'
import type { Live2DStateMachine } from './statemachine'

const stateMachine = shallowRef<Live2DStateMachine | null>(null)
const wsConnected = ref(false)
const textBubble = computed(() => stateMachine.value?.textBubble.value ?? null)
const userBubble = computed(() => stateMachine.value?.userBubble.value ?? null)
const isThinking = computed(() => stateMachine.value?.isThinking.value ?? false)

// 模型切换（由 WS 事件驱动）
const currentCharKey = ref('sakiko')
const sakikoState = ref(true)  // true=黑祥(costume), false=白祥(base)，默认黑祥
const customModelPath = ref('')  // 自定义模型路径（Qt 切模型时设置）
const currentModelPath = computed(() => {
  if (customModelPath.value) return customModelPath.value
  if (currentCharKey.value === 'sakiko' && sakikoState.value) {
    return '/live2d/sakiko/live2D_model_costume/3.model.json'
  }
  return `/live2d/${currentCharKey.value}/live2D_model/3.model.json`
})
const stageKey = ref(0)

// ── 悬停淡出（airi fade-on-hover）──
const fadeOnHoverEnabled = ref(false)
const mouseX = ref(0)
const mouseY = ref(0)
const mouseInWindow = ref(true)
const isOverModel = computed(() => {
  if (!mouseInWindow.value) return false
  const mx = 0.2
  return mouseX.value > window.innerWidth * mx
      && mouseX.value < window.innerWidth * (1 - mx)
      && mouseY.value > window.innerHeight * mx
      && mouseY.value < window.innerHeight * (1 - mx)
})
const shouldFade = computed(() => fadeOnHoverEnabled.value && isOverModel.value)

// ── 窗口边框高亮（airi border highlight）──
const _nearBorder = ref(false)
const nearBorder = refDebounced(_nearBorder, 250)

onMounted(() => {
  // 悬停淡出鼠标追踪
  window.addEventListener('mousemove', (e) => {
    mouseX.value = e.clientX; mouseY.value = e.clientY
  })
  document.addEventListener('mouseleave', () => { mouseInWindow.value = false })
  document.addEventListener('mouseenter', () => { mouseInWindow.value = true })

  // 边框高亮鼠标追踪
  setInterval(async () => {
    try {
      const pos = await (window as any).electronAPI?.getMousePosition()
      const bounds = await (window as any).electronAPI?.getWindowBounds()
      if (!pos || !bounds) return
      const rx = pos.x - bounds.x
      const ry = pos.y - bounds.y
      const t = 10
      _nearBorder.value = rx >= -t && rx <= bounds.width + t && ry >= -t && ry <= bounds.height + t
        && (rx <= t || rx >= bounds.width - t || ry <= t || ry >= bounds.height - t)
    } catch {}
  }, 200)
})

function toggleFadeOnHover() {
  fadeOnHoverEnabled.value = !fadeOnHoverEnabled.value
}

provide('fadeOnHoverEnabled', fadeOnHoverEnabled)
provide('toggleFadeOnHover', toggleFadeOnHover)

function reloadModel(charKey: string, costumeMode: boolean | null = null) {
  disconnectWebSocket()
  stateMachine.value?.destroy()
  stateMachine.value = null
  currentCharKey.value = charKey
  if (costumeMode !== null) sakikoState.value = costumeMode
  stageKey.value++
}

function reloadCustomModel(path: string) {
  disconnectWebSocket()
  stateMachine.value?.destroy()
  stateMachine.value = null
  customModelPath.value = path
  stageKey.value++
}

function onStateMachineReady(sm: Live2DStateMachine) {
  stateMachine.value = sm
  connectWebSocket(sm)
}

// ── WebSocket ──
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000

function connectWebSocket(sm: Live2DStateMachine) {
  if (ws) { try { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; ws.close() } catch(_){}; ws=null }
  try { ws = new WebSocket('ws://localhost:9876') } catch(e) { scheduleReconnect(sm); return }
  ws.onopen = () => { wsConnected.value=true; reconnectDelay=1000; console.log('[WS] Connected'); sm.reset() }
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      // switch_live2d: 切换角色或模型
      if (msg.type === 'switch_live2d' && msg.data?.character_name) {
        const modelUrl = msg.data.model_url
        // 有自定义模型 → 直接加载
        if (modelUrl) { reloadCustomModel(modelUrl); return }
        // 切换角色
        const m: Record<string,string> = {'祥子':'sakiko','爱音':'anon','素世':'soyo'}
        const key = m[msg.data.character_name]
        if (key && key !== currentCharKey.value) { reloadModel(key); return }
      }
      // char_converted: 黑白祥 / mask
      if (msg.type === 'char_converted') {
        const v = msg.data?.value
        if (v === false || v === 0) { reloadModel('sakiko', false); return }
        if (v === true || v === 1) { reloadModel('sakiko', true); return }
      }
      sm.pushEvent({ type: msg.type, data: msg.data })
    } catch(e) { console.warn('[WS] Parse:', e) }
  }
  ws.onclose = () => { wsConnected.value=false; scheduleReconnect(sm) }
  ws.onerror = () => { ws?.close() }
}

function scheduleReconnect(sm: Live2DStateMachine) {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => { reconnectDelay = Math.min(reconnectDelay*2, 30000); connectWebSocket(sm) }, reconnectDelay)
}

function disconnectWebSocket() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; try{ws.close()}catch(_){}; ws=null }
}

onUnmounted(() => disconnectWebSocket())
</script>

<template>
  <div class="app-root">
    <div class="stage-area" :class="{ 'pointer-events-none': fadeOnHoverEnabled }" :style="{ transition: 'opacity 0.25s ease-in-out', opacity: shouldFade ? 0 : 1 }">
      <Live2DStage :key="stageKey" :model-path="currentModelPath" :model-key="currentCharKey" @state-machine-ready="onStateMachineReady" />
    </div>
    <Transition name="fade"><div v-if="textBubble" class="text-bubble character">{{ textBubble }}</div></Transition>
    <Transition name="fade"><div v-if="userBubble" class="text-bubble user">{{ userBubble }}</div></Transition>
    <Transition name="fade"><div v-if="isThinking" class="thinking-indicator">思考中...</div></Transition>
    <ResizeHandler />
    <ControlsIsland />

    <!-- 窗口边框高亮 -->
    <div v-if="nearBorder" style="position:fixed;top:0;left:0;right:0;bottom:0;z-index:9999;pointer-events:none;border:4px solid rgba(59,130,246,0.5);border-radius:1rem;animation:pulse 2s infinite;"></div>
  </div>
</template>

<style scoped>
.app-root { width:100%; height:100%; position:relative; overflow:hidden; background:transparent; }
.stage-area { width:100%; height:100%; position:absolute; top:0; left:0; }
.text-bubble { position:absolute; padding:.5rem 1rem; max-width:80%; text-align:center; font-size:16px; border-radius:.75rem; background:rgba(38,38,38,.8); color:#d4d4d4; pointer-events:none; }
.text-bubble.character { bottom:4rem; left:50%; transform:translateX(-50%); }
.text-bubble.user { top:1rem; right:1rem; }
.thinking-indicator { position:absolute; top:.5rem; left:50%; transform:translateX(-50%); padding:.25rem .75rem; font-size:12px; border-radius:.5rem; background:rgba(38,38,38,.8); color:#f59e0b; pointer-events:none; }
.fade-enter-active,.fade-leave-active { transition:opacity .3s ease; }
.fade-enter-from,.fade-leave-to { opacity:0; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
