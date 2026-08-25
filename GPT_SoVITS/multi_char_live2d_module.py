"""Compatibility boundary for the retired two-slot theater renderer.

The desktop application no longer starts this module.  Its old renderer-side
behavior scheduler was removed; only the slot normalization helpers retained
by existing theater configuration tests remain.  The production single-model
renderer imports :class:`TextOverlay` from ``live2d_support.text_overlay``.
"""
from __future__ import annotations

import logging
from typing import Any

from live2d_support.runtime_adapter import Live2DVersion, detect_live2d_runtime_version
from live2d_support.text_overlay import TextOverlay

logger = logging.getLogger(__name__)


class Live2DModule:
    """Legacy API shim for theater slot validation, without runtime behavior."""

    def _normalize_slots_payload(self, slots_payload: object) -> list[dict[str, object]]:
        if not isinstance(slots_payload, list) or len(slots_payload) != 2:
            raise ValueError("set_active_slots 需要提供两个槽位")

        normalized: list[dict[str, object]] = []
        for expected_slot in (0, 1):
            slot_data: dict[str, Any] | None = None
            for item in slots_payload:
                if isinstance(item, dict) and item.get("slot") == expected_slot:
                    slot_data = item
                    break
            if slot_data is None:
                raise ValueError(f"缺少 slot={expected_slot} 的配置")

            character_name = str(slot_data.get("character_name", "")).strip()
            raw_model_json_path = slot_data.get("model_json_path")
            model_json_path = (
                raw_model_json_path.strip()
                if isinstance(raw_model_json_path, str) and raw_model_json_path.strip()
                else None
            )
            if not character_name:
                raise ValueError(f"slot={expected_slot} 缺少 character_name")

            model_version: Live2DVersion | None = None
            if model_json_path is not None:
                try:
                    model_version = detect_live2d_runtime_version(model_json_path)
                except Exception:
                    logger.exception(
                        "小剧场 slot=%d Live2D 模型目标无法识别，将使用空模型槽位：%s",
                        expected_slot,
                        model_json_path,
                    )
            normalized.append({
                "slot": expected_slot,
                "character_name": character_name,
                "model_json_path": model_json_path,
                "model_version": model_version,
            })
        return normalized

    @staticmethod
    def _select_runtime_version(
        versions: list[Live2DVersion | None], changed_slot: int | None,
    ) -> Live2DVersion | None:
        available_versions = [version for version in versions if version is not None]
        if not available_versions:
            return None
        if len(set(available_versions)) == 1:
            return available_versions[0]
        if versions[0] == versions[1]:
            return versions[0]
        selected_slot = changed_slot if changed_slot in (0, 1) else 0
        return versions[selected_slot] or available_versions[0]


__all__ = ["Live2DModule", "TextOverlay"]
