"""
Internal limbic state: inertial blend of classifier signal and current emotion map.

The signal (BERT) is a target, not an instant replacement. Per-axis update:
  new_i = clamp( current_i + alpha * (signal_i - current_i) )
"""

from __future__ import annotations

# Order matches EmotionDetector / rubert-tiny2-russian-emotion-detection
EMOTION_LABELS = (
    "neutral",
    "happiness",
    "sadness",
    "enthusiasm",
    "fear",
    "anger",
    "disgust",
)

DEFAULT_BASELINE: dict[str, float] = {
    "neutral": 0.500,
    "happiness": 0.250,
    "enthusiasm": 0.250,
    "sadness": 0.000,
    "fear": 0.000,
    "anger": 0.000,
    "disgust": 0.000,
}

DEFAULT_BLEND_ALPHA = 0.25
DEFAULT_MAX_STEP = 0.30


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def format_emotion_vector(vec: dict[str, float]) -> str:
    ranked = sorted(
        ((k, vec.get(k, 0.0)) for k in EMOTION_LABELS),
        key=lambda item: -item[1],
    )
    return ", ".join(f"{k}={v:.3f}" for k, v in ranked)


class LimbicState:
    def __init__(
        self,
        baseline: dict[str, float] | None = None,
        blend_alpha: float = DEFAULT_BLEND_ALPHA,
        max_step: float | None = DEFAULT_MAX_STEP,
    ):
        self.baseline = self._normalize_baseline(baseline or DEFAULT_BASELINE)
        self.blend_alpha = blend_alpha
        self.max_step = max_step
        self.current = dict(self.baseline)

    @staticmethod
    def _normalize_baseline(raw: dict[str, float]) -> dict[str, float]:
        out = {label: 0.0 for label in EMOTION_LABELS}
        for label in EMOTION_LABELS:
            out[label] = _clamp01(raw.get(label, 0.0))
        return out

    def reset(self) -> None:
        self.current = dict(self.baseline)

    def snapshot(self) -> dict[str, float]:
        return dict(self.current)

    def load_snapshot(self, vec: dict[str, float]) -> None:
        """Restore runtime state from saved vector (DB / session)."""
        self.current = self._normalize_baseline(vec)

    def step_toward_baseline(self) -> dict[str, float]:
        """One inertial step toward baseline (decay without BERT)."""
        return self.blend_signal(self.baseline)

    def is_at_baseline(self, epsilon: float = 1e-3) -> bool:
        for label in EMOTION_LABELS:
            cur = self.current.get(label, self.baseline[label])
            if abs(cur - self.baseline[label]) > epsilon:
                return False
        return True

    def decay_until_baseline(self, max_steps: int) -> int:
        """
        Up to max_steps toward baseline; stop when baseline reached.
        Returns number of steps actually applied.
        """
        applied = 0
        for _ in range(max(0, int(max_steps))):
            if self.is_at_baseline():
                break
            self.step_toward_baseline()
            applied += 1
        return applied

    def blend_signal(self, signal: dict[str, float]) -> dict[str, float]:
        """
        signal — classifier probabilities (sum ≈ 1).
        New state after one inertial step.
        """
        alpha = self.blend_alpha
        new_state: dict[str, float] = {}
        for label in EMOTION_LABELS:
            cur = self.current.get(label, self.baseline[label])
            target = _clamp01(signal.get(label, 0.0))
            delta = target - cur
            if self.max_step is not None:
                if delta > self.max_step:
                    delta = self.max_step
                elif delta < -self.max_step:
                    delta = -self.max_step
            new_state[label] = _clamp01(cur + alpha * delta)
        self.current = new_state
        return dict(self.current)

    def top_label(self, vec: dict[str, float] | None = None) -> str:
        data = vec if vec is not None else self.current
        return max(EMOTION_LABELS, key=lambda k: data.get(k, 0.0))
