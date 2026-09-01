"""Pure Phase 3 analysis helpers — no demo I/O.

Speed is 1-tick horizontal position difference. Native demo velocity is a
check column only (never ffilled over posdiff). Rifle accuracy uses
weapon_max * 0.34, not a flat 130 u/s. Input-cause classes are positive
evidence; leftover rows are UNCLASSIFIED, never RELEASE_ONLY.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

TICKRATE = 64.0
INACCURACY_FRAC = 0.34
SYNC_GATE_MS = 30.0
# Test A: digital-on at ~6–7% analog depth.
ANALOG_DOWN = int(0.07 * 65535)  # 4587
STATIONARY_SPEED = 20.0
DELAYED_GAP_MS = 100.0
OVERLAP_MS = 50.0
RESID_MATCH_MAX_MS = 400.0

WEAPON_MAX_SPEED = {
    "weapon_ak47": 215.0,
    "weapon_m4a1_silencer": 225.0,
    "weapon_m4a1": 225.0,
    "weapon_galilar": 215.0,
    "weapon_famas": 220.0,
    "weapon_awp": 200.0,
    "weapon_ssg08": 230.0,
    "weapon_deagle": 230.0,
    "weapon_usp_silencer": 240.0,
    "weapon_glock": 240.0,
    "weapon_tec9": 240.0,
    "weapon_elite": 240.0,
    "weapon_mac10": 240.0,
}
AWP_SCOPED_MAX = 100.0

RIFLES = frozenset({
    "weapon_ak47",
    "weapon_m4a1_silencer",
    "weapon_m4a1",
    "weapon_galilar",
    "weapon_famas",
})


def max_speed(weapon: str, is_scoped: bool = False) -> Optional[float]:
    if weapon == "weapon_awp" and is_scoped:
        return AWP_SCOPED_MAX
    return WEAPON_MAX_SPEED.get(weapon)


def accuracy_threshold(weapon: str, is_scoped: bool = False) -> Optional[float]:
    ms = max_speed(weapon, is_scoped)
    if ms is None:
        return None
    return ms * INACCURACY_FRAC


def is_headline_rifle(weapon: str) -> bool:
    return weapon in RIFLES


def rifle_accurate(weapon: str, speed: float, is_scoped: bool = False) -> Optional[bool]:
    if not is_headline_rifle(weapon):
        return None
    th = accuracy_threshold(weapon, is_scoped)
    if th is None or speed != speed:  # NaN
        return None
    return float(speed) < th


def horizontal_speed(x0: float, y0: float, x1: float, y1: float, dt_ticks: float,
                     tickrate: float = TICKRATE) -> float:
    if dt_ticks == 0:
        return float("nan")
    dt_s = dt_ticks / tickrate
    return math.hypot(x1 - x0, y1 - y0) / dt_s


def is_first_bullet(shots_fired) -> bool:
    if shots_fired is None:
        return False
    try:
        if shots_fired != shots_fired:  # NaN
            return False
        return int(shots_fired) == 1
    except (TypeError, ValueError):
        return False


def ffill_numeric(values: np.ndarray) -> np.ndarray:
    out = np.array(values, dtype=float).copy()
    last = math.nan
    for i, v in enumerate(out):
        if v == v:  # not NaN
            last = v
        elif last == last:
            out[i] = last
    return out


def rising_edges(down: np.ndarray) -> np.ndarray:
    d = np.asarray(down, dtype=bool)
    if d.size == 0:
        return np.array([], dtype=int)
    prev = np.empty_like(d)
    prev[0] = False
    prev[1:] = d[:-1]
    return np.flatnonzero(d & ~prev)


def nearest_residual_ms(pred_ms: float, edge_ms, max_abs_ms: float = RESID_MATCH_MAX_MS):
    if edge_ms is None:
        return None, None
    edges = np.asarray(edge_ms, dtype=float)
    if edges.size == 0 or pred_ms != pred_ms:
        return None, None
    i = int(np.argmin(np.abs(edges - pred_ms)))
    matched = float(edges[i])
    resid = matched - float(pred_ms)
    if abs(resid) > max_abs_ms:
        return None, None
    return resid, matched


def ad_counter_metrics(a_down: np.ndarray, d_down: np.ndarray):
    """Return (gap_ms, overlap_ms) if both A and D appear and a release→onset pair exists.

    Sample index is treated as 1 ms (recorder is ~1 kHz). None, None if no counter pair.
    """
    a = np.asarray(a_down, dtype=bool)
    d = np.asarray(d_down, dtype=bool)
    if a.size == 0 or not a.any() or not d.any():
        return None, None
    if int(a.sum()) >= int(d.sum()):
        own, opp = a, d
    else:
        own, opp = d, a
    release_i = None
    for i in range(len(own) - 1, -1, -1):
        if (not own[i]) and (i == 0 or own[i - 1]):
            release_i = i
            break
    onset_i = None
    for i in range(len(opp)):
        if opp[i] and (i == 0 or not opp[i - 1]):
            if release_i is None or i >= release_i:
                onset_i = i
                break
    if release_i is None or onset_i is None:
        return None, None
    overlap = int((own & opp).sum())
    return float(onset_i - release_i), float(overlap)


def analog_down(v) -> bool:
    if v is None or v != v:
        return False
    return float(v) >= ANALOG_DOWN


def classify_input(
    *,
    map_name: str,
    residual_ms,
    speed: float,
    w_max,
    a_max,
    s_max,
    d_max,
    gap_ms,
    overlap_ms,
    sync_gate_ms: float = SYNC_GATE_MS,
) -> dict:
    ws = analog_down(w_max) or analog_down(s_max)
    ad = analog_down(a_max) or analog_down(d_max)

    if map_name == "de_cache":
        return {
            "sync_ok": False,
            "ws_involved": ws,
            "ad_involved": ad,
            "input_class": "SKIP_CACHE",
        }

    if residual_ms is None or residual_ms != residual_ms or abs(float(residual_ms)) > sync_gate_ms:
        return {
            "sync_ok": False,
            "ws_involved": ws,
            "ad_involved": ad,
            "input_class": "SYNC_UNCERTAIN",
        }

    if gap_ms is not None and gap_ms == gap_ms:
        if overlap_ms is not None and overlap_ms == overlap_ms and float(overlap_ms) > OVERLAP_MS:
            cls = "COUNTER_OVERLAP"
        elif float(gap_ms) > DELAYED_GAP_MS:
            cls = "DELAYED_OPPOSITE"
        else:
            cls = "COUNTER_CLEAN"
        return {
            "sync_ok": True,
            "ws_involved": ws,
            "ad_involved": ad,
            "input_class": cls,
        }

    if ad and ws:
        cls = "DIAGONAL"
    elif ad:
        cls = "RELEASE_NO_COUNTER"
    elif ws:
        cls = "FORWARD_BACK"
    elif speed == speed and float(speed) < STATIONARY_SPEED:
        cls = "STATIONARY"
    else:
        cls = "UNCLASSIFIED"

    return {
        "sync_ok": True,
        "ws_involved": ws,
        "ad_involved": ad,
        "input_class": cls,
    }


def fit_offset_s(t_demo_s: np.ndarray, t_csi_s: np.ndarray) -> float:
    return float(np.median(np.asarray(t_csi_s, dtype=float) - np.asarray(t_demo_s, dtype=float)))


def fit_slope_offset(t_demo_s: np.ndarray, t_csi_s: np.ndarray) -> tuple[float, float]:
    """t_csi = a * t_demo + b. Returns (a, b)."""
    x = np.asarray(t_demo_s, dtype=float)
    y = np.asarray(t_csi_s, dtype=float)
    if x.size < 2:
        b = fit_offset_s(x, y) if x.size else 0.0
        return 1.0, b
    a, b = np.polyfit(x, y, 1)
    return float(a), float(b)
