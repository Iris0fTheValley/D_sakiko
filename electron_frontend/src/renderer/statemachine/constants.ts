/** 情感标签 → Live2D 运动组名 */
export const EMOTION_MAP: Record<string, string> = {
  'LABEL_0': 'happiness',
  'LABEL_1': 'sadness',
  'LABEL_2': 'anger',
  'LABEL_3': 'disgust',
  'LABEL_4': 'like',
  'LABEL_5': 'surprise',
  'LABEL_6': 'fear',
}

/** 每个运动组包含的动作数量 */
export const MOTION_GROUP_SIZES: Record<string, number> = {
  happiness: 6,
  sadness: 4,
  anger: 7,
  disgust: 2,
  like: 4,
  surprise: 4,
  fear: 2,
  IDLE: 7,
  text_generating: 4,
  bye: 1,
  change_character: 3,
  idle_motion: 1,
  talking_motion: 1,
}

/** 按角色适配的动作组数量 */
export const MODEL_SPECIFIC_SIZES: Record<string, Record<string, number>> = {
  sakiko: {
    happiness: 6, sadness: 4, anger: 7, disgust: 2, like: 4, surprise: 4, fear: 2,
    IDLE: 7, text_generating: 4, bye: 1, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
  anon: {
    happiness: 6, sadness: 6, anger: 6, disgust: 6, like: 6, surprise: 6, fear: 6,
    IDLE: 9, text_generating: 3, bye: 2, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
  soyo: {
    happiness: 6, sadness: 6, anger: 6, disgust: 6, like: 6, surprise: 6, fear: 6,
    IDLE: 9, text_generating: 3, bye: 2, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
}

/** 长音频运动循环参数 */
export const LONG_AUDIO_THRESHOLD_SECONDS = 6.0
export const LONG_AUDIO_REPEAT_DELAY_SECONDS = 1.5
export const LONG_AUDIO_MAX_REPEATS = 2

/** 空闲恢复延迟（毫秒） */
export const IDLE_RECOVER_DELAY_MS = 2500

/** 定时待机间隔（毫秒） */
export const TIMED_IDLE_INTERVAL_MS = 25000

/** 思考动作间隔（首次 / 后续），单位秒 */
export const THINK_INTERVAL_FIRST = 1
export const THINK_INTERVAL_SUBSEQUENT = 15

/** 睁眼过渡时长（毫秒） */
export const EYE_OPEN_DURATION_MS = 100

/** bye 动画超时（毫秒） */
export const BYE_TIMEOUT_MS = 5000

/** 点击节流（毫秒） */
export const CLICK_THROTTLE_MS = 200

/** WS 事件类型 */
export interface StateMachineEvent {
  type: 'emotion' | 'text_generating' | 'cancel_turn' | 'user_text' | 'bye' | 'switch_live2d'
  data: any
}
