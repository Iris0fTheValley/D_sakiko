import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'
import {
  EMOTION_MAP, MOTION_GROUP_SIZES,
  LONG_AUDIO_THRESHOLD_SECONDS, LONG_AUDIO_REPEAT_DELAY_SECONDS, LONG_AUDIO_MAX_REPEATS,
  IDLE_RECOVER_DELAY_MS, TIMED_IDLE_INTERVAL_MS,
  THINK_INTERVAL_FIRST, THINK_INTERVAL_SUBSEQUENT,
  EYE_OPEN_DURATION_MS, BYE_TIMEOUT_MS, CLICK_THROTTLE_MS,
} from './constants'
import type { StateMachineEvent } from './constants'

export class Live2DStateMachine {
  // ── 外部引用 ──
  private model: Live2DModel
  private ticker: Ticker
  private tickerCallback: () => void

  // ── 响应式输出 ──
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)

  // ── 事件队列 ──
  private eventQueue: StateMachineEvent[] = []

  // ── 模型状态 ──
  private modelLoaded = false

  // ── 运动状态 ──
  private motionInProgress = false

  // ── 音频状态 ──
  private audioPlaying = false
  private currentAudio: HTMLAudioElement | null = null

  // ── Stale Promise 保护 ──
  private currentMotionId = 0
  private currentAudioId = 0

  // ── 计时器 ──
  private idleRecoverDeadline = 0
  private lastIdleTime = 0
  private lastThinkTime = 0
  private thinkInterval = THINK_INTERVAL_FIRST
  private lastClickTime = 0

  // ── 长音频 ──
  private longAudioActive = false
  private longAudioGroup = ''
  private longAudioNextMotionAt = 0
  private longAudioTriggeredCount = 0

  // ── 睁眼过渡 ──
  private eyeOpenPending = false
  private eyeOpenStartTime = 0
  private eyeOpenStartL = 1.0
  private eyeOpenStartR = 1.0

  constructor(model: Live2DModel, ticker: Ticker) {
    this.model = model
    this.ticker = ticker
    this.tickerCallback = () => this.onTickerUpdate()
  }

  /** 注册 Ticker 回调，开始运行 */
  start(): void {
    this.ticker.add(this.tickerCallback)
    this.lastIdleTime = performance.now()
    this.modelLoaded = true
    console.log('[StateMachine] Started')
  }

  /** 移除 Ticker 回调，停止运行 */
  destroy(): void {
    this.ticker.remove(this.tickerCallback)
    this.stopAudio()
    this.modelLoaded = false
    console.log('[StateMachine] Destroyed')
  }

  /** 从外部（useWebSocket）推入事件 */
  pushEvent(event: StateMachineEvent): void {
    if (!this.modelLoaded) {
      console.warn('[StateMachine] Event ignored: model not loaded')
      return
    }
    this.eventQueue.push(event)
  }

  /** 每帧由 PixiJS Ticker 调用 */
  private onTickerUpdate(): void {
    const now = performance.now()
    this.processEvents(now)
    this.checkIdleRecover(now)
    this.checkTimedIdle(now)
    this.checkThinkingMotion(now)
    this.checkLongAudioLoop(now)
    this.updateEyeOpen(now)
  }

  // ── 以下方法在后续任务中实现 ──

  private processEvents(now: number): void {
    while (this.eventQueue.length > 0) {
      const event = this.eventQueue.shift()!
      switch (event.type) {
        case 'emotion': {
          // emotion 事件只处理文本和音频，动作由 motion 事件驱动（来自 Pygame _emit_motion）
          const { audio, text } = event.data

          // 打断当前音频
          if (this.currentAudio) {
            this.currentAudio.pause()
            this.currentAudio = null
            this.audioPlaying = false
          }

          // 重置计时器（新动作触发时重置所有空闲计时）
          this.idleRecoverDeadline = 0
          this.lastIdleTime = now
          this.longAudioActive = false
          this.longAudioTriggeredCount = 0

          // 并行播放音频
          if (audio) {
            const audioPath = audio.startsWith('file://') ? audio : `file:///${audio.replace(/\\/g, '/')}`
            const audioEl = new Audio(audioPath)
            this.currentAudioId++
            const audioId = this.currentAudioId
            this.currentAudio = audioEl
            this.audioPlaying = true
            audioEl.play().catch((e) => console.warn('[StateMachine] Audio play failed:', e))

            audioEl.addEventListener('loadedmetadata', () => {
              if (audioId !== this.currentAudioId) return
              if (audioEl.duration > LONG_AUDIO_THRESHOLD_SECONDS) {
                this.longAudioActive = true
                this.longAudioNextMotionAt = now + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
              }
            })

            audioEl.addEventListener('ended', () => {
              if (audioId !== this.currentAudioId) return
              this.audioPlaying = false
              this.currentAudio = null
            })
          }

          if (text) {
            this.textBubble.value = text
          }
          break
        }

        case 'motion': {
          // motion 事件来自 Pygame 的 _emit_motion：{ group, priority, timestamp }
          const { group, priority } = event.data
          if (!group) break

          this.currentMotionId++
          const motionId = this.currentMotionId

          const size = MOTION_GROUP_SIZES[group] ?? 1
          const idx = Math.floor(Math.random() * size)

          this.motionInProgress = true
          const motionPromise = this.model.motion(group, idx, priority ?? 3)

          // 更新长音频 group（音频由 emotion 事件启动时标记）
          if (this.longAudioActive && group !== 'idle_motion' && group !== 'IDLE') {
            this.longAudioGroup = group
          }

          motionPromise.then(() => {
            if (motionId !== this.currentMotionId) return
            this.motionInProgress = false
            this.idleRecoverDeadline = performance.now() + IDLE_RECOVER_DELAY_MS
            this.eyeOpenPending = true
            this.eyeOpenStartTime = performance.now()
            try {
              // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
              this.eyeOpenStartL = this.model.internalModel.getParameterValue('PARAM_EYE_L_OPEN')
              // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
              this.eyeOpenStartR = this.model.internalModel.getParameterValue('PARAM_EYE_R_OPEN')
            } catch (_e) {
              this.eyeOpenStartL = 1.0
              this.eyeOpenStartR = 1.0
            }
          }).catch((e) => {
            console.warn('[StateMachine] Motion failed:', e)
            if (motionId === this.currentMotionId) {
              this.reset()
            }
          })
          break
        }

        case 'text_generating': {
          const active = event.data.active === true
          this.isThinking.value = active
          if (active) {
            this.thinkInterval = THINK_INTERVAL_FIRST
            this.lastThinkTime = now
          } else {
            this.thinkInterval = THINK_INTERVAL_FIRST
          }
          break
        }

        case 'cancel_turn': {
          this.stopAudio()
          this.reset()
          this.textBubble.value = '...'
          break
        }

        case 'user_text': {
          if (event.data.text) {
            this.userBubble.value = event.data.text
          }
          break
        }

        case 'bye': {
          // bye 动作由 Pygame 的 _emit_motion("bye") 驱动（motion 事件）
          // 这里只设置超时关闭窗口
          this.stopAudio()
          setTimeout(() => {
            try {
              ;(window as any).electronAPI?.closeWindow()
            } catch (_e) {
              window.close()
            }
          }, BYE_TIMEOUT_MS)
          break
        }
      }
    }
  }

  private checkIdleRecover(now: number): void {
    if (
      this.motionInProgress ||
      this.audioPlaying ||
      this.isThinking.value ||
      this.longAudioActive ||
      this.idleRecoverDeadline <= 0 ||
      now <= this.idleRecoverDeadline
    ) {
      return
    }
    // 不 await，fire-and-forget（低优先级动作可被后续打断）
    this.model.motion('idle_motion', 0, 1)
    this.idleRecoverDeadline = 0
  }

  private checkTimedIdle(now: number): void {
    if (
      this.motionInProgress ||
      this.isThinking.value ||
      this.audioPlaying ||
      this.longAudioActive ||
      now - this.lastIdleTime <= TIMED_IDLE_INTERVAL_MS
    ) {
      return
    }
    this.lastIdleTime = now
    const idx = Math.floor(Math.random() * (MOTION_GROUP_SIZES['IDLE'] ?? 7))
    this.model.motion('IDLE', idx, 1).then(() => {
      this.idleRecoverDeadline = performance.now() + IDLE_RECOVER_DELAY_MS
    }).catch((e) => console.warn('[StateMachine] Timed idle failed:', e))
  }

  private checkThinkingMotion(now: number): void {
    if (
      !this.isThinking.value ||
      this.motionInProgress ||
      now - this.lastThinkTime <= this.thinkInterval * 1000
    ) {
      return
    }
    this.lastThinkTime = now
    this.thinkInterval = THINK_INTERVAL_SUBSEQUENT  // 立即切换为 15s，不等动作播完
    const idx = Math.floor(Math.random() * (MOTION_GROUP_SIZES['text_generating'] ?? 4))
    this.model.motion('text_generating', idx, 3).then(() => {
      this.idleRecoverDeadline = performance.now() + IDLE_RECOVER_DELAY_MS
    }).catch((e) => console.warn('[StateMachine] Thinking motion failed:', e))
  }

  private checkLongAudioLoop(now: number): void {
    if (
      !this.longAudioActive ||
      this.motionInProgress ||
      !this.audioPlaying ||
      this.longAudioTriggeredCount >= LONG_AUDIO_MAX_REPEATS ||
      now <= this.longAudioNextMotionAt
    ) {
      return
    }
    this.longAudioTriggeredCount++
    this.longAudioNextMotionAt = now + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
    const size = MOTION_GROUP_SIZES[this.longAudioGroup] ?? 1
    const idx = Math.floor(Math.random() * size)
    this.motionInProgress = true
    this.model.motion(this.longAudioGroup, idx, 3).then(() => {
      this.motionInProgress = false
      this.idleRecoverDeadline = performance.now() + IDLE_RECOVER_DELAY_MS
    }).catch((e) => {
      console.warn('[StateMachine] Long audio loop motion failed:', e)
      this.motionInProgress = false
    })
  }

  private updateEyeOpen(now: number): void {
    if (!this.eyeOpenPending) return

    const elapsed = now - this.eyeOpenStartTime
    if (elapsed >= EYE_OPEN_DURATION_MS) {
      this.eyeOpenPending = false
      try {
        // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
        this.model.internalModel.setParameterValue('PARAM_EYE_L_OPEN', 1.0)
        // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
        this.model.internalModel.setParameterValue('PARAM_EYE_R_OPEN', 1.0)
      } catch (_e) { /* ignore */ }
      return
    }

    const t = elapsed / EYE_OPEN_DURATION_MS
    const lerp = (start: number, end: number, factor: number) => start + (end - start) * factor
    try {
      // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
      this.model.internalModel.setParameterValue('PARAM_EYE_L_OPEN', lerp(this.eyeOpenStartL, 1.0, t))
      // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
      this.model.internalModel.setParameterValue('PARAM_EYE_R_OPEN', lerp(this.eyeOpenStartR, 1.0, t))
    } catch (_e) { /* ignore */ }
  }

  handleClick(clientX: number, width: number): void {
    if (this.motionInProgress) return
    const now = performance.now()
    if (now - this.lastClickTime < CLICK_THROTTLE_MS) return
    this.lastClickTime = now

    // 重置思考状态
    this.isThinking.value = false
    this.thinkInterval = THINK_INTERVAL_FIRST

    // 根据点击位置设置朝向参数
    try {
      const gazeParam = clientX < width / 2 ? -0.3 : 0.3
      // @ts-expect-error internalModel API not fully typed by pixi-live2d-display
      this.model.internalModel.setParameterValue('PARAM_BODY_ANGLE_X', gazeParam)
    } catch (_e) { /* ignore */ }

    const idx = Math.floor(Math.random() * (MOTION_GROUP_SIZES['IDLE'] ?? 7))
    this.model.motion('IDLE', idx, 1).catch((e) => console.warn('[StateMachine] Click motion failed:', e))
  }

  reset(): void {
    this.stopAudio()
    this.motionInProgress = false
    this.idleRecoverDeadline = 0
    this.lastIdleTime = performance.now()
    this.lastThinkTime = 0
    this.thinkInterval = THINK_INTERVAL_FIRST
    this.longAudioActive = false
    this.longAudioGroup = ''
    this.longAudioNextMotionAt = 0
    this.longAudioTriggeredCount = 0
    this.eyeOpenPending = false
    this.isThinking.value = false
    this.textBubble.value = null
    this.userBubble.value = null
    this.eventQueue.length = 0
  }

  private stopAudio(): void {
    if (this.currentAudio) {
      this.currentAudio.pause()
      this.currentAudio = null
      this.audioPlaying = false
    }
  }
}
