import { ref, onUnmounted } from 'vue'
import type { Live2DStateMachine } from '../statemachine/Live2DStateMachine'

export function useWebSocket(
  stateMachine: Live2DStateMachine,
  url: string = 'ws://localhost:9876'
) {
  const connected = ref(false)
  const retryCount = ref(0)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000

  function connect() {
    if (ws?.readyState === WebSocket.OPEN) return
    try {
      ws = new WebSocket(url)
    } catch (e) {
      console.error('[WS] Failed to create WebSocket:', e)
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      retryCount.value = 0
      reconnectDelay = 1000
      console.log('[WS] Connected')
      // 重连后重置状态机，防止中间状态残留
      stateMachine.reset()
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        stateMachine.pushEvent({ type: msg.type, data: msg.data })
      } catch (e) {
        console.warn('[WS] Failed to parse message:', e)
      }
    }

    ws.onclose = () => {
      connected.value = false
      retryCount.value++
      scheduleReconnect()
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, 30000)
      connect()
    }, reconnectDelay)
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.onopen = null
      ws.onclose = null
      ws.onerror = null
      ws.onmessage = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close()
      }
      ws = null
    }
  }

  onUnmounted(disconnect)
  connect()

  return { connected, retryCount, connect, disconnect }
}
