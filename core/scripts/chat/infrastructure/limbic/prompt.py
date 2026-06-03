"""
Map LimbicState → short text for the limbic role in chat_template.jinja.
Wording from infrastructure/locale/variables/{locale}.json.
"""

from __future__ import annotations

from infrastructure.config.defaults import DEFAULT_UI_LOCALE
from infrastructure.limbic.state import EMOTION_LABELS, LimbicState
from infrastructure.locale.variables import var_dict, var_get

DELTA_THRESHOLD = 0.06
INTENSITY_MED_DELTA = 0.15
INTENSITY_HIGH_DELTA = 0.30


def _intensity_adverb(delta: float, locale: str) -> str:
    adv = var_dict("limbic.intensity_adverb", locale)
    if delta >= INTENSITY_HIGH_DELTA:
        return adv.get("high", "strongly")
    if delta >= INTENSITY_MED_DELTA:
        return adv.get("medium", "noticeably")
    return adv.get("low", "slightly")


def _significant_axes(
    current: dict[str, float],
    baseline: dict[str, float],
) -> list[tuple[str, float, float]]:
    """(label, current, delta) sorted by delta descending."""
    rows: list[tuple[str, float, float]] = []
    for label in EMOTION_LABELS:
        if label == "neutral":
            continue
        cur = float(current.get(label, 0.0))
        base = float(baseline.get(label, 0.0))
        delta = cur - base
        if delta >= DELTA_THRESHOLD:
            rows.append((label, cur, delta))
    rows.sort(key=lambda x: (-x[2], -x[1]))
    return rows


def render_limbic_prompt(
    limbic: LimbicState,
    locale: str | None = None,
    *,
    format_vars: dict[str, str] | None = None,
) -> str | None:
    """Text for role=limbic, or None if state is near baseline."""
    loc = locale or DEFAULT_UI_LOCALE
    mood_map = var_dict("limbic.mood", loc)
    behavior_map = var_dict("limbic.behavior", loc)
    footer = str(var_get("limbic.footer", loc) or "")
    line_one = str(var_get("limbic.mood_line_one", loc) or "Right now you are {mood}.")
    line_two = str(var_get("limbic.mood_line_two", loc) or "Right now you are {mood_a} and {mood_b}.")

    current = limbic.snapshot()
    baseline = limbic.baseline
    axes = _significant_axes(current, baseline)
    if not axes:
        return None

    top = axes[:2]
    mood_parts: list[str] = []
    adv_map = var_dict("limbic.intensity_adverb", loc)
    for label, _cur, delta in top:
        adv = _intensity_adverb(delta, loc)
        mood = mood_map.get(label, label)
        if adv == adv_map.get("high"):
            mood_parts.append(f"{adv} {mood}")
        elif adv == adv_map.get("medium"):
            mood_parts.append(f"{adv} {mood}")
        else:
            mood_parts.append(f"{adv} {mood}")

    if len(mood_parts) == 1:
        mood_line = line_one.format(mood=mood_parts[0])
    else:
        mood_line = line_two.format(mood_a=mood_parts[0], mood_b=mood_parts[1])

    behavior_lines = [behavior_map.get(label, "") for label, _, _ in top]
    behavior_lines = [b for b in behavior_lines if b]
    behavior = " ".join(dict.fromkeys(behavior_lines))
    if format_vars:
        try:
            behavior = behavior.format(**format_vars)
        except (KeyError, ValueError):
            pass

    return f"{mood_line} {behavior} {footer}".strip()


def render_limbic_prompt_from_snapshot(
    current: dict[str, float],
    baseline: dict[str, float] | None = None,
    locale: str | None = None,
) -> str | None:
    """Convenience for tests without full LimbicState."""
    stub = LimbicState(baseline=baseline)
    stub.current = dict(current)
    return render_limbic_prompt(stub, locale=locale)
