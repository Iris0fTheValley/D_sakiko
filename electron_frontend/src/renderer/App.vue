<script setup lang="ts">
import { shallowRef, ref, watch, computed } from 'vue'
import Live2DStage from './components/Live2DStage.vue'
import ResizeHandler from './components/ResizeHandler.vue'
import ControlsIsland from './components/controls-island/index.vue'
import { useWebSocket } from './composables/useWebSocket'
import type { Live2DStateMachine } from './statemachine/Live2DStateMachine'

const stateMachine = shallowRef<Live2DStateMachine | null>(null)
const wsConnected = ref(false)

// 将状态机的响应式属性映射到模板可直接使用的 computed
const textBubble = computed(() => stateMachine.value?.textBubble.value ?? null)
const userBubble = computed(() => stateMachine.value?.userBubble.value ?? null)
const isThinking = computed(() => stateMachine.value?.isThinking.value ?? false)

function onStateMachineReady(sm: Live2DStateMachine) {
  stateMachine.value = sm
}

// 状态机就绪后建立 WS 连接
watch(stateMachine, (sm) => {
  if (sm) {
    const { connected } = useWebSocket(sm)
    watch(connected, (val) => { wsConnected.value = val })
  }
})
</script>

<template>
  <div class="app-root">
    <div class="stage-area">
      <Live2DStage @state-machine-ready="onStateMachineReady" />
    </div>

    <Transition name="fade">
      <div v-if="textBubble" class="text-bubble character">
        {{ textBubble }}
      </div>
    </Transition>

    <Transition name="fade">
      <div v-if="userBubble" class="text-bubble user">
        {{ userBubble }}
      </div>
    </Transition>

    <Transition name="fade">
      <div v-if="isThinking" class="thinking-indicator">
        思考中...
      </div>
    </Transition>

    <ResizeHandler />
    <ControlsIsland />
  </div>
</template>

<style scoped>
.app-root {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  background: transparent;
}
.stage-area {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}
.text-bubble {
  position: absolute;
  padding: 0.5rem 1rem;
  max-width: 80%;
  text-align: center;
  font-size: 16px;
  border-radius: 0.75rem;
  background: rgba(38, 38, 38, 0.8);
  color: #d4d4d4;
  pointer-events: none;
}
.text-bubble.character {
  bottom: 4rem;
  left: 50%;
  transform: translateX(-50%);
}
.text-bubble.user {
  top: 1rem;
  right: 1rem;
}
.thinking-indicator {
  position: absolute;
  top: 0.5rem;
  left: 50%;
  transform: translateX(-50%);
  padding: 0.25rem 0.75rem;
  font-size: 12px;
  border-radius: 0.5rem;
  background: rgba(38, 38, 38, 0.8);
  color: #f59e0b;
  pointer-events: none;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
