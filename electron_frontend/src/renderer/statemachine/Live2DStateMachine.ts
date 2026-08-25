import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'

export type RendererFact = { type: string; data: Record<string, unknown> }
export type RendererCommand = { type: string; data?: Record<string, unknown> }
type Reporter = (fact: RendererFact) => void

/** SDK/audio executor only.  Shared Python behavior owns every decision. */
export class Live2DStateMachine {
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)
  private reporter: Reporter
  private activeMotionToken = ''
  private currentAudio: HTMLAudioElement | null = null

  constructor(
    private readonly model: Live2DModel,
    private readonly ticker: Ticker,
    private readonly modelKey: string,
    private readonly motionFilesByGroup: Record<string, string[]> = {},
    private readonly expressionIds: string[] = [],
    reporter: Reporter = () => {},
  ) {
    this.reporter = reporter
  }
  start(): void { this.report({ type: 'renderer_ready', data: { renderer_id: this.modelKey, motion_groups: this.motionGroups(), motion_files_by_group: this.motionFilesByGroup, expression_ids: this.expressionIds, capabilities: { motion: true, audio: true, lipsync: true } } }) }
  destroy(): void { this.stopAudio(); this.stopMotion() }
  reset(): void { this.stopAudio(); this.stopMotion(); this.textBubble.value = null; this.userBubble.value = null; this.isThinking.value = false }
  setReporter(reporter: Reporter): void { this.reporter = reporter; this.start() }
  pushEvent(event: RendererCommand): void { this.pushCommand(event) }
  pushCommand(command: RendererCommand): void {
    const data = command.data || {}
    if (command.type === 'play_motion') return this.playMotion(data)
    if (command.type === 'play_audio') return this.playAudio(data)
    if (command.type === 'stop_motion') return this.stopMotion()
    if (command.type === 'stop_audio') return this.stopAudio()
    if (command.type === 'reset_renderer') return this.reset()
    if (command.type === 'thinking_changed') { this.isThinking.value = data.active === true; return }
    if (command.type === 'text' || command.type === 'segment_started') { this.textBubble.value = String(data.text || ''); return }
    if (command.type === 'user_text') this.userBubble.value = String(data.text || '')
  }
  handleClick(): void { this.report({ type: 'renderer_intent', data: { intent: 'click', renderer_id: this.modelKey } }) }
  private playMotion(data: Record<string, unknown>): void {
    const group = String(data.group || ''), index = Number(data.index), token = String(data.token || '')
    if (!group || !Number.isInteger(index) || index < 0 || !token) return this.failed(token, 'motion_start')
    this.stopMotion(); this.activeMotionToken = token
    const expression = typeof data.expression_id === 'string' ? data.expression_id : ''
    try { if (expression) this.model.expression(expression) } catch (_) { /* optional expression */ }
    const manager = (this.model.internalModel as any)?.motionManager
    manager?.once?.('motionFinish', () => { if (this.activeMotionToken === token) { this.activeMotionToken = ''; this.report({ type: 'motion_finished', data: { token, renderer_id: this.modelKey } }) } })
    this.model.motion(group, index, Number(data.priority ?? 3) as any).then((started) => {
      if (this.activeMotionToken !== token) return
      if (!started) return this.failed(token, 'motion_start')
      this.report({ type: 'motion_started', data: { token, renderer_id: this.modelKey } })
    }).catch(() => this.failed(token, 'motion_start'))
  }
  private playAudio(data: Record<string, unknown>): void {
    const path = String(data.path || data.url || ''), token = String(data.token || '')
    if (!path || !token) return this.failed(token, 'audio_start')
    this.stopAudio(); const audio = new Audio(path); this.currentAudio = audio
    audio.onended = () => { if (this.currentAudio === audio) { this.currentAudio = null; this.report({ type: 'audio_ended', data: { token, renderer_id: this.modelKey } }) } }
    audio.onerror = () => this.failed(token, 'audio_start')
    audio.play().then(() => this.report({ type: 'audio_started', data: { token, renderer_id: this.modelKey } })).catch(() => this.failed(token, 'audio_start'))
  }
  private stopMotion(): void { this.activeMotionToken = ''; try { (this.model.internalModel as any)?.motionManager?.stopAllMotions?.() } catch (_) {} }
  private stopAudio(): void { if (this.currentAudio) { this.currentAudio.pause(); this.currentAudio.src = ''; this.currentAudio = null } }
  private failed(token: string, phase: string): void { this.activeMotionToken = ''; this.report({ type: 'command_failed', data: { token, phase, renderer_id: this.modelKey } }) }
  private report(fact: RendererFact): void { this.reporter(fact) }
  private motionGroups(): Record<string, number> { const defs = (this.model.internalModel as any)?.motionManager?.definitions || {}; return Object.fromEntries(Object.entries(defs).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, (v as unknown[]).length])) }
}
