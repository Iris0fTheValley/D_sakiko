"""Master Sakiko black/white/mask decisions, independent of renderer reloads."""
from __future__ import annotations

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class SakikoConversionDecision:
    model_target: str
    semantic_expression: str | None
    motion_group: str
    priority: int
    purpose: str
    fixed_index: int | None = None


class SharedSakikoConversion:
    """Owns master-only conversion state; adapters only reload and execute."""

    def __init__(self, rng: Random | None = None) -> None:
        self._rng = rng or Random()
        self.is_black = True
        self.mask_on = True

    def decide(self, conversion) -> SakikoConversionDecision:
        if conversion == "maskoff":
            return self._toggle_mask()
        if conversion:
            self.is_black = True
            self.mask_on = self._rng.random() < 0.5
            return SakikoConversionDecision(
                "black", "serious",
                "change_character" if self.mask_on else "change_character_maskoff",
                2, "sakiko_black",
            )
        self.is_black = False
        return SakikoConversionDecision("white", "idle", "change_character", 2, "sakiko_white")

    def _toggle_mask(self) -> SakikoConversionDecision:
        if not self.is_black:
            return SakikoConversionDecision("current", None, "text_generating", 3, "sakiko_white_toggle", 0)
        group = "change_character_maskoff" if self.mask_on else "maskon"
        self.mask_on = not self.mask_on
        return SakikoConversionDecision("current", None, group, 3, "sakiko_mask_toggle")
