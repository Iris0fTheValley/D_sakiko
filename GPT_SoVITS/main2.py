from __future__ import annotations

import os,sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
# Keep the GPT-SoVITS package directory ahead of the repository root.  Both
# directories contain a ``tools`` package; the former is the runtime package
# used by TTS_infer_pack, while the latter contains launcher utilities.
for _path in (script_dir, project_root):
    if _path in sys.path:
        sys.path.remove(_path)
sys.path.insert(0, project_root)
sys.path.insert(0, script_dir)
from ui_main.threads.update_config_thread import UpdateConfigThread

from queue import Queue, Empty
import threading
import multiprocessing
import time
import json
import re

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import character
import dp_local2
import audio_generator
import qtUI
from chat.chat import get_chat_manager

from emotion_enum import EmotionEnum
from log import setup_logging, get_logger, get_log_queue, setup_worker_logging, shutdown_logging

import faulthandler

faulthandler.enable(file=open("faulthandler_log.txt", "a"), all_threads=True)

# 日志记录
main_logger = get_logger(__name__)

NO_AUDIO_TEXT_EVENT_PREFIX = "__NO_AUDIO_TEXT__:"


def resolve_renderer_mode(configured_mode: str = "electron") -> str:
    """Resolve the renderer once, allowing an explicit environment override."""
    requested = os.environ.get("DSAKIKO_RENDERER", "").strip().lower()
    if requested in {"electron", "pygame"}:
        return requested
    configured = str(configured_mode or "electron").strip().lower()
    return configured if configured in {"electron", "pygame"} else "electron"


ELECTRON_RENDERER = resolve_renderer_mode() == "electron"

# Electron mode owns a formal bridge/controller pair.  These globals are
# initialized in __main__ and intentionally stay absent in legacy mode.
electron_controller = None
electron_bridge = None
electron_renderer_messages = None
electron_ui_commands = None
electron_change_char_queue = None
electron_char_is_converted_queue = None
electron_renderer_ready = threading.Event()
electron_segment_events: dict[str, threading.Event] = {}
electron_segment_events_lock = threading.Lock()


def electron_emit(command: dict[str, object]) -> None:
    """Queue one controller command for Bridge; never choose or rewrite motion."""
    if electron_bridge is None:
        return
    command_type = str(command.get("type") or "")
    data = command.get("data") if isinstance(command.get("data"), dict) else {}
    if command_type == "segment_completed":
        segment_id = str(data.get("segment_id") or "")
        with electron_segment_events_lock:
            waiter = electron_segment_events.get(segment_id)
        if waiter is not None:
            waiter.set()
    electron_bridge.event_queue.put(command)


def electron_audio_url(audio_path: str) -> str:
    """Convert a generated-audio path into the bridge's constrained URL space."""
    absolute = os.path.abspath(os.path.join(script_dir, audio_path))
    relative = os.path.relpath(absolute, project_root).replace(os.sep, "/")
    return "http://127.0.0.1:9877/audio/" + relative


def electron_model_url(model_json: str) -> tuple[str, str]:
    """Resolve one project model path into its Bridge URL and absolute path."""
    model_path = os.path.abspath(os.path.join(script_dir, str(model_json)))
    model_relative = os.path.relpath(model_path, project_root).replace(os.sep, "/")
    if model_relative.startswith("live2d_related/"):
        model_relative = model_relative[len("live2d_related/"):]
    return "http://127.0.0.1:9877/model/" + model_relative, model_path


def resolve_electron_sakiko_model(sakiko_state: bool) -> dict[str, object] | None:
    """Build the authoritative renderer descriptor for light/dark Sakiko."""
    is_dark = bool(sakiko_state)
    model_json = (
        "../live2d_related/sakiko/live2D_model_costume/3.model.json"
        if is_dark
        else "../live2d_related/sakiko/live2D_model/3.model.json"
    )
    model_url, model_path = electron_model_url(model_json)
    if not os.path.isfile(model_path):
        main_logger.error("祥子 Live2D 模型不存在，取消黑白切换：%s", model_path)
        return None
    return {
        "model_url": model_url,
        "character_folder": "sakiko",
        "character_name": "祥子",
        "variant": "dark" if is_dark else "light",
        "initial_expression": "serious" if is_dark else "idle",
        "transition_groups": (
            ["change_character", "change_character_maskoff"]
            if is_dark
            else ["change_character"]
        ),
        "transition_priority": 2,
    }


def electron_renderer_loop() -> None:
    """Feed renderer facts into the shared controller and forward UI requests."""
    global electron_controller
    next_tick = time.monotonic()
    while electron_controller is not None:
        now = time.monotonic()
        if now >= next_tick:
            electron_controller.tick()
            next_tick = now + 0.05
        try:
            ui_command = electron_change_char_queue.get_nowait()
        except Exception:
            ui_command = None
        if isinstance(ui_command, dict):
            command_type = str(ui_command.get("type") or "")
            if command_type == "cancel_turn":
                electron_controller.reset()
            elif command_type == "switch_live2d":
                model_json = str(ui_command.get("model_json") or "")
                model_url, model_path = electron_model_url(model_json)
                if not os.path.isfile(model_path):
                    model_url = str(ui_command.get("model_url") or model_url)
                electron_controller.switch_model({
                    "model_url": model_url,
                    "character_folder": str(ui_command.get("character_folder_name") or ""),
                    "character_name": str(ui_command.get("character_name") or ""),
                })
        # The old Pygame renderer consumed this queue directly.  Electron is
        # now the only renderer, so the shared controller must consume the
        # same business event and choose the model transition once here.
        try:
            sakiko_state = electron_char_is_converted_queue.get_nowait()
        except Exception:
            sakiko_state = None
        if sakiko_state is True or sakiko_state is False:
            model = resolve_electron_sakiko_model(sakiko_state)
            if model is not None:
                electron_controller.switch_model(model)
        elif sakiko_state == "maskoff":
            main_logger.warning("Electron 模式暂未迁移祥子面具动作；已忽略本次面具切换。")
        try:
            message = electron_renderer_messages.get(timeout=0.05)
        except Exception:
            continue
        if not isinstance(message, dict):
            continue
        if str(message.get("type") or "") == "renderer_hello":
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            electron_controller.handle_renderer_event({
                "type": "renderer_ready",
                "event_id": message.get("event_id"),
                "session_id": electron_controller.snapshot().get("session_id"),
                "source": message.get("source", "electron-renderer"),
                "data": {
                    **data,
                    "renderer_id": data.get("renderer_id") or "electron-main",
                    "motion_groups": data.get("motion_groups", {}),
                },
            })
            continue
        # Renderer sessions are transport sessions; controller session is the
        # authoritative behavior epoch and is attached at the process boundary.
        message["session_id"] = electron_controller.snapshot().get("session_id", "")
        if str(message.get("type") or "") == "renderer_intent":
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            if data.get("intent") == "click":
                electron_controller.click_motion(event_id=str(message.get("event_id") or ""))
            elif data.get("intent") == "open_python_settings":
                # This thread is owned by Bridge, not Qt.  Keep the request on a
                # dedicated in-process queue so ChatGUI can consume it from the
                # QApplication thread without touching legacy Pygame queues.
                if electron_ui_commands is not None:
                    electron_ui_commands.put({"type": "open_python_settings"})
            continue
        electron_controller.handle_renderer_event(message)
        if str(message.get("type") or "") == "renderer_ready":
            electron_renderer_ready.set()


def get_character_by_name(character_name: str) -> character.CharacterAttributes | None:
    """按角色名查找角色对象。"""
    for one_character in characters:
        if one_character.character_name == character_name:
            return one_character
    return None


def build_assistant_segment_event(
    payload: dict[str, object],
    segment: dict[str, object],
    audio_path: str,
) -> dict[str, object]:
    """构造发给 qtUI 的 assistant 片段事件。"""
    raw_message_index = segment.get("message_index")
    message_index = raw_message_index if isinstance(raw_message_index, int) else -1
    return {
        "type": "assistant_segment_ready",
        "chat_id": str(payload.get("chat_id") or ""),
        "turn_id": str(payload.get("turn_id") or ""),
        "message_index": message_index,
        "character_name": str(payload.get("character_name") or ""),
        "text": str(segment.get("text") or ""),
        "translation": str(segment.get("translation") or ""),
        "emotion": str(segment.get("emotion") or "LABEL_0"),
        "audio_path": audio_path,
    }


def build_assistant_turn_phase_event(payload: dict[str, object], phase: str) -> dict[str, object]:
    """构造发给 qtUI 的对话轮次阶段事件。"""
    segments = payload.get("segments")
    message_indices = []
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict) and isinstance(segment.get("message_index"), int):
                message_indices.append(segment["message_index"])
    return {
        "type": "assistant_turn_phase",
        "chat_id": str(payload.get("chat_id") or ""),
        "turn_id": str(payload.get("turn_id") or ""),
        "phase": phase,
        "message_indices": message_indices,
    }


def build_assistant_turn_complete_event(payload: dict[str, object], status: str = "ok") -> dict[str, object]:
    """构造发给 qtUI 的对话轮次完成事件。"""
    return {
        "type": "assistant_turn_complete",
        "chat_id": str(payload.get("chat_id") or ""),
        "turn_id": str(payload.get("turn_id") or ""),
        "status": status,
    }


def is_payload_turn_cancelled(payload: dict[str, object]) -> bool:
    """判断当前 payload 对应轮次是否已被前端取消。"""
    chat_id = str(payload.get("chat_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    if not chat_id or not turn_id or not hasattr(dp_chat, "is_turn_cancelled"):
        return False
    return bool(dp_chat.is_turn_cancelled(chat_id, turn_id))


def mark_segments_no_audio(payload: dict[str, object], segments_raw: list[object], start_index: int = 0) -> None:
    """将尚未生成语音的段落标记为无语音。"""
    chat_id = str(payload.get("chat_id") or "")
    chat = dp_chat.chat_manager.get_chat_by_id(chat_id)
    if chat is None:
        return
    for segment_raw in segments_raw[start_index:]:
        if not isinstance(segment_raw, dict):
            continue
        raw_message_index = segment_raw.get("message_index")
        if not isinstance(raw_message_index, int):
            continue
        if 0 <= raw_message_index < len(chat.message_list):
            msg = chat.message_list[raw_message_index]
            if not msg.audio_path:
                msg.audio_path = "NO_AUDIO"


def update_segment_audio_path(payload: dict[str, object], segment_raw: dict[str, object], audio_path: str) -> None:
    """直接回填某个段落的音频路径，便于取消后保留已生成语音。"""
    chat_id = str(payload.get("chat_id") or "")
    chat = dp_chat.chat_manager.get_chat_by_id(chat_id)
    if chat is None:
        return
    raw_message_index = segment_raw.get("message_index")
    if not isinstance(raw_message_index, int):
        return
    if 0 <= raw_message_index < len(chat.message_list):
        msg = chat.message_list[raw_message_index]
        msg.audio_path = audio_path
        msg.translation = str(segment_raw.get("translation") or msg.translation)


def _audio_duration_seconds(audio_path: str) -> float:
    try:
        import wave
        with wave.open(audio_path, "rb") as wav_file:
            rate = wav_file.getframerate()
            return wav_file.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


def handle_electron_model_response_payload(payload: dict[str, object]) -> None:
    """Generate one ordered turn for the shared controller in Electron mode."""
    if not electron_renderer_ready.wait(timeout=60):
        main_logger.error("Electron renderer 在 60 秒内未完成握手，跳过 Live2D 片段")
        is_audio_play_complete.put("yes")
        return
    character_name = str(payload.get("character_name") or "")
    current_character = get_character_by_name(character_name)
    segments_raw = payload.get("segments")
    turn_id = str(payload.get("turn_id") or "")
    if current_character is None or not isinstance(segments_raw, list):
        is_audio_play_complete.put("yes")
        return

    dp2qt_queue.put(build_assistant_turn_phase_event(payload, "tts"))
    turn_status = "ok"
    for index, segment_raw in enumerate(segments_raw):
        if not isinstance(segment_raw, dict) or is_payload_turn_cancelled(payload):
            turn_status = "cancelled"
            mark_segments_no_audio(payload, segments_raw, index)
            break
        text = str(segment_raw.get("text") or "")
        emotion_label = str(segment_raw.get("emotion") or "LABEL_0")
        force_no_audio = bool(segment_raw.get("force_no_audio", False)) or not dp_chat.if_generate_audio
        audio_path = ""
        if not force_no_audio:
            try:
                audio_path = audio_gen.generate_audio_for_character_sync(
                    clean_text_for_audio(text),
                    current_character,
                    bool(payload.get("sakiko_state", dp_chat.sakiko_state)),
                    str(payload.get("audio_language_choice") or dp_chat.audio_language_choice),
                    segment_index=index + 1,
                    segment_total=len(segments_raw),
                    emotion=emotion_label,
                )
            except Exception:
                main_logger.exception("Electron 模式语音合成失败，使用无语音片段")
                audio_path = ""

        if index == 0:
            dp_chat._clear_text_generating_flag_if_needed(is_text_generating_queue)
            electron_controller.stop_thinking(turn_id)

        segment_id = str(segment_raw.get("message_index", index))
        waiter = threading.Event()
        with electron_segment_events_lock:
            electron_segment_events[segment_id] = waiter
        try:
            electron_controller.start_emotion_segment(
                turn_id=turn_id,
                segment_id=segment_id,
                emotion=emotion_label,
                audio_url=electron_audio_url(audio_path) if audio_path else "",
                audio_duration=_audio_duration_seconds(audio_path) if audio_path else 0.0,
                text=text,
            )
            while not waiter.wait(0.25):
                if is_payload_turn_cancelled(payload):
                    turn_status = "cancelled"
                    electron_controller.reset()
                    mark_segments_no_audio(payload, segments_raw, index)
                    break
            if turn_status == "cancelled":
                break
        finally:
            with electron_segment_events_lock:
                electron_segment_events.pop(segment_id, None)
        translation = str(segment_raw.get("translation") or "")
        dp2qt_queue.put(build_assistant_segment_event(payload, segment_raw, audio_path or "NO_AUDIO"))

    is_audio_play_complete.put("yes")
    if turn_status in {"cancelled", "error"}:
        clear_text_generating_flag_if_needed()
    if bool(payload.get("turn_complete", True)):
        dp2qt_queue.put(build_assistant_turn_complete_event(payload, turn_status))


def clear_text_generating_flag_if_needed() -> None:
    """取消或异常收尾时，确保 Live2D 不会一直停留在思考状态。"""
    try:
        is_text_generating_queue.get_nowait()
    except Empty:
        return
    except Exception:
        return


def handle_model_response_payload(payload: dict[str, object]) -> None:
    """处理结构化模型回复事件，逐段合成语音并通知 UI。"""
    if ELECTRON_RENDERER and electron_controller is not None:
        handle_electron_model_response_payload(payload)
        return
    character_name = str(payload.get("character_name") or "")
    current_character = get_character_by_name(character_name)
    segments_raw = payload.get("segments")
    # 当前的模型回复是否是最终回复（完成整段对话）
    turn_complete = bool(payload.get("turn_complete", True))
    if current_character is None or not isinstance(segments_raw, list):
        main_logger.warning("收到无效模型回复 payload：%s", payload)
        is_audio_play_complete.put("yes")
        if turn_complete:
            dp2qt_queue.put(build_assistant_turn_complete_event(payload, "error"))
        return

    audio_language_choice = str(payload.get("audio_language_choice") or dp_chat.audio_language_choice)
    sakiko_state = bool(payload.get("sakiko_state", dp_chat.sakiko_state))
    if_generate_audio = bool(payload.get("if_generate_audio", dp_chat.if_generate_audio))
    turn_status = "ok"

    try:
        # 转阶段：要求 qtUI 更新当前阶段为语音生成
        dp2qt_queue.put(build_assistant_turn_phase_event(payload, "tts"))
        for index, segment_raw in enumerate(segments_raw):
            if not isinstance(segment_raw, dict):
                continue
            # 如果用户要求取消，则终止这个语音生成流程
            if is_payload_turn_cancelled(payload):
                turn_status = "cancelled"
                mark_segments_no_audio(payload, segments_raw, index)
                break
            text = str(segment_raw.get("text") or "")
            translation = str(segment_raw.get("translation") or "")
            emotion_label = str(segment_raw.get("emotion") or "LABEL_0")
            force_no_audio = bool(segment_raw.get("force_no_audio", False)) or not if_generate_audio

            if force_no_audio:
                if index == 0:
                    is_text_generating_queue.get()
                audio_file_path_queue.put("../reference_audio/silent_audio/silence.wav")
                dp2qt_queue.put(build_assistant_segment_event(payload, segment_raw, "NO_AUDIO"))
                emotion_queue.put(emotion_label)
                continue

            #QT_message_queue.put(f"正在合成语音...{index + 1}/{len(segments_raw)}")
            cleaned_text = clean_text_for_audio(text)
            audio_generate_count = 1
            generated_audio_path = "../reference_audio/silent_audio/silence.wav"

            while audio_generate_count <= 2:
                try:
                    generated_audio_path = audio_gen.generate_audio_for_character_sync(
                        cleaned_text,
                        current_character,
                        sakiko_state,
                        audio_language_choice,
                        segment_index=index + 1,
                        segment_total=len(segments_raw),
                        emotion=emotion_label,
                    )
                    break
                except Exception:
                    QT_message_queue.put("语音合成出错，重试中")
                    audio_generate_count += 1
                    main_logger.exception("语音合成错误")
                    time.sleep(1)

            if index == 0:
                is_text_generating_queue.get()

            if is_payload_turn_cancelled(payload):
                turn_status = "cancelled"
                mark_segments_no_audio(payload, segments_raw, index)
                break

            if audio_generate_count > 2:
                generated_audio_path = "../reference_audio/silent_audio/silence.wav"

            # 在播放期间如果用户要求取消，则标记剩余段落无语音并终止流程
            while not motion_complete_value.value:
                if is_payload_turn_cancelled(payload):
                    turn_status = "cancelled"
                    mark_segments_no_audio(payload, segments_raw, index)
                    break
                time.sleep(0.2)
            if turn_status == "cancelled":
                break

            audio_gen.audio_file_path = generated_audio_path
            update_segment_audio_path(payload, segment_raw, generated_audio_path)
            audio_file_path_queue.put(generated_audio_path)

            while not motion_complete_value.value:
                if is_payload_turn_cancelled(payload):
                    turn_status = "cancelled"
                    mark_segments_no_audio(payload, segments_raw, index + 1)
                    break
                time.sleep(0.5)
            if turn_status == "cancelled":
                break

            dp2qt_queue.put(build_assistant_segment_event(payload, segment_raw, generated_audio_path))
            emotion_queue.put(emotion_label)
    except Exception:
        turn_status = "error"
        QT_message_queue.put("语音合成流程出错。")
        main_logger.exception("处理模型回复 payload 时出错")
    finally:
        if turn_status in {"cancelled", "error"}:
            clear_text_generating_flag_if_needed()
        is_audio_play_complete.put("yes")
        if turn_complete:
            dp2qt_queue.put(build_assistant_turn_complete_event(payload, turn_status))
        if turn_status == "cancelled" and hasattr(dp_chat, "clear_cancelled_turn"):
            dp_chat.clear_cancelled_turn(str(payload.get("chat_id") or ""), str(payload.get("turn_id") or ""))

def merge_short_sentences(sentences, min_length=25):
    merged = []
    i = 0
    n = len(sentences)

    while i < n:
        current = sentences[i]
        # 如果当前句子已经足够长，直接加入
        if len(current) >= min_length:
            merged.append(current)
            i += 1
        else:
            # 否则，尝试合并后续句子，直到足够长或没有更多句子
            j = i + 1
            while j < n and len(current) < min_length:
                current += sentences[j]
                j += 1
            merged.append(current)
            i = j  # 跳过已合并的句子

    return merged


def clean_text_for_audio(text):
    """清洗文本使其适合送入语音合成模块：移除括号内容、中括号、书名号等"""
    cleaned = re.sub(r"（.*?）", "", text)
    cleaned = re.sub(r"\(.*?\)", "", cleaned)
    cleaned = re.sub(r"\[.*?]", "", cleaned)
    cleaned = cleaned.replace('「', '')
    cleaned = cleaned.replace('」', '')
    cleaned = cleaned.strip()
    if not cleaned or bool(re.fullmatch(r'[\W_]+', cleaned)):
        cleaned = '不能送去合成'
    return cleaned


def parse_llm_response(response_text):
    """
    解析 LLM 回复。优先尝试 JSON 格式（数组或单对象），失败则回退到旧版正则。
    返回: list of (text, translation, emotion_label)
      - text: 原始文本
      - translation: 翻译（可能为空）
      - emotion_label: 情感标签（如 'LABEL_0'），若无法确定则返回 None
    """
    # 尝试解析 JSON 格式
    try:
        json_text = response_text.strip()
        if json_text.startswith('```'):
            json_text = re.sub(r'^```(?:json)?\s*', '', json_text)
            json_text = re.sub(r'\s*```$', '', json_text)

        data = json.loads(json_text)

        # JSON 数组格式（多段回复）
        if isinstance(data, list):
            segments = []
            for item in data:
                text = item.get('text', '')
                translation = item.get('translation', '')
                emotion_str = item.get('emotion', 'happiness')
                emotion_label = EmotionEnum.from_string(emotion_str).as_label()
                if text:
                    segments.append((text, translation, emotion_label))
            if segments:
                return segments

        # JSON 单对象格式（单段回复）
        if isinstance(data, dict):
            text = data.get('text', '')
            translation = data.get('translation', '')
            emotion_str = data.get('emotion', 'happiness')
            emotion_label = EmotionEnum.from_string(emotion_str).as_label()
            if text:
                return [(text, translation, emotion_label)]

    except (json.JSONDecodeError, KeyError, AttributeError):
        pass

    # 回退到旧版 [翻译]...[翻译结束] 格式
    pattern = r'(.*?)(?:\[翻译\]|\[翻訳\])(.+?)(?:\[翻译结束\]|\[翻訳終了\])'
    match_result = re.findall(pattern, response_text, flags=re.DOTALL)
    if match_result:
        return [(orig.strip(), trans.strip(), None) for orig, trans in match_result if trans.strip()]

    # 纯文本回复（中文模式），按句号分割
    text = response_text.strip() + '。'
    text = text.replace("。。", "。")
    sentences = re.findall(r'.+?[。！!]', text, flags=re.DOTALL)
    sentences = merge_short_sentences(sentences)
    if sentences:
        return [(s, '', None) for s in sentences]

    return [(response_text.strip(), '', None)]


def main_thread():

    while True:
        time.sleep(1)   #防GIL
        if not text_queue.empty():

            this_turn_response=text_queue.get()
            if isinstance(this_turn_response, dict):
                response_type = str(this_turn_response.get("type") or "")
                if response_type == "model_response":
                    handle_model_response_payload(this_turn_response)
                    continue
                if response_type == "exit":
                    this_turn_response = "bye"

            if this_turn_response=='bye':
                if ELECTRON_RENDERER and electron_controller is not None:
                    electron_controller.bye()
                else:
                    emotion_queue.put('bye')    #退出 legacy Live2D 进程
                dp2qt_queue.put("（再见）")
                audio_gen.shutdown_worker()

                # legacy Pygame 进程需要等待；Electron renderer 由 bridge 命令关闭。
                if tr1 is not None:
                    tr1.join()

                QT_message_queue.put('bye')
                break

            if isinstance(this_turn_response, str) and this_turn_response.startswith(NO_AUDIO_TEXT_EVENT_PREFIX):
                no_audio_text = this_turn_response[len(NO_AUDIO_TEXT_EVENT_PREFIX):]
                dp2qt_queue.put(no_audio_text)
                is_audio_play_complete.put('yes')
                is_text_generating_queue.get()  # 让模型停止思考动作
                continue

            audio_gen.audio_language_choice = dp_chat.audio_language_choice
            QT_message_queue.put("整理语言...")

            # --- 解析 LLM 回复为多个段落 ---
            segments = parse_llm_response(this_turn_response)

            # --- 逐段处理：流水线式语音合成 + 播放 ---
            for i, (text, translation, emotion_label) in enumerate(segments):
                QT_message_queue.put(f"正在合成语音...{i+1}/{len(segments)}")
                cleaned_text = clean_text_for_audio(text)

                # 语音合成
                audio_generate_count = 1
                if not dp_chat.if_generate_audio:
                    audio_generate_count = 99

                while audio_generate_count <= 2:
                    try:
                        current_character = dp_chat.get_current_character()
                        audio_gen.audio_file_path = audio_gen.generate_audio_for_character_sync(
                            cleaned_text,
                            current_character,
                            dp_chat.sakiko_state,
                            dp_chat.audio_language_choice,
                            segment_index=i + 1,
                            segment_total=len(segments),
                            emotion=emotion_label or "LABEL_0",
                        )
                        break
                    except Exception as e:
                        QT_message_queue.put("语音合成出错，重试中")
                        audio_generate_count += 1
                        main_logger.exception("语音合成错误")
                        time.sleep(1)

                if audio_generate_count != 1:
                    # 语音合成失败或未启用，让模型停止思考动作
                    if i == 0:
                        is_text_generating_queue.get()  

                    # 将全部剩余文本和翻译逐段传给 qtUI 展示，必须逐段传以保证和 message_list 数量一一对应消耗
                    for rem_text, rem_trans, rem_emotion in segments[i:]:
                        audio_file_path_queue.put('../reference_audio/silent_audio/silence.wav')
                        if rem_trans:
                            dp2qt_queue.put(rem_text + '\n[翻译]' + rem_trans + '[翻译结束]')
                        else:
                            dp2qt_queue.put(rem_text)
                        emotion_queue.put(rem_emotion)
                        time.sleep(0.05)  # 稍微让出排队时间
                    break

                # 语音合成成功 —— 等待上一段播放完毕（避免打断）
                while not motion_complete_value.value:      #为了等待这句话说完，以免下一句先生成完了导致直接打断
                    time.sleep(0.2)

                audio_file_path_queue.put(audio_gen.audio_file_path)

                if i == 0:
                    is_text_generating_queue.get()  # 第一段合成完后让模型停止思考动作

                # 等待当前播放完毕后再送文本到 qtUI（保持顺序）
                while not motion_complete_value.value:
                    time.sleep(0.5)

                # 将本段文本和翻译传给 qtUI 显示
                if translation:
                    dp2qt_queue.put(text + '\n[翻译]' + translation + '[翻译结束]')
                else:
                    dp2qt_queue.put(text)
                emotion_queue.put(emotion_label)

            is_audio_play_complete.put('yes')  # 本轮全部段落处理完毕


if __name__=='__main__':
    # 强制设置多进程实现为 spawn
    multiprocessing.set_start_method('spawn', force=True)
    setup_logging()

    # 添加本文件的目录到导入 Path
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    from runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock
    try:
        runtime_lease = acquire_runtime_lock(project_root, "desktop")
    except RuntimeLockBusy as exc:
        print(str(exc))
        raise SystemExit(1)

    from qconfig import d_sakiko_config

    renderer_mode = resolve_renderer_mode(d_sakiko_config.live2d_renderer.value)
    ELECTRON_RENDERER = renderer_mode == "electron"

    main_logger.info("数字小祥程序...")
    main_logger.info("Live2D 渲染模式：%s", renderer_mode)
    get_all=character.GetCharacterAttributes()
    characters=get_all.character_class_list

    # 初始化全局 ChatManager（自动处理旧版聊天记录迁移）
    chat_manager = get_chat_manager()
    chat_manager.ensure_default_single_character_chat(characters)

    #模块间传参队列
    text_queue=Queue()
    emotion_queue=multiprocessing.Queue()
    audio_file_path_queue=multiprocessing.Queue()
    is_audio_play_complete=Queue()
    is_text_generating_queue=multiprocessing.Queue()
    dp2qt_queue=Queue()
    qt2dp_queue=Queue()
    QT_message_queue=Queue()
    char_is_converted_queue=multiprocessing.Queue()
    change_char_queue=multiprocessing.Queue()
    electron_char_is_converted_queue = char_is_converted_queue
    electron_change_char_queue = change_char_queue
    # Live2D 跨进程通信
    live2d_text_queue=multiprocessing.Queue()  # 用于传递要显示的文本
    is_display_text_value=multiprocessing.Value('b', True)  # 是否显示文本
    motion_complete_value=multiprocessing.Value('b', True)  # 动作是否完成

    if ELECTRON_RENDERER:
        from live2d_controller import Live2DBehaviorController
        from bridge.saki_bridge import Bridge

        electron_event_queue = Queue()
        electron_renderer_messages = Queue()
        electron_ui_commands = Queue()
        electron_controller = Live2DBehaviorController(
            electron_emit,
            motion_catalog={
                "happiness": 6, "sadness": 4, "anger": 7, "disgust": 2,
                "like": 4, "surprise": 4, "fear": 2, "IDLE": 9,
                "text_generating": 4, "bye": 2, "change_character": 3,
                "idle_motion": 1, "talking_motion": 1,
            },
        )
        electron_bridge = Bridge(electron_event_queue, electron_renderer_messages, project_root)
        electron_bridge.start()
        threading.Thread(target=electron_renderer_loop, name="live2d-behavior", daemon=True).start()

    dp_chat=dp_local2.DSLocalAndVoiceGen(characters, chat_manager)

    audio_gen=audio_generator.AudioGenerate(log_queue=get_log_queue())


    audio_gen.initialize(characters,QT_message_queue)


    def get_timestamp_from_filename(filepath):
        """
        从路径中提取时间戳，只为读取字体文件使用
        假设文件名格式为: .../custom_font_1715668823.ttf
        """
        try:
            # 1. 只取文件名: "custom_font_1715668823.ttf"
            filename = os.path.basename(filepath)

            # 2. 去掉后缀: "custom_font_1715668823"
            name_no_ext = os.path.splitext(filename)[0]

            # 3. 取最后一个下划线后面的部分: "1715668823"
            timestamp_str = name_no_ext.split('_')[-1]

            return int(timestamp_str)
        except (IndexError, ValueError):
            return 0  # 如果文件名格式不对，返回0，当作最老的处理
    font_path = ''
    import glob

    font_dir = os.path.join(project_root, 'font')
    font_files = glob.glob(os.path.join(font_dir, 'custom_font_*.*'))
    if not font_files:
        font_path = os.path.join(font_dir, 'msyh.ttc')  # 默认字体路径
    else:
        # 比文件名里的数字大小，而不是比文件系统的元数据
        font_path = max(font_files, key=get_timestamp_from_filename)
        #print(f"检测到最新导入的字体: {font_path}")

        # --- 清理旧文件 (逻辑不变) ---
        for f in font_files:
            if os.path.abspath(f) != os.path.abspath(font_path):
                try:
                    os.remove(f)
                except Exception:
                    pass  # 删不掉就跳过

    qt_app = QApplication(sys.argv)
    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    desktop_w = QDesktopWidget().screenGeometry().width()
    desktop_h = QDesktopWidget().screenGeometry().height()
    screen_w_mid = int(0.5 * desktop_w)
    screen_h_mid = int(0.5 * desktop_h)

    tr1 = None
    if not ELECTRON_RENDERER:
        # Legacy fallback only: importing/starting Pygame is opt-in via
        # DSAKIKO_RENDERER=pygame and never happens in Electron mode.
        import live2d_module
        main_logger.info("加载 legacy Pygame Live2D 界面中...")
        tr1=multiprocessing.Process(target=live2d_module.run_live2d_process,args=(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue,change_char_queue,live2d_text_queue,is_display_text_value,motion_complete_value, desktop_w, desktop_h, get_log_queue()))
    # LLM 生成模块（该模块为不同线程）
    tr2=threading.Thread(target=dp_chat.text_generator,args=(text_queue,
                                                             is_audio_play_complete,
                                                             is_text_generating_queue,
                                                             dp2qt_queue,
                                                             qt2dp_queue,
                                                             QT_message_queue,
                                                             char_is_converted_queue,
                                                             change_char_queue,
                                                             audio_gen))
    # 主要的循环线程
    tr3=threading.Thread(target=main_thread)
    # 更新配置的线程
    tr4 = UpdateConfigThread("d_sakiko_config")
    tr4.reload_requested.connect(d_sakiko_config.reload_from_disk)
    if tr1 is not None:
        tr1.start()
    tr2.start()
    tr3.start()
    tr4.start()

    qt_win = qtUI.ChatGUI(dp2qt_queue=dp2qt_queue,
                          qt2dp_queue=qt2dp_queue,
                          QT_message_queue=QT_message_queue
                          , characters=characters,
                          dp_chat=dp_chat,
                          audio_gen=audio_gen, live2d_text_queue=live2d_text_queue,
                          is_display_text_value=is_display_text_value, motion_complete_value=motion_complete_value,
                          emotion_queue=emotion_queue, audio_file_path_queue=audio_file_path_queue,
                          change_char_queue=change_char_queue,
                          electron_ui_command_queue=electron_ui_commands)

    font_id = QFontDatabase.addApplicationFont(os.path.abspath(font_path))  # 设置字体
    # font_id = -1 表示 Qt 无法加载给定的字体。此时，不设置程序的字体。
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)
        font = QFont(font_family[0], 12)
        qt_app.setFont(font)

    qt_win.move(screen_w_mid, int(screen_h_mid - 0.35 * desktop_h))  # 因为窗口高度设置的是0.7倍桌面宽

    qt_win.show()
    qt_app.exec_()

    # 尝试退出所有子程序。
    # 由于有些程序可能已经退出，所以使用 try-except 来捕获异常，防止程序崩溃。
    try:
        text_queue.put('bye')
    except Exception:
        pass
    try:
        # DeepSeek 推理线程
        qt2dp_queue.put('bye')
    except Exception:
        pass
    try:
        if tr1 is not None:
            change_char_queue.put('exit')
            emotion_queue.put('bye')
    except Exception:
        pass
    try:
        # 主窗口
        QT_message_queue.put('bye')
    except Exception:
        pass
    try:
        # 语音生成 worker
        audio_gen.shutdown_worker()
    except Exception:
        pass

    # 理论上讲 main_thread 函数中已经调用过 tr1.join，等待过 live2d 进程结束；这里再调用一次不是必要的，但也没有副作用。
    if tr1 is not None:
        tr1.join(timeout=3)
    if tr1 is not None and tr1.is_alive():
        try:
            tr1.terminate()
            tr1.join(timeout=3)
        except Exception:
            pass
    if ELECTRON_RENDERER and electron_controller is not None:
        electron_controller.close()
        if electron_bridge is not None:
            electron_bridge.shutdown()
    tr2.join()
    tr3.join()
    tr4.quit()
    tr4.wait(3000)

    shutdown_logging()

    if_delete = d_sakiko_config.delete_audio_cache_on_exit.value

    if if_delete:
        folder_path = '../reference_audio/generated_audios_temp'    #删除音频缓存

        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
# 修改库的源码：
# ffmpeg/_run.py 196
# jieba_fast/__init__.py 117/136/150/168/170
# AR/models/t2s_model.py 560/736/875
# text/chinese2.py 27
# runtime\Lib\site-packages\pygame\__init__.py 336
# AR\models\\t2s_model.py 845
# runtime\Lib\site-packages\live2d\\utils\lipsync.py 55 防止出现 nan，使程序崩溃
# runtime\Lib\site-packages/live2d/v2/core/graphics/draw_param_opengl.py 45 330 解决腮红变黑问题
# runtime/Lib/site-packages/live2d/v2/lapp_model.py 173
# runtime\Lib\site-packages\\faster_whisper\\transcribe.py
# inference_webui.py 大改
# inference_cli.py 大改
#
# 更改角色皮肤
# 可更改参考音频
# 可重新生成音频
