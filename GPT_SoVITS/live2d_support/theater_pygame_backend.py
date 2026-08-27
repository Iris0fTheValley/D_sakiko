"""Two-slot Pygame mechanics backend for authoritative theater commands."""
from __future__ import annotations

import contextlib
import glob
import os
import queue
from uuid import uuid4

from live2d.utils.lipsync import WavHandler

with open(os.devnull, "w") as devnull:
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        import pygame
        from pygame.locals import DOUBLEBUF, OPENGL

from OpenGL.GL import *

from live2d_module import BackgroundRen
from live2d_support.layout import Live2DLayout, get_live2d_layout
from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    detect_live2d_runtime_version,
    initialize_live2d_runtime,
    load_live2d_runtime,
    release_live2d_runtime,
)
from live2d_support.shared_segment_executor import PygameRendererCommandAdapter
from live2d_support.text_overlay import TextOverlay
from log import get_logger, setup_worker_logging
from qconfig import d_sakiko_config


def run_theater_pygame_backend(
    renderer_fact_queue,
    renderer_command_queue,
    live2d_text_queue,
    is_display_text_value,
    motion_complete_value,
    desktop_width,
    desktop_height,
    log_queue,
) -> None:
    setup_worker_logging(log_queue)
    logger = get_logger(__name__)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    height = int(0.7 * desktop_height)
    width = int(height * 1.33)
    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{int(0.55 * desktop_width - width)},{int(0.5 * desktop_height - height / 2)}"
    pygame.init()
    pygame.display.set_mode((width, height), DOUBLEBUF | OPENGL)
    pygame.display.set_caption("数字小祥 小剧场")
    frame_clock = pygame.time.Clock()
    target_fps = 60
    instance_id = f"pygame-theater-{uuid4().hex}"
    model_token = ""
    runtime = None
    runtime_version = None
    models: list[Live2DModelAdapter | None] = [None, None]
    slots: list[dict] = []
    layouts = [Live2DLayout(0.8, 0.0, 0.0), Live2DLayout(0.8, 0.0, 0.0)]
    overlay = TextOverlay((width, height), ["小剧场"])
    wav_handler = WavHandler()
    audio_token = ""
    audio_was_busy = False
    lip_sync_slot: int | None = None

    backgrounds = glob.glob(os.path.join("../live2d_related", "*.jpg")) + glob.glob(os.path.join("../live2d_related", "*.png"))
    if not backgrounds:
        raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)")
    configured_background = d_sakiko_config.background_image_path.value
    background_index = backgrounds.index(configured_background) if configured_background in backgrounds else 0
    texture = BackgroundRen.render(pygame.image.load(backgrounds[background_index]).convert_alpha())

    def emit(payload: dict) -> None:
        data = dict(payload.get("data") or {})
        data.setdefault("renderer_id", "pygame-renderer")
        data.setdefault("renderer_instance_id", instance_id)
        payload = dict(payload)
        payload["data"] = data
        renderer_fact_queue.put(payload)

    def dispose_models() -> None:
        for index, model in enumerate(models):
            if model is not None:
                try:
                    model.dispose()
                except Exception:
                    logger.debug("释放小剧场模型失败", exc_info=True)
            models[index] = None

    def apply_layout(slot: int, model: Live2DModelAdapter) -> None:
        model.Resize(width, height)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        layout = layouts[slot]
        model.SetScale(layout.scale)
        model.SetOffset((-0.4 if slot == 0 else 0.4) + layout.offset_x, layout.offset_y)

    def stage_catalog() -> tuple[dict, dict]:
        slot_catalogs = {}
        canonical = {}
        for slot, model in enumerate(models):
            if model is None:
                continue
            catalog = {
                "motion_files_by_group": dict(model.motion_files_by_group),
                "expression_ids": list(model.expression_ids),
            }
            slot_catalogs[str(slot)] = catalog
            if not canonical:
                canonical = catalog
        return slot_catalogs, canonical

    def emit_ready() -> None:
        slot_catalogs, canonical = stage_catalog()
        emit({
            "type": "renderer_ready",
            "data": {
                "model_token": model_token,
                "model_key": "theater",
                "motion_files_by_group": canonical.get("motion_files_by_group", {}),
                "expression_ids": canonical.get("expression_ids", []),
                "slot_catalogs": slot_catalogs,
                "visible_slots": sorted(int(slot) for slot in slot_catalogs),
                "capabilities": {"motion": bool(slot_catalogs), "audio": True, "lipsync": bool(slot_catalogs)},
            },
        })

    def load_stage(data: dict) -> None:
        nonlocal runtime, runtime_version, slots, model_token, overlay, wav_handler
        raw_slots = data.get("theater_slots")
        if not isinstance(raw_slots, list):
            emit({"type": "renderer_unavailable", "data": {"reason": "missing_theater_slots"}})
            return
        normalized = [dict(value) for value in raw_slots if isinstance(value, dict)]
        normalized.sort(key=lambda value: int(value.get("slot", 0)))
        if len(normalized) != 2:
            emit({"type": "renderer_unavailable", "data": {"reason": "invalid_theater_slots"}})
            return
        versions = []
        for slot in normalized:
            path = str(slot.get("model_json_path") or "")
            try:
                versions.append(detect_live2d_runtime_version(path) if path else None)
            except Exception:
                logger.exception("无法识别小剧场模型：%s", path)
                versions.append(None)
        target_version = next((version for version in versions if version is not None), None)
        dispose_models()
        if target_version != runtime_version:
            release_live2d_runtime(runtime)
            runtime = load_live2d_runtime(target_version) if target_version else None
            if runtime is not None:
                initialize_live2d_runtime(runtime)
            runtime_version = target_version
        slots = normalized
        for slot_index, slot in enumerate(slots):
            path = str(slot.get("model_json_path") or "")
            if not path or versions[slot_index] != runtime_version:
                continue
            try:
                model = Live2DModelAdapter.create(path)
                layouts[slot_index] = get_live2d_layout(path, model.version, "theater")
                apply_layout(slot_index, model)
                models[slot_index] = model
            except Exception:
                logger.exception("加载小剧场 slot=%d 模型失败：%s", slot_index, path)
        model_token = str(data.get("model_token") or "")
        wav_handler = WavHandler()
        names = [str(slot.get("character_name") or f"slot{index}") for index, slot in enumerate(slots)]
        overlay = TextOverlay((width, height), names)
        emit_ready()

    def start_audio(path: str) -> bool:
        nonlocal wav_handler
        if not path or not os.path.exists(path):
            return False
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            if path != "../reference_audio/silent_audio/silence.wav":
                wav_handler.Start(path)
            return True
        except Exception:
            logger.debug("小剧场音频播放失败：%s", path, exc_info=True)
            return False

    def execute(command: dict) -> None:
        nonlocal audio_token, audio_was_busy, lip_sync_slot, target_fps, texture, background_index, wav_handler
        command_type = str(command.get("type") or "")
        data = command.get("data")
        data = dict(data) if isinstance(data, dict) else {}
        if command_type == "switch_live2d":
            load_stage(data)
            return
        if command_type == "close_renderer":
            raise SystemExit
        if command_type in {"stop_audio", "reset"}:
            pygame.mixer.music.stop()
            wav_handler = WavHandler()
            audio_token = ""
        if command_type in {"stop_motion", "reset", "thinking_changed"}:
            return
        if command_type == "switch_l2d_fps":
            fps = int(data.get("fps", target_fps))
            if fps in (30, 60, 120):
                target_fps = fps
            return
        if command_type == "change_l2d_background":
            background_index = (background_index + 1) % len(backgrounds)
            glDeleteTextures([texture])
            texture = BackgroundRen.render(pygame.image.load(backgrounds[background_index]).convert_alpha())
            return
        if command_type not in {"play_motion", "play_audio"}:
            return
        target_slot = data.get("target_slot")
        target_slot = target_slot if target_slot in (0, 1) else 0
        model = models[target_slot]
        if model is None:
            emit({"type": "command_failed", "data": {"token": str(data.get("token") or ""), "phase": "motion_start" if command_type == "play_motion" else "audio_start"}})
            return
        adapter = PygameRendererCommandAdapter(model, emit, start_audio)
        if command_type == "play_audio":
            lip_sync_slot = target_slot
        if adapter.execute(command) and command_type == "play_audio":
            audio_token = str(data.get("token") or "")
            audio_was_busy = True

    emit({"type": "renderer_hello", "data": {"model_token": "", "model_key": "theater"}})
    running = True
    try:
        while running:
            while True:
                try:
                    command = renderer_command_queue.get_nowait()
                except queue.Empty:
                    break
                if not isinstance(command, dict):
                    continue
                try:
                    execute(command)
                except SystemExit:
                    running = False
                    break

            busy = pygame.mixer.music.get_busy()
            if audio_token and audio_was_busy and not busy:
                emit({"type": "audio_ended", "data": {"token": audio_token}})
                audio_token = ""
                motion_complete_value.value = True
            audio_was_busy = busy
            if busy:
                motion_complete_value.value = False

            latest = None
            while True:
                try:
                    latest = live2d_text_queue.get_nowait()
                except queue.Empty:
                    break
            if isinstance(latest, dict):
                overlay.set_text(str(latest.get("character_name") or ""), str(latest.get("text") or ""))

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    emit({"type": "renderer_intent", "data": {"intent": "bye"}})
                    running = False

            glClear(GL_COLOR_BUFFER_BIT)
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, texture)
            BackgroundRen.blit((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
            mouth_value = wav_handler.GetRms() * 1.4 if busy and wav_handler.Update() else 0.0
            for slot, model in enumerate(models):
                if model is None:
                    continue
                model.Update()
                model.set_parameter_value("mouth_open_y", mouth_value if slot == lip_sync_slot else 0.0)
                model.Draw()
            overlay.update()
            if is_display_text_value.value:
                overlay.draw()
            pygame.display.flip()
            frame_clock.tick(target_fps)
    finally:
        pygame.mixer.music.stop()
        dispose_models()
        glDeleteTextures([texture])
        release_live2d_runtime(runtime)
        pygame.quit()
