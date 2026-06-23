import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'
import {
  EMOTION_MAP, MOTION_GROUP_SIZES, MODEL_SPECIFIC_SIZES,
  LONG_AUDIO_THRESHOLD_SECONDS, LONG_AUDIO_REPEAT_DELAY_SECONDS, LONG_AUDIO_MAX_REPEATS,
  IDLE_RECOVER_DELAY_MS, TIMED_IDLE_INTERVAL_MS,
  THINK_INTERVAL_FIRST, THINK_INTERVAL_SUBSEQUENT,
  EYE_OPEN_DURATION_MS, BYE_TIMEOUT_MS, CLICK_THROTTLE_MS,
} from './constants'
import type { StateMachineEvent } from './constants'

export class Live2DStateMachine {
  // ── 模型 ──
  private model: Live2DModel
  private ticker: Ticker
  private tickerCallback: () => void
  private modelKey = 'sakiko'

  // ── 输出 ──
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)

  // ── 1:1 复刻 Pygame 变量 ──
  // motion_is_over: bool — 当前动作是否结束
  private motionIsOver = true
  // think_motion_is_over: bool — 思考动作是否结束
  private thinkMotionIsOver = true
  // live2d_this_turn_motion_complete: bool — 本轮音频是否播完
  private turnMotionComplete = true
  // idle_recover_timer: float — 模块级全局，设为一个过去时间戳模拟"早早开始计时"
  private idleRecoverTimer = 0  // 在 start() 中设为 past time
  // last_saved_time: float — 25s 待机计时器
  private lastSavedTime = 0
  // last_saved_time_think: float — 思考间隔计时器
  private lastThinkTime = 0
  // interval_think: int — 思考间隔（首次 1，后续 15）
  private thinkInterval = THINK_INTERVAL_FIRST
  // if_bye: bool
  private ifBye = false
  // mouse_position_x: 点击标记（None=未点击, 0=已处理）
  private mouseClicked = false

  // ── 音频 ──
  private audioPlaying = false
  private currentAudio: HTMLAudioElement | null = null
  private currentAudioId = 0

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

  // ── 口型同步 ──
  private mouthSyncFrameCount = 0
  private mouthOpenValue = 0
  private lipSyncN = 2.8
  private _mouthParamIndex = -1
  private audioContext: AudioContext | null = null
  private analyserNode: AnalyserNode | null = null

  // ── Stale Promise ──
  private currentMotionId = 0
  private lastClickTime = 0
  private eventQueue: StateMachineEvent[] = []
  private modelLoaded = false

  constructor(model: Live2DModel, ticker: Ticker, modelKey?: string) {
    this.model = model
    this.ticker = ticker
    if (modelKey) this.modelKey = modelKey
    this.tickerCallback = () => this.onTickerUpdate()
  }

  private getMotionSize(group: string): number {
    return MODEL_SPECIFIC_SIZES[this.modelKey]?.[group] ?? MOTION_GROUP_SIZES[group] ?? 1
  }

  start(): void {
    this.ticker.add(this.tickerCallback, undefined, 30 as any)
    // Pygame: idle_recover_timer 是模块级全局，在 import 时就设了
    // 模拟：设为 2.5s 前，确保第一帧就触发 idle_motion
    this.idleRecoverTimer = performance.now() - IDLE_RECOVER_DELAY_MS - 1
    this.lastSavedTime = performance.now()
    this.modelLoaded = true
    this._initMouth()
    this._initBlinkBreath()
    console.log('[StateMachine] Started')
  }

  destroy(): void {
    this.ticker.remove(this.tickerCallback, undefined)
    this.stopAudio()
    if (this.audioContext) { try { this.audioContext.close() } catch (_) {}; this.audioContext = null }
    this.modelLoaded = false
    console.log('[StateMachine] Destroyed')
  }

  pushEvent(event: StateMachineEvent): void {
    if (!this.modelLoaded) { this.eventQueue.push(event); return }
    this.eventQueue.push(event)
  }

  // ── 主循环：每帧由 Ticker 调用 ──
  private onTickerUpdate(): void {
    const now = performance.now()
    this.processEvents(now)
    this.checkThinking(now)
    this.checkIdleRecover(now)
    this.checkTimedIdle(now)
    this.checkClick(now)
    this.turnMotionComplete = !this.audioPlaying
    this.checkLongAudioLoop(now)
    this.updateEyeOpen(now)
  }

  // ── processEvents: 消费 WS 事件队列 ──
  private processEvents(now: number): void {
    while (this.eventQueue.length > 0) {
      const event = this.eventQueue.shift()!
      switch (event.type) {
        case 'emotion': {
          // emotion 事件：驱动动作 + 文本 + 音频
          const { label, audio, text } = event.data

          if (label === 'bye') {
            this._resetLongAudio()
            if (!this.ifBye) {
              this.motionIsOver = false
              this._playMotion('bye', 3)
            }
            this.ifBye = true
            setTimeout(() => { try { (window as any).electronAPI?.closeWindow() } catch (_) { window.close() } }, BYE_TIMEOUT_MS)
            break
          }

          const group = EMOTION_MAP[label]
          this.stopAudio()
          this._resetLongAudio()
          this.thinkMotionIsOver = true  // 有情感标签 → 停止思考

          // 播放音频
          if (audio) {
            this.currentAudioId++
            const aid = this.currentAudioId
            const el = new Audio()
            el.crossOrigin = 'anonymous'
            el.src = audio
            this.currentAudio = el
            this.audioPlaying = true
            el.play().catch(() => {})
            this._setupLipSync(el, aid)
            el.addEventListener('loadedmetadata', () => {
              if (aid !== this.currentAudioId) return
              if (el.duration > LONG_AUDIO_THRESHOLD_SECONDS) {
                this.longAudioActive = true
                this.longAudioNextMotionAt = now + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
                this.longAudioGroup = group || ''
              }
            })
            el.addEventListener('ended', () => {
              if (aid !== this.currentAudioId) return
              this.audioPlaying = false
              this.currentAudio = null
            })
          }

          // 启动动作
          if (group) {
            this.motionIsOver = false
            this._playMotion(group, 3)
          }
          if (text) this.textBubble.value = text
          break
        }

        case 'text_generating': {
          const active = event.data.active === true
          this.isThinking.value = active
          if (!active) {
            this.thinkInterval = THINK_INTERVAL_FIRST
          }
          break
        }

        case 'cancel_turn': {
          this.stopAudio()
          this.motionIsOver = true
          this.thinkMotionIsOver = true
          this.turnMotionComplete = true
          this.textBubble.value = '...'
          this._resetLongAudio()
          break
        }

        case 'user_text': {
          if (event.data.text) this.userBubble.value = event.data.text
          break
        }

        case 'bye': {
          this.stopAudio()
          setTimeout(() => { try { (window as any).electronAPI?.closeWindow() } catch (_) { window.close() } }, BYE_TIMEOUT_MS)
          break
        }

        case 'switch_live2d': {
          // Qt 切换对话 → Electron 播放 change_character 动作
          this._resetLongAudio()
          this.motionIsOver = false
          this.thinkMotionIsOver = true
          this._playMotion('change_character', 3)
          break
        }
      }
    }
  }

  // ── checkThinking: 思考动作（1:1 Pygame）──
  private checkThinking(now: number): void {
    // Pygame: if not is_text_generating_queue.empty() and self.think_motion_is_over:
    if (!this.isThinking.value || !this.thinkMotionIsOver) return
    // Pygame: if time.time()-last_saved_time_think>interval_think:
    if (now - this.lastThinkTime <= this.thinkInterval * 1000) return

    this.lastThinkTime = now
    this.thinkInterval = THINK_INTERVAL_SUBSEQUENT

    // Pygame: onStartCallback_think_motion_version
    this.thinkMotionIsOver = false

    this._playMotion('text_generating', 3)
  }

  // ── checkIdleRecover: idle_motion 恢复（1:1 Pygame）──
  private checkIdleRecover(now: number): void {
    // Pygame: if self.motion_is_over and not pygame.mixer.music.get_busy():
    if (!this.motionIsOver || this.audioPlaying) return
    // Pygame: if is_text_generating_queue.empty() and time.time()-idle_recover_timer>2.5:
    if (this.isThinking.value) return
    if (now - this.idleRecoverTimer <= IDLE_RECOVER_DELAY_MS) return

    // Pygame: onStartCallback（无 onFinish）
    this.motionIsOver = false
    this._playMotion('IDLE', 1, true)  // noFinishReset，随机 IDLE 动画
  }

  // ── checkTimedIdle: 25s 待机 IDLE（1:1 Pygame）──
  private checkTimedIdle(now: number): void {
    // Pygame: if (time.time()-last_saved_time)>25:
    if (now - this.lastSavedTime <= TIMED_IDLE_INTERVAL_MS) return
    // Pygame: last_saved_time = time.time()  ← 总是重置
    this.lastSavedTime = now

    // Pygame: if self.live2d_this_turn_motion_complete and is_text_generating_queue.empty():
    if (!this.turnMotionComplete || this.isThinking.value) return

    // Pygame: StartRandomMotion("IDLE",1,onStart,onFinish)
    this.motionIsOver = false
    this._playMotion('IDLE', 1)
  }

  // ── checkClick ──
  private checkClick(now: number): void {
    if (!this.mouseClicked) return
    this.mouseClicked = false
    this.thinkMotionIsOver = true
    this.motionIsOver = false
    const idx = Math.floor(Math.random() * this.getMotionSize('IDLE'))
    this.model.motion('IDLE', idx, 1).then(() => {
      this.motionIsOver = true
      this.idleRecoverTimer = performance.now()
      this._queueEyeOpen()
    }).catch(() => { this.motionIsOver = true })
  }

  handleClick(clientX: number, width: number): void {
    if (performance.now() - this.lastClickTime < CLICK_THROTTLE_MS) return
    this.lastClickTime = performance.now()
    this.mouseClicked = true
    try {
      const gazeParam = clientX < width / 2 ? -0.3 : 0.3
      const cm = (this.model.internalModel as any)?.coreModel
      const idx = cm?.getParamIndex?.('PARAM_BODY_ANGLE_X')
      if (idx >= 0) cm?.setParamFloat?.(idx, gazeParam)
    } catch (_) {}
  }

  // ── checkLongAudio: 长音频动作循环（1:1 Pygame）──
  private checkLongAudioLoop(now: number): void {
    if (!this.longAudioActive) return
    if (!this.audioPlaying) { this._resetLongAudio(); return }
    if (!this.motionIsOver) return
    if (!this.longAudioGroup) { this._resetLongAudio(); return }
    if (this.longAudioTriggeredCount >= LONG_AUDIO_MAX_REPEATS) return

    if (this.longAudioNextMotionAt <= 0) {
      this.longAudioNextMotionAt = now + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
      return
    }
    if (now < this.longAudioNextMotionAt) return

    this.motionIsOver = false
    this._playMotion(this.longAudioGroup, 3)
    this.longAudioTriggeredCount++
    this.longAudioNextMotionAt = 0  // 重置，下次 onFinish 后重新计时
  }

  // ── 动作播放 + 回调 ──
  private _playMotion(group: string, priority: number, noFinishReset?: boolean, fixedIdx?: number): void {
    const size = this.getMotionSize(group)
    if (size <= 0) return
    const idx = fixedIdx !== undefined ? fixedIdx : Math.floor(Math.random() * size)
    this.currentMotionId++
    const motionId = this.currentMotionId

    const isIdleLike = noFinishReset === true
    const isThink = group === 'text_generating'

    this.model.motion(group, idx, priority).then(() => {
      if (motionId !== this.currentMotionId) return

      if (isIdleLike) {
        // idle 循环：加最小延迟再触发下一次（对标 idle_motion 的 ~1s 动画时长）
        this.motionIsOver = true
        this.idleRecoverTimer = performance.now() - IDLE_RECOVER_DELAY_MS + 1500
      } else if (isThink) {
        this.thinkMotionIsOver = true
        this.motionIsOver = true
      } else {
        this.motionIsOver = true
        this.idleRecoverTimer = performance.now()
      }

      if (!isIdleLike) {
        this._queueEyeOpen()
      }
    }).catch(() => {
      this.motionIsOver = true
    })
  }

  private _queueEyeOpen(): void {
    this.eyeOpenPending = true
    this.eyeOpenStartTime = performance.now()
    try {
      const cm = (this.model.internalModel as any)?.coreModel
      this.eyeOpenStartL = cm?.getParamFloat?.(cm.getParamIndex?.('PARAM_EYE_L_OPEN') ?? -1) ?? 1
      this.eyeOpenStartR = cm?.getParamFloat?.(cm.getParamIndex?.('PARAM_EYE_R_OPEN') ?? -1) ?? 1
    } catch (_) { this.eyeOpenStartL = 1; this.eyeOpenStartR = 1 }
  }

  private updateEyeOpen(now: number): void {
    if (!this.eyeOpenPending) return
    const elapsed = now - this.eyeOpenStartTime
    if (elapsed >= EYE_OPEN_DURATION_MS) {
      this.eyeOpenPending = false
      try {
        const cm = (this.model.internalModel as any)?.coreModel
        cm?.setParamFloat?.(cm.getParamIndex?.('PARAM_EYE_L_OPEN') ?? -1, 1)
        cm?.setParamFloat?.(cm.getParamIndex?.('PARAM_EYE_R_OPEN') ?? -1, 1)
      } catch (_) {}
      return
    }
    const t = elapsed / EYE_OPEN_DURATION_MS
    const lerp = (s: number, e: number, f: number) => s + (e - s) * f
    try {
      const cm = (this.model.internalModel as any)?.coreModel
      cm?.setParamFloat?.(cm.getParamIndex?.('PARAM_EYE_L_OPEN') ?? -1, lerp(this.eyeOpenStartL, 1, t))
      cm?.setParamFloat?.(cm.getParamIndex?.('PARAM_EYE_R_OPEN') ?? -1, lerp(this.eyeOpenStartR, 1, t))
    } catch (_) {}
  }

  reset(): void {
    this.stopAudio()
    this.motionIsOver = true
    this.thinkMotionIsOver = true
    this.turnMotionComplete = true
    this.idleRecoverTimer = performance.now() - IDLE_RECOVER_DELAY_MS - 1
    this.lastSavedTime = performance.now()
    this.lastThinkTime = 0
    this.thinkInterval = THINK_INTERVAL_FIRST
    this._resetLongAudio()
    this.eyeOpenPending = false
    this.isThinking.value = false
    this.textBubble.value = null
    this.userBubble.value = null
    this.eventQueue.length = 0
  }

  private _resetLongAudio(): void {
    this.longAudioActive = false
    this.longAudioGroup = ''
    this.longAudioNextMotionAt = 0
    this.longAudioTriggeredCount = 0
  }

  private stopAudio(): void {
    if (this.currentAudio) { this.currentAudio.pause(); this.currentAudio = null; this.audioPlaying = false }
    this.mouthOpenValue = 0
    if (this.analyserNode) { try { this.analyserNode.disconnect() } catch (_) {}; this.analyserNode = null }
  }

  // ── 口型同步（保持不变）──
  private _initMouth(): void {
    try {
      const cm = (this.model.internalModel as any)?.coreModel
      if (cm?.getParamIndex) {
        this._mouthParamIndex = cm.getParamIndex('PARAM_MOUTH_OPEN_Y')
        if (this._mouthParamIndex >= 0 && cm.update) {
          const orig = cm.update.bind(cm)
          const self = this
          cm.update = function() { self._preUpdateMouth(); return orig() }
        }
      }
    } catch (e) { console.error('[StateMachine] Mouth init failed:', e) }
  }

  _preUpdateMouth(): void {
    if (!this.audioPlaying || !this.analyserNode || this._mouthParamIndex < 0) {
      if (this.mouthOpenValue > 0.005) this.mouthOpenValue *= 0.85; else this.mouthOpenValue = 0
      this._rawSetMouth(this.mouthOpenValue)
      return
    }
    this.mouthSyncFrameCount++
    try {
      const bl = this.analyserNode.fftSize
      const d = new Float32Array(bl)
      this.analyserNode.getFloatTimeDomainData(d)
      let s = 0; for (let i = 0; i < bl; i++) s += d[i] * d[i]
      this.mouthOpenValue = Math.min(1, Math.sqrt(s / bl) * this.lipSyncN)
    } catch (_) {}
    this._rawSetMouth(this.mouthOpenValue)
  }

  private _rawSetMouth(v: number): void {
    const cm = (this.model.internalModel as any)?.coreModel
    cm?.setParamFloat?.(this._mouthParamIndex, v)
  }

  private _setupLipSync(audioEl: HTMLAudioElement, audioId: number): void {
    try {
      if (!this.audioContext) this.audioContext = new AudioContext()
      if (this.audioContext.state === 'suspended') this.audioContext.resume()
      if (this.analyserNode) { try { this.analyserNode.disconnect() } catch (_) {} }
      const src = this.audioContext.createMediaElementSource(audioEl)
      this.analyserNode = this.audioContext.createAnalyser()
      this.analyserNode.fftSize = 256
      src.connect(this.analyserNode)
      this.analyserNode.connect(this.audioContext.destination)
      this.mouthSyncFrameCount = 0
    } catch (e) { console.warn('[StateMachine] Lip sync setup failed:', e); this.analyserNode = null }
  }

  private _initBlinkBreath(): void {
    try {
      const im = this.model.internalModel as any
      if (im?.setAutoBlinkEnable) im.setAutoBlinkEnable(true)
      if (im?.setAutoBreathEnable) im.setAutoBreathEnable(true)
    } catch (_) {}
  }
}
