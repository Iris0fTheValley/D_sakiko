<script setup lang="ts">
import { shallowRef, ref, onUnmounted, computed, onMounted, provide } from 'vue'
import Live2DStage from './components/Live2DStage.vue'
import ResizeHandler from './components/ResizeHandler.vue'
import ControlsIsland from './components/controls-island/index.vue'
import type { Live2DStateMachine } from './statemachine'
import type { ProtocolMessage } from './statemachine/constants'

const stateMachine = shallowRef<Live2DStateMachine | null>(null)
const wsConnected = ref(false)
const textBubble = computed(() => stateMachine.value?.textBubble.value ?? null)
const userBubble = computed(() => stateMachine.value?.userBubble.value ?? null)
const isThinking = computed(() => stateMachine.value?.isThinking.value ?? false)

// 模型切换（由 WS 事件驱动）
const currentCharKey = ref('sakiko')
// 所有模型统一走 Bridge HTTP，初始默认黑祥
const customModelPath = ref('http://127.0.0.1:9877/model/sakiko/live2D_model_costume/3.model.json')
const pendingModelToken = ref('')
const initialExpression = ref('serious')
const themeColor = ref('#7799CC')
const nearBorder = ref(false)
let borderPollTimer: ReturnType<typeof setInterval> | null = null
const rendererId = sessionStorage.getItem('live2d-renderer-id') || (() => {
  const id = crypto.randomUUID(); sessionStorage.setItem('live2d-renderer-id', id); return id
})()
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

onMounted(() => {
  // 悬停淡出鼠标追踪
  window.addEventListener('mousemove', (e) => {
    mouseX.value = e.clientX; mouseY.value = e.clientY
  })
  document.addEventListener('mouseleave', () => { mouseInWindow.value = false })
  document.addEventListener('mouseenter', () => { mouseInWindow.value = true })

  // Reuse the donor/AIRI edge detection. Screen-coordinate polling continues
  // to work while mouse-through is enabled, when renderer mouse events stop.
  borderPollTimer = setInterval(async () => {
    try {
      const [pos, bounds] = await Promise.all([
        window.electronAPI.getMousePosition(),
        window.electronAPI.getWindowBounds(),
      ])
      const x = pos.x - bounds.x
      const y = pos.y - bounds.y
      const threshold = 10
      const nearWindow = x >= -threshold && x <= bounds.width + threshold
        && y >= -threshold && y <= bounds.height + threshold
      nearBorder.value = nearWindow && (
        x <= threshold || x >= bounds.width - threshold
        || y <= threshold || y >= bounds.height - threshold
      )
    } catch {
      nearBorder.value = false
    }
  }, 100)
})

function toggleFadeOnHover() {
  fadeOnHoverEnabled.value = !fadeOnHoverEnabled.value
}

function setThemeColor(color: unknown) {
  if (typeof color !== 'string' || !/^#[0-9a-f]{6}$/i.test(color)) return
  themeColor.value = color.toUpperCase()
}

provide('fadeOnHoverEnabled', fadeOnHoverEnabled)
provide('toggleFadeOnHover', toggleFadeOnHover)

function reloadCustomModel(path: string, charKey?: string, expression?: string, nextThemeColor?: unknown) {
  stateMachine.value = null
  customModelPath.value = path
  if (charKey) currentCharKey.value = charKey
  if (expression) initialExpression.value = expression
  setThemeColor(nextThemeColor)
  stageKey.value++
}

function onStateMachineReady(sm: Live2DStateMachine) {
  stateMachine.value = sm
  if (ws?.readyState === WebSocket.OPEN) sm.reportReady()
  else connectWebSocket()
}

function createProtocolMessage(type: string, data: Record<string, any>): ProtocolMessage {
  return {
    v: 1,
    type,
    event_id: crypto.randomUUID(),
    session_id: sessionStorage.getItem('live2d-session-id') || (() => {
      const id = crypto.randomUUID(); sessionStorage.setItem('live2d-session-id', id); return id
    })(),
    source: 'electron-renderer',
    timestamp: Date.now() / 1000,
    data,
  }
}

// ── WebSocket ──
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000

function connectWebSocket() {
  if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return
  if (ws) { try { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; ws.close() } catch(_){}; ws=null }
  try { ws = new WebSocket('ws://localhost:9876') } catch(e) { scheduleReconnect(); return }
  ws.onopen = () => {
    wsConnected.value = true
    reconnectDelay = 1000
    stateMachine.value?.reset()
    ws?.send(JSON.stringify(createProtocolMessage('renderer_hello', {
      capabilities: ['motion', 'audio', 'lipsync', 'snapshot'],
      model_key: currentCharKey.value,
      model_token: pendingModelToken.value,
      renderer_id: rendererId,
    })))
    stateMachine.value?.reportReady()
  }
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as Partial<ProtocolMessage> & { data?: any }
      if (msg.v !== 1) {
        console.warn('[WS] Ignoring unversioned bridge message')
        return
      }
      if (msg.type === 'live2d_command') {
        const command = msg.data?.command || msg.data
        const targets = command?.data?.target_renderer_ids
        const target = command?.data?.target_renderer_id
        if ((Array.isArray(targets) && targets.length > 0 && !targets.includes(rendererId))
          || (target && target !== rendererId)) return
        if (command?.type === 'load_model' && command.data?.model?.model_url) {
          pendingModelToken.value = String(command.data.token || '')
          reloadCustomModel(
            command.data.model.model_url,
            command.data.model.character_folder,
            command.data.model.initial_expression,
            command.data.model.theme_color,
          )
          return
        }
        if (command?.type === 'set_theme_color') {
          setThemeColor(command.data?.theme_color)
          return
        }
        if (command?.type) stateMachine.value?.pushCommand({ type: command.type, event_id: msg.event_id, session_id: msg.session_id, data: command.data || command })
        return
      }
      if (msg.type === 'model_switch' && msg.data?.model_url) {
        reloadCustomModel(msg.data.model_url, msg.data.character_folder, msg.data.initial_expression, msg.data.theme_color)
        return
      }
      if (msg.type === 'renderer_snapshot' && Array.isArray(msg.data?.commands)) {
        for (const command of msg.data.commands) {
          if (command?.type) stateMachine.value?.pushCommand(command)
        }
      }
    } catch(e) { console.warn('[WS] Parse:', e) }
  }
  ws.onclose = () => { wsConnected.value=false; ws=null; scheduleReconnect() }
  ws.onerror = () => { ws?.close() }
}

function onRendererFact(fact: { type: string; event_id?: string; data: Record<string, any> }) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return
  const message = createProtocolMessage(fact.type, fact.data)
  ws.send(JSON.stringify(message))
}

provide('sendRendererIntent', (intent: string) => {
  onRendererFact({ type: 'renderer_intent', data: { intent } })
})

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => { reconnectDelay = Math.min(reconnectDelay*2, 30000); connectWebSocket() }, reconnectDelay)
}

function disconnectWebSocket() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; try{ws.close()}catch(_){}; ws=null }
}

onUnmounted(() => {
  disconnectWebSocket()
  if (borderPollTimer) clearInterval(borderPollTimer)
  borderPollTimer = null
})
</script>

<template>
  <div class="app-root">
    <div class="stage-area" :class="{ 'pointer-events-none': fadeOnHoverEnabled }" :style="{ transition: 'opacity 0.25s ease-in-out', opacity: shouldFade ? 0 : 1 }">
      <Live2DStage :key="stageKey" :model-path="customModelPath" :model-key="currentCharKey" :model-token="pendingModelToken" :initial-expression="initialExpression" :renderer-id="rendererId" @state-machine-ready="onStateMachineReady" @renderer-fact="onRendererFact" />
    </div>
    <Transition name="fade"><div v-if="textBubble" class="text-bubble character">{{ textBubble }}</div></Transition>
    <Transition name="fade"><div v-if="userBubble" class="text-bubble user">{{ userBubble }}</div></Transition>
    <Transition name="fade"><div v-if="isThinking" class="thinking-indicator">思考中...</div></Transition>
    <ResizeHandler />
    <ControlsIsland />

    <div
      class="window-edge-highlight"
      :class="{ 'is-visible': nearBorder }"
      :style="{ '--window-theme-color': themeColor }"
      aria-hidden="true"
    />
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

.window-edge-highlight {
  position: fixed;
  inset: 0;
  z-index: 9999;
  box-sizing: border-box;
  pointer-events: none;
  border: 1px solid var(--window-theme-color);
  border-radius: 12px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 280ms ease, visibility 0s linear 280ms,
    border-color 700ms ease, box-shadow 700ms ease;
}
.window-edge-highlight.is-visible {
  opacity: 0.68;
  visibility: visible;
  animation: window-edge-breathe 3.6s ease-in-out infinite;
}
@keyframes window-edge-breathe {
  0%, 100% {
    opacity: 0.52;
    box-shadow: 0 0 4px color-mix(in srgb, var(--window-theme-color) 22%, transparent);
  }
  50% {
    opacity: 0.78;
    box-shadow: 0 0 7px color-mix(in srgb, var(--window-theme-color) 32%, transparent);
  }
}
@media (prefers-reduced-motion: reduce) {
  .window-edge-highlight.is-visible {
    animation: none;
  }
}
</style>
