<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import type { Application, Ticker } from 'pixi.js'
import { Live2DStateMachine } from '../statemachine/Live2DStateMachine'

const props = defineProps<{ modelPath?: string }>()
const emit = defineEmits<{ stateMachineReady: [sm: Live2DStateMachine] }>()

const canvasContainer = ref<HTMLDivElement>()
let app: Application | null = null
let sm: Live2DStateMachine | null = null

function onCanvasClick(e: MouseEvent) {
  if (sm && canvasContainer.value) {
    sm.handleClick(e.clientX, canvasContainer.value.clientWidth)
  }
}

onMounted(async () => {
  const { Application, Ticker } = await import('pixi.js')
  const { Live2DModel } = await import('pixi-live2d-display/cubism2')

  // 教训 #5：注册 Ticker，缺了不渲染
  Live2DModel.registerTicker(Ticker)

  const canvas = canvasContainer.value?.querySelector('canvas') as HTMLCanvasElement
  if (!canvas) return

  app = new Application({
    view: canvas,
    width: canvasContainer.value!.clientWidth,
    height: canvasContainer.value!.clientHeight,
    backgroundAlpha: 0,
    antialias: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  })

  try {
    const modelSrc = props.modelPath || '/live2d/sakiko/live2D_model/3.model.json'
    const live2dModel = await Live2DModel.from(modelSrc, { autoInteract: false })

    // 调整位置和大小
    live2dModel.scale.set(0.3)
    live2dModel.anchor.set(0.5, 0.5)
    live2dModel.x = app.screen.width / 2
    live2dModel.y = app.screen.height / 2
    app.stage.addChild(live2dModel)

    // 创建状态机并启动
    sm = new Live2DStateMachine(live2dModel, Ticker.shared)
    sm.start()
    emit('stateMachineReady', sm)
    console.log('[Live2DStage] Model loaded, state machine started')
  } catch (e) {
    console.error('[Live2DStage] Failed to load model:', e)
  }
})

onUnmounted(() => {
  sm?.destroy()
  sm = null
  app?.destroy(true)
})
</script>

<template>
  <div ref="canvasContainer" class="live2d-container" @click="onCanvasClick">
    <canvas></canvas>
  </div>
</template>

<style scoped>
.live2d-container {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}
.live2d-container canvas {
  width: 100%;
  height: 100%;
}
</style>
