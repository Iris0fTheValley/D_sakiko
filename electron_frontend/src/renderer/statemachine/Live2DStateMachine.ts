import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'
import type { RendererCommand, RendererFact } from './constants'

export type RendererFactHandler = (fact: RendererFact) => void

/**
 * Electron-side command executor.
 * Behaviour decisions (idle, thinking, emotion, random index and sequencing)
 * belong to the Python Live2DBehaviorController. This class owns SDK, audio
 * and DOM state and reports lifecycle facts back to that controller.
 */
export class Live2DStateMachine {
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)

  private readonly model: Live2DModel
  private readonly ticker: Ticker
  private readonly modelKey: string
  private readonly rendererId: string
  private readonly modelToken: string
  private readonly report: RendererFactHandler
  private readonly tickerCallback: () => void
  private activeMotionToken = ''
  private currentAudio: HTMLAudioElement | null = null
  private audioContext: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private audioSource: MediaElementAudioSourceNode | null = null
  private mouthIndex = -1
  private mouthValue = 0
  private started = false
  private readyData: Record<string, unknown> = {}

  constructor(model: Live2DModel, ticker: Ticker, modelKey: string, report: RendererFactHandler, rendererId = modelKey, modelToken = '') {
    this.model = model
    this.ticker = ticker
    this.modelKey = modelKey
    this.rendererId = rendererId
    this.modelToken = modelToken
    this.report = report
    this.tickerCallback = () => this.updateLipSync()
  }

  start(): void {
    if (this.started) return
    this.started = true
    this.ticker.add(this.tickerCallback, undefined, 30 as any)
    this.initParameters()
  }

  destroy(): void {
    if (!this.started) return
    this.started = false
    this.ticker.remove(this.tickerCallback, undefined)
    this.stopAudio()
    this.stopMotion()
    try { this.audioContext?.close() } catch (_) { /* best effort */ }
    this.audioContext = null
  }

  reset(): void {
    this.stopAudio()
    this.stopMotion()
    this.textBubble.value = null
    this.userBubble.value = null
    this.isThinking.value = false
  }

  reportReady(): void {
    if (!this.started) return
    this.report({ type: 'renderer_ready', data: { ...this.readyData } })
  }

  pushCommand(command: RendererCommand): void {
    const data = command.data || {}
    switch (command.type) {
      case 'motion': this.executeMotion(command); break
      case 'play_motion': this.executeMotion({ ...command, type: 'motion', data: { ...data, motion_token: data.token } }); break
      case 'audio': this.executeAudio(command); break
      case 'play_audio': this.executeAudio({ ...command, type: 'audio', data: { ...data, audio_token: data.token } }); break
      case 'stop_audio': this.stopAudio(); break
      case 'stop_motion': this.stopMotion(); break
      case 'text': this.textBubble.value = String(data.text || ''); break
      case 'segment_started': this.textBubble.value = String(data.text || ''); break
      case 'user_text': this.userBubble.value = String(data.text || ''); break
      case 'thinking': this.isThinking.value = data.active === true; break
      case 'thinking_changed': this.isThinking.value = data.active === true; break
      case 'reset': this.reset(); break
      case 'reset_renderer': this.reset(); break
      case 'bye': this.executeBye(command); break
      case 'close_renderer': try { (window as any).electronAPI?.closeWindow?.() } catch (_) { window.close() }; break
      default: break
    }
  }

  /** Compatibility adapter for old bridge messages; it performs no scheduling. */
  pushEvent(event: { type: string; data?: Record<string, unknown> }): void {
    const data = event.data || {}
    if (event.type === 'user_text') this.userBubble.value = String(data.text || '')
    if (event.type === 'text') this.textBubble.value = String(data.text || '')
  }

  handleClick(clientX: number, width: number): void {
    try {
      const gaze = clientX < width / 2 ? -0.3 : 0.3
      const core = (this.model.internalModel as any)?.coreModel
      const index = core?.getParamIndex?.('PARAM_BODY_ANGLE_X')
      if (index >= 0) core.setParamFloat(index, gaze)
    } catch (_) { /* model may not expose this parameter */ }
    this.report({ type: 'renderer_intent', data: { intent: 'click' } })
  }

  private executeMotion(command: RendererCommand): void {
    const data = command.data
    const group = String(data.group || '')
    const index = Number(data.index)
    const priority = Number(data.priority ?? 1)
    const token = String(data.motion_token || command.event_id || '')
    if (!group || !Number.isInteger(index) || index < 0) {
      this.report({ type: 'command_failed', event_id: command.event_id, data: { reason: 'invalid_motion' } })
      return
    }
    this.stopMotion()
    this.activeMotionToken = token
    const manager = (this.model.internalModel as any)?.motionManager
    const onFinish = () => {
      if (this.activeMotionToken !== token) return
      this.activeMotionToken = ''
      this.report({ type: 'motion_finished', event_id: command.event_id, data: { token, turn_id: data.turn_id || '', segment_id: data.segment_id || '', group, index, renderer_id: this.rendererId } })
    }
    manager?.once?.('motionFinish', onFinish)
    this.model.motion(group, index, priority as any).then((started) => {
      if (this.activeMotionToken !== token) return
      if (!started) {
        this.activeMotionToken = ''
        this.report({ type: 'command_failed', event_id: command.event_id, data: { reason: 'motion_not_started', group, index } })
        return
      }
        this.report({ type: 'motion_started', event_id: command.event_id, data: { token, turn_id: data.turn_id || '', segment_id: data.segment_id || '', group, index, renderer_id: this.rendererId } })
    }).catch((error) => {
      if (this.activeMotionToken !== token) return
      this.activeMotionToken = ''
      this.report({ type: 'command_failed', event_id: command.event_id, data: { reason: String(error) } })
    })
  }

  private executeAudio(command: RendererCommand): void {
    const data = command.data
    const url = String(data.url || '')
    const segmentId = String(data.segment_id || command.event_id || '')
    if (!url) {
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: 'no_audio', renderer_id: this.rendererId } })
      return
    }
    this.stopAudio()
    const audio = new Audio(url)
    audio.crossOrigin = 'anonymous'
    this.currentAudio = audio
    audio.addEventListener('ended', () => {
      if (this.currentAudio !== audio) return
      this.mouthValue = 0
      this.currentAudio = null
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, renderer_id: this.rendererId } })
    })
    audio.addEventListener('error', () => {
      if (this.currentAudio !== audio) return
      this.currentAudio = null
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: 'audio_error', renderer_id: this.rendererId } })
    })
    this.setupLipSync(audio)
    audio.play().then(() => {
      this.report({ type: 'audio_started', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, renderer_id: this.rendererId } })
    }).catch((error) => {
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: String(error), renderer_id: this.rendererId } })
    })
  }

  private executeBye(command: RendererCommand): void {
    this.stopAudio()
    this.executeMotion({ ...command, type: 'motion', data: command.data })
  }

  private stopMotion(): void {
    this.activeMotionToken = ''
    try { (this.model.internalModel as any)?.motionManager?.stopAllMotions?.() } catch (_) { /* best effort */ }
  }

  private stopAudio(): void {
    if (this.currentAudio) {
      this.currentAudio.pause()
      this.currentAudio.src = ''
      this.currentAudio = null
    }
    try { this.audioSource?.disconnect() } catch (_) { /* best effort */ }
    try { this.analyser?.disconnect() } catch (_) { /* best effort */ }
    this.audioSource = null
    this.analyser = null
    this.mouthValue = 0
  }

  private initParameters(): void {
    try {
      const core = (this.model.internalModel as any)?.coreModel
      this.mouthIndex = core?.getParamIndex?.('PARAM_MOUTH_OPEN_Y') ?? -1
      const internal = this.model.internalModel as any
      internal?.setAutoBlinkEnable?.(true)
      internal?.setAutoBreathEnable?.(true)
      const definitions = internal?.motionManager?.definitions || {}
      const motionGroups: Record<string, number> = {}
      for (const [group, entries] of Object.entries(definitions)) {
        if (Array.isArray(entries)) motionGroups[group] = entries.length
      }
      this.readyData = {
        model_key: this.modelKey,
        renderer_id: this.rendererId,
        model_token: this.modelToken,
        capabilities: { motion: true, audio: true, lipsync: true },
        motion_groups: motionGroups,
      }
    } catch (_) { /* optional SDK features */ }
  }

  private setupLipSync(audio: HTMLAudioElement): void {
    try {
      if (!this.audioContext) this.audioContext = new AudioContext()
      if (this.audioContext.state === 'suspended') void this.audioContext.resume()
      this.analyser = this.audioContext.createAnalyser()
      this.analyser.fftSize = 256
      this.audioSource = this.audioContext.createMediaElementSource(audio)
      this.audioSource.connect(this.analyser)
      this.analyser.connect(this.audioContext.destination)
    } catch (_) {
      this.analyser = null
      this.audioSource = null
    }
  }

  private updateLipSync(): void {
    const core = (this.model.internalModel as any)?.coreModel
    if (this.mouthIndex < 0 || !core?.setParamFloat) return
    if (this.currentAudio && this.analyser) {
      const samples = new Float32Array(this.analyser.fftSize)
      this.analyser.getFloatTimeDomainData(samples)
      let power = 0
      for (const sample of samples) power += sample * sample
      this.mouthValue = Math.min(1, Math.sqrt(power / samples.length) * 2.8)
    } else {
      this.mouthValue *= 0.82
    }
    core.setParamFloat(this.mouthIndex, this.mouthValue)
  }
}
