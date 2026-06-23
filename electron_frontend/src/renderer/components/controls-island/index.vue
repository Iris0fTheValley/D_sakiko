<script setup lang="ts">
import { refDebounced, useIntervalFn } from '@vueuse/core'
import { computed, reactive, ref, watch } from 'vue'

import ControlButtonTooltip from './control-button-tooltip.vue'
import ControlButton from './control-button.vue'

declare const electronAPI: {
  toggleDevTools: () => Promise<boolean>
  toggleAlwaysOnTop: () => Promise<boolean>
}

const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleDark() {
  isDark.value = !isDark.value
  if (isDark.value) {
    document.documentElement.classList.add('dark')
    document.documentElement.classList.remove('light')
  } else {
    document.documentElement.classList.add('light')
    document.documentElement.classList.remove('dark')
  }
  localStorage.setItem('saki-theme', isDark.value ? 'dark' : 'light')
}

const expanded = ref(false)
const islandRef = ref<HTMLElement>()

const blockingOverlays = reactive(new Set<string>())
const isBlocked = computed(() => blockingOverlays.size > 0)

function setOverlay(key: string, active: boolean) {
  if (active) { blockingOverlays.add(key); return }
  blockingOverlays.delete(key)
}

defineExpose({
  get hearingDialogOpen() { return blockingOverlays.has('hearing') },
  set hearingDialogOpen(v: boolean) { setOverlay('hearing', v) },
})

const { isOutside } = (() => {
  const isOutside = ref(false)
  const handler = (e: MouseEvent) => {
    if (!islandRef.value) return
    const rect = islandRef.value.getBoundingClientRect()
    isOutside.value = !(
      e.clientX >= rect.left && e.clientX <= rect.right &&
      e.clientY >= rect.top && e.clientY <= rect.bottom
    )
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('mousemove', handler)
  }
  return { isOutside }
})()

const isOutsideAfter2seconds = refDebounced(isOutside, 1500)

watch(isOutsideAfter2seconds, (outside) => {
  if (outside && expanded.value && !isBlocked.value) { expanded.value = false }
})

watch(expanded, (isExpanded) => { if (!isExpanded) { blockingOverlays.clear() } })

useIntervalFn(() => {
  if (expanded.value && isOutside.value && !isBlocked.value) { expanded.value = false }
}, 1500)

const alwaysOnTop = ref(true)

async function toggleAlwaysOnTop() {
  try {
    const result = await electronAPI.toggleAlwaysOnTop()
    alwaysOnTop.value = result
  } catch { alwaysOnTop.value = !alwaysOnTop.value }
}

const fadeOnHover = ref(false)

function toggleFadeOnHover() {
  fadeOnHover.value = !fadeOnHover.value
}

const adjustStyleClasses = computed(() => {
  const icon = 'size-5'
  const border = 'border-2'
  const padding = 'p-2'
  return { icon, border, padding, button: `${border} ${padding}` }
})

function refreshWindow() { window.location.reload() }

async function toggleDevToolsHandler() {
  try { await electronAPI.toggleDevTools() } catch { /* dev mode only */ }
}

function closeWindow() { window.close() }
</script>

<template>
  <div ref="islandRef" fixed bottom-2 right-2>
    <div flex flex-col items-end gap-1>
      <Transition
        enter-active-class="transition-all duration-500 cubic-bezier(0.32, 0.72, 0, 1)"
        leave-active-class="transition-all duration-400 cubic-bezier(0.32, 0.72, 0, 1)"
        enter-from-class="opacity-0 translate-y-8 scale-90 blur-sm"
        leave-to-class="opacity-0 translate-y-8 scale-90 blur-sm"
      >
        <div v-if="expanded" border="1 neutral-200 dark:neutral-800" mb-2 flex flex-col gap-1 rounded-2xl p-2 backdrop-blur-xl class="bg-neutral-100/80 shadow-2xl shadow-black/20 dark:bg-neutral-900/80">
          <div grid grid-cols-3 gap-2>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button">
                <div i-solar:settings-minimalistic-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>设置</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button">
                <div i-solar:emoji-funny-square-broken :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>切换角色</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleDevToolsHandler">
                <div i-solar:code-bold-duotone :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>Vue DevTools</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="refreshWindow">
                <div i-solar:refresh-linear :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>刷新窗口</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleDark()">
                <Transition name="fade" mode="out-in">
                  <div v-if="isDark" i-solar:moon-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                  <div v-else i-solar:sun-2-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                </Transition>
              </ControlButton>
              <template #tooltip>{{ isDark ? '切换亮色模式' : '切换暗色模式' }}</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleAlwaysOnTop()">
                <div v-if="alwaysOnTop" i-solar:pin-bold :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                <div v-else i-solar:pin-linear :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300 opacity-50" />
              </ControlButton>
              <template #tooltip>{{ alwaysOnTop ? '取消置顶' : '窗口置顶' }}</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton
                :button-style="adjustStyleClasses.button"
                :class="{ 'border-primary-300/70 shadow-[0_10px_24px_rgba(0,0,0,0.22)]': fadeOnHover }"
                @click="toggleFadeOnHover()"
              >
                <Transition name="fade" mode="out-in">
                  <div v-if="fadeOnHover" i-ph:eye :class="adjustStyleClasses.icon" text="primary-700 dark:primary-300" />
                  <div v-else i-ph:eye-slash :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                </Transition>
              </ControlButton>
              <template #tooltip>{{ fadeOnHover ? '禁用悬停隐藏' : '启用悬停隐藏' }}</template>
            </ControlButtonTooltip>

            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" hover:bg-red-500 hover:text-white @click="closeWindow()">
                <div i-solar:close-circle-outline :class="adjustStyleClasses.icon" />
              </ControlButton>
              <template #tooltip>关闭</template>
            </ControlButtonTooltip>
          </div>
        </div>
      </Transition>

      <div flex flex-col gap-1>
        <ControlButtonTooltip side="left">
          <ControlButton :button-style="adjustStyleClasses.button" @click="expanded = !expanded">
            <div
              :class="[adjustStyleClasses.icon, expanded ? 'rotate-180' : 'rotate-0']"
              i-solar:alt-arrow-up-line-duotone scale-110 transition-all duration-300
              text="neutral-800 dark:neutral-300"
            />
          </ControlButton>
          <template #tooltip>{{ expanded ? '收起' : '展开' }}</template>
        </ControlButtonTooltip>

        <ControlButtonTooltip side="left">
          <ControlButton :button-style="adjustStyleClasses.button">
            <div i-ph:microphone-slash :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
          </ControlButton>
          <template #tooltip>语音配置</template>
        </ControlButtonTooltip>

        <ControlButtonTooltip side="left">
          <ControlButton :button-style="adjustStyleClasses.button" cursor-move style="-webkit-app-region: drag">
            <div i-ph:arrows-out-cardinal :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
          </ControlButton>
          <template #tooltip>拖动移动窗口</template>
        </ControlButtonTooltip>
      </div>
    </div>
  </div>
</template>
