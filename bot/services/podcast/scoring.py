"""Scoring for podcast episode candidates.

Duration uses a **percentage** tolerance rather than a fixed window because
dynamic ad insertion changes an episode's length between platforms, so a fixed
±4s (the music matcher's rule) would reject valid copies. A candidate within
``max(tol_min_s, tol_pct * duration)`` counts as a match, and title and show
carry most of the weight.
"""

from __future__ import annotations

from bot.services.spotify.matcher import text_similarity

WEIGHT_TITLE = 0.45
WEIGHT_SHOW = 0.35
WEIGHT_DURATION = 0.20


def duration_tolerance(expected_s: float | None, tol_min_s: int, tol_pct: float) -> float | None:
    """The allowed drift in seconds for ``expected_s``, or None when unknown."""
    if not expected_s or expected_s <= 0:
        return None
    return max(float(tol_min_s), float(tol_pct) * float(expected_s))


def duration_score(expected_s: float | None, actual_s: float | None, tol_s: float | None) -> float | None:
    """0..1 closeness, renormalised over the tolerance window; None if unusable."""
    if not expected_s or not actual_s or not tol_s:
        return None
    drift = abs(float(expected_s) - float(actual_s))
    return round(max(0.0, 1.0 - drift / max(1.0, tol_s)), 4)


def score_candidate(
    *,
    title: str | None,
    show: str | None,
    duration_s: float | None,
    cand_title: str | None,
    cand_show: str | None,
    cand_duration_s: float | None,
    tol_min_s: int,
    tol_pct: float,
) -> float:
    """Weighted 0..1 score; components with missing data are renormalised out."""
    parts: list[tuple[float, float]] = [(text_similarity(title, cand_title), WEIGHT_TITLE)]
    if show:
        parts.append((text_similarity(show, cand_show), WEIGHT_SHOW))

    tol = duration_tolerance(duration_s, tol_min_s, tol_pct)
    d_score = duration_score(duration_s, cand_duration_s, tol)
    if d_score is not None:
        parts.append((d_score, WEIGHT_DURATION))

    total_weight = sum(w for _, w in parts) or 1.0
    return round(sum(v * w for v, w in parts) / total_weight, 4)
