"""Pure Phase 3 analysis helpers — no demo I/O.

Speed is 1-tick horizontal position difference. Native demo velocity is a
check column only (never ffilled over posdiff). Rifle accuracy uses
weapon_max * 0.34, not a flat 130 u/s. Input-cause classes are positive
evidence; leftover rows are TRANSITION_AMBIGUOUS, never RELEASE_ONLY.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

TICKRATE = 64.0
INACCURACY_FRAC = 0.34
SYNC_GATE_MS = 30.0
ANALOG_DOWN = int(0.07 * 65535)  # 4587; Test A digital-on
STATIONARY_SPEED = 20.0
DELAYED_GAP_MS = 100.0
OVERLAP_MS = 50.0
RESID_MATCH_MAX_MS = 400.0
NATIVE_DISAGREE_U = 30.0
DUCK_STANDING_MAX = 0.1
DUCK_CROUCHED_MIN = 0.9
BURST_GAP_S = 1.5
BURST_MATCH_WINDOW_S = 0.4

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

KEY_META = {
    "W": ("fb", "+", 0x01),
    "A": ("ad", "-", 0x02),
    "S": ("fb", "-", 0x04),
    "D": ("ad", "+", 0x08),
}

OPPOSITE_DIAG = {
    frozenset({"W", "A"}): frozenset({"S", "D"}),
    frozenset({"W", "D"}): frozenset({"S", "A"}),
    frozenset({"S", "A"}): frozenset({"W", "D"}),
    frozenset({"S", "D"}): frozenset({"W", "A"}),
}


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
    if th is None or speed != speed:
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
        if shots_fired != shots_fired:
            return False
        return int(shots_fired) == 1
    except (TypeError, ValueError):
        return False


def ffill_numeric(values: np.ndarray) -> np.ndarray:
    out = np.array(values, dtype=float).copy()
    last = math.nan
    for i, v in enumerate(out):
        if v == v:
            last = v
        elif last == last:
            out[i] = last
    return out


def speed_disagreement(posdiff, native, thresh: float = NATIVE_DISAGREE_U) -> bool:
    if posdiff is None or native is None:
        return False
    try:
        p = float(posdiff)
        n = float(native)
    except (TypeError, ValueError):
        return False
    if p != p or n != n:
        return False
    return abs(p - n) > thresh


def posture_label(is_airborne, duck_amount) -> str:
    if is_airborne:
        return "air"
    try:
        if duck_amount is None or duck_amount != duck_amount:
            duck = 0.0
        else:
            duck = float(duck_amount)
    except (TypeError, ValueError):
        duck = 0.0
    if duck >= DUCK_CROUCHED_MIN:
        return "crouched"
    if duck >= DUCK_STANDING_MAX:
        return "transition"
    return "standing"


def is_standing_on_ground(is_airborne, duck_amount) -> bool:
    return posture_label(is_airborne, duck_amount) == "standing"


def rising_edges(down: np.ndarray) -> np.ndarray:
    d = np.asarray(down, dtype=bool)
    if d.size == 0:
        return np.array([], dtype=int)
    prev = np.empty_like(d)
    prev[0] = False
    prev[1:] = d[:-1]
    return np.flatnonzero(d & ~prev)


def falling_edges(down: np.ndarray) -> np.ndarray:
    d = np.asarray(down, dtype=bool)
    if d.size == 0:
        return np.array([], dtype=int)
    prev = np.empty_like(d)
    prev[0] = False
    prev[1:] = d[:-1]
    return np.flatnonzero(prev & ~d)


def match_monotonic(pred_ms, edge_ms, max_abs_ms: float = RESID_MATCH_MAX_MS) -> dict:
    """One-to-one monotonic matching of predicted times to CSI edges.

    Maximizes match count, then minimizes sum of |residual|.
    """
    pred = np.asarray(pred_ms, dtype=float)
    edge = np.asarray(edge_ms, dtype=float)
    n, m = int(pred.size), int(edge.size)
    if n == 0:
        return {"residual_ms": [], "matched_ms": [], "edge_index": []}
    if m == 0:
        return {
            "residual_ms": [None] * n,
            "matched_ms": [None] * n,
            "edge_index": [None] * n,
        }

    big = 1_000_000.0
    neg = -1e18
    dp = np.full((n + 1, m + 1), neg)
    dp[0, :] = 0.0
    dp[:, 0] = 0.0
    ch = np.zeros((n + 1, m + 1), dtype=np.uint8)

    for i in range(n):
        pi = pred[i]
        dp_i = dp[i]
        dp_i1 = dp[i + 1]
        ch_i1 = ch[i + 1]
        for j in range(m):
            s_skip_pred = dp_i[j + 1]
            s_skip_edge = dp_i1[j]
            resid = edge[j] - pi
            s_match = neg
            if abs(resid) <= max_abs_ms:
                s_match = dp_i[j] + (big - abs(resid))
            best, c = s_skip_pred, 0
            if s_skip_edge > best:
                best, c = s_skip_edge, 1
            if s_match > best:
                best, c = s_match, 2
            dp_i1[j + 1] = best
            ch_i1[j + 1] = c

    residual = [None] * n
    matched = [None] * n
    eidx = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        c = int(ch[i, j])
        if c == 2:
            residual[i - 1] = float(edge[j - 1] - pred[i - 1])
            matched[i - 1] = float(edge[j - 1])
            eidx[i - 1] = j - 1
            i -= 1
            j -= 1
        elif c == 1:
            j -= 1
        else:
            i -= 1

    return {"residual_ms": residual, "matched_ms": matched, "edge_index": eidx}


def analog_down(v) -> bool:
    if v is None or v != v:
        return False
    return float(v) >= ANALOG_DOWN


def _duration_ms(t: np.ndarray, mask: np.ndarray, t0: float, t1: float) -> float:
    if t.size == 0:
        return 0.0
    dt = np.empty_like(t)
    dt[0] = 0.0
    if t.size > 1:
        dt[1:] = np.diff(t)
    lo, hi = (t0, t1) if t0 <= t1 else (t1, t0)
    in_range = (t >= lo - 1e-9) & (t <= hi + 1e-9)
    return float(dt[mask & in_range].sum())


def axis_transition(t_ms, left_down, right_down, left: str = "A", right: str = "D"):
    """Last release→opposite-onset pair on one axis, using real timestamps."""
    t = np.asarray(t_ms, dtype=float)
    L = np.asarray(left_down, dtype=bool)
    R = np.asarray(right_down, dtype=bool)
    if t.size == 0 or (not L.any() and not R.any()):
        return None

    l_fall = falling_edges(L)
    r_fall = falling_edges(R)
    l_rise = rising_edges(L)
    r_rise = rising_edges(R)
    candidates = []

    def consider(own_name, opp_name, fall_idx, opp_rise, own_arr, opp_arr):
        for i in fall_idx:
            i = int(i)
            t_rel = float(t[i])
            j = None
            for k in opp_rise:
                k = int(k)
                if t[k] + 1e-9 >= t_rel:
                    j = k
                    break
            if j is None and bool(opp_arr[i]):
                earlier = [int(k) for k in opp_rise if t[int(k)] <= t_rel + 1e-9]
                j = earlier[-1] if earlier else i
            if j is None:
                continue
            t_on = float(t[j])
            t0, t1 = (t_rel, t_on) if t_rel <= t_on else (t_on, t_rel)
            candidates.append({
                "own": own_name,
                "opp": opp_name,
                "gap_ms": t_on - t_rel,
                "overlap_ms": _duration_ms(t, own_arr & opp_arr, t0, t1),
                "release_ms": t_rel,
                "onset_ms": t_on,
            })

    consider(left, right, l_fall, r_rise, L, R)
    consider(right, left, r_fall, l_rise, R, L)

    if not candidates:
        last_L = int(np.where(L)[0][-1]) if L.any() else -1
        last_R = int(np.where(R)[0][-1]) if R.any() else -1
        if last_L < 0 and last_R < 0:
            return None
        own = left if last_L >= last_R else right
        own_arr = L if own == left else R
        opp_arr = R if own == left else L
        return {
            "own": own,
            "opp": None,
            "gap_ms": None,
            "overlap_ms": 0.0,
            "own_down_at_end": bool(own_arr[-1]),
            "opp_down_at_end": bool(opp_arr[-1]),
            "own_ever": bool(own_arr.any()),
            "opp_ever": bool(opp_arr.any()),
        }

    cand = max(candidates, key=lambda c: (c["release_ms"], c["onset_ms"]))
    own_arr = L if cand["own"] == left else R
    opp_arr = R if cand["own"] == left else L
    cand["own_down_at_end"] = bool(own_arr[-1])
    cand["opp_down_at_end"] = bool(opp_arr[-1])
    cand["own_ever"] = True
    cand["opp_ever"] = True
    return cand


def key_events(t_ms, analog: dict, mask, analog_thresh: float = None) -> list:
    if analog_thresh is None:
        analog_thresh = ANALOG_DOWN
    t = np.asarray(t_ms, dtype=float)
    mask_arr = np.asarray(mask)
    events = []
    for key, (axis, direction, bit) in KEY_META.items():
        an = np.asarray(analog[key], dtype=float)
        a_dn = an >= analog_thresh
        d_dn = (mask_arr.astype(np.uint16) & bit) != 0
        for kind, idx in (
            ("analog_down", rising_edges(a_dn)),
            ("analog_up", falling_edges(a_dn)),
            ("digital_down", rising_edges(d_dn)),
            ("digital_up", falling_edges(d_dn)),
        ):
            for i in idx:
                i = int(i)
                events.append({
                    "key": key,
                    "axis": axis,
                    "direction": direction,
                    "kind": kind,
                    "sample_index": i,
                    "t_ms": float(t[i]),
                    "analog": float(an[i]),
                    "digital": bool(d_dn[i]),
                })
    events.sort(key=lambda e: (e["t_ms"], e["sample_index"], e["key"], e["kind"]))
    return events


def is_diagonal_counter(ad_tr, ws_tr) -> bool:
    if not ad_tr or not ws_tr:
        return False
    if not ad_tr.get("opp") or not ws_tr.get("opp"):
        return False
    own = frozenset({ad_tr["own"], ws_tr["own"]})
    opp = frozenset({ad_tr["opp"], ws_tr["opp"]})
    return OPPOSITE_DIAG.get(own) == opp


def _quality(gap_ms, overlap_ms) -> str:
    if overlap_ms is not None and overlap_ms == overlap_ms and float(overlap_ms) > OVERLAP_MS:
        return "OVERLAP"
    if gap_ms is not None and gap_ms == gap_ms and float(gap_ms) > DELAYED_GAP_MS:
        return "DELAYED"
    return "CLEAN"


def _has_pair(tr) -> bool:
    return bool(tr) and tr.get("opp") is not None and tr.get("gap_ms") is not None


def classify_input(
    *,
    map_name: str,
    residual_ms,
    speed: float,
    w_max,
    a_max,
    s_max,
    d_max,
    sync_gate_ms: float = SYNC_GATE_MS,
    ad_transition=None,
    ws_transition=None,
) -> dict:
    ws = analog_down(w_max) or analog_down(s_max)
    ad = analog_down(a_max) or analog_down(d_max)
    if ws_transition:
        ws = ws or bool(ws_transition.get("own_ever"))
    if ad_transition:
        ad = ad or bool(ad_transition.get("own_ever"))

    def out(cls, sync_ok, quality=None):
        return {
            "sync_ok": sync_ok,
            "ws_involved": ws,
            "ad_involved": ad,
            "input_class": cls,
            "counter_quality": quality,
        }

    if map_name == "de_cache":
        return out("SKIP_CACHE", False)

    if residual_ms is None or residual_ms != residual_ms or abs(float(residual_ms)) > sync_gate_ms:
        return out("SYNC_UNCERTAIN", False)

    ad_pair = _has_pair(ad_transition)
    ws_pair = _has_pair(ws_transition)

    if ad_pair and ws_pair and is_diagonal_counter(ad_transition, ws_transition):
        q = _quality(ad_transition.get("gap_ms"), ad_transition.get("overlap_ms"))
        return out("DIAGONAL_COUNTER", True, q)

    if ad_pair:
        g = ad_transition.get("gap_ms")
        o = ad_transition.get("overlap_ms")
        return out("LATERAL_COUNTER", True, _quality(g, o))

    if ws_pair and not ad:
        g = ws_transition.get("gap_ms")
        o = ws_transition.get("overlap_ms")
        return out("FORWARD_BACK_COUNTER", True, _quality(g, o))

    if ad_transition and ad_transition.get("own_down_at_end") and not ad_transition.get("opp"):
        return out("LATERAL_HELD_THROUGH_SHOT", True)

    if ad and ws:
        return out("MIXED_AXIS_UNRESOLVED", True)
    if ad:
        return out("LATERAL_RELEASE_WITHOUT_OPPOSITE", True)
    if ws:
        return out("FORWARD_BACK_COUNTER", True) if ws_pair else out("MIXED_AXIS_UNRESOLVED", True)
    if speed == speed and float(speed) < STATIONARY_SPEED:
        return out("STATIONARY_NO_RECENT_MOVEMENT", True)
    return out("TRANSITION_AMBIGUOUS", True)


def group_bursts(times_s, gap_s: float = BURST_GAP_S) -> np.ndarray:
    times = np.sort(np.asarray(times_s, dtype=float))
    if times.size == 0:
        return times
    bursts = [float(times[0])]
    last = float(times[0])
    for t in times[1:]:
        t = float(t)
        if t - last > gap_s:
            bursts.append(t)
        last = t
    return np.asarray(bursts, dtype=float)


def score_offset(demo_b, csi_b, offset_s: float, window_s: float = BURST_MATCH_WINDOW_S):
    demo_b = np.asarray(demo_b, dtype=float)
    csi_b = np.asarray(csi_b, dtype=float)
    j = 0
    n = 0
    residuals = []
    for d in demo_b:
        pred = float(d) + offset_s
        while j < csi_b.size and csi_b[j] < pred - window_s:
            j += 1
        best = None
        k = j
        while k < csi_b.size and csi_b[k] <= pred + window_s:
            r = float(csi_b[k] - pred)
            if best is None or abs(r) < abs(best[0]):
                best = (r, k)
            k += 1
        if best is not None:
            n += 1
            residuals.append(best[0])
            j = best[1] + 1
    return n, residuals


def search_offset(
    demo_b,
    csi_b,
    t_lo=None,
    t_hi=None,
    coarse_s: float = 0.25,
    window_s: float = BURST_MATCH_WINDOW_S,
    allowed_fn: Optional[Callable[[float], bool]] = None,
) -> dict:
    demo_b = np.sort(np.asarray(demo_b, dtype=float))
    csi_b = np.sort(np.asarray(csi_b, dtype=float))
    empty = {
        "offset_s": 0.0,
        "matched": 0,
        "total_bursts": int(demo_b.size),
        "median_residual_ms": None,
        "residual_std_ms": None,
        "residual_p10_ms": None,
        "residual_p90_ms": None,
        "model": "offset_only",
    }
    if demo_b.size == 0 or csi_b.size == 0:
        return empty
    if t_lo is None:
        t_lo = float(csi_b[0] - demo_b[-1] - 1.0)
    if t_hi is None:
        t_hi = float(csi_b[-1] - demo_b[0] + 1.0)

    def allowed(off: float) -> bool:
        return True if allowed_fn is None else bool(allowed_fn(off))

    def consider(cand: float):
        nonlocal best
        if not allowed(cand):
            return
        n, res = score_offset(demo_b, csi_b, cand, window_s)
        std = float(np.std(res)) if res else 1e9
        med = abs(float(np.median(res))) if res else 1e9
        key = (n, -std, -med)
        if best is None or key > best[0]:
            best = (key, cand, res)

    best = None
    votes = np.concatenate([csi_b - d for d in demo_b])
    votes = votes[(votes >= t_lo) & (votes <= t_hi)]
    if votes.size and t_hi > t_lo:
        bins = np.arange(t_lo, t_hi + coarse_s, coarse_s)
        if bins.size >= 2:
            hist, edges = np.histogram(votes, bins=bins)
            for idx in np.argsort(hist)[::-1][:40]:
                if hist[idx] <= 0:
                    break
                consider(float(0.5 * (edges[idx] + edges[idx + 1])))

    grid = 1.0
    off = float(np.floor(t_lo))
    while off <= t_hi:
        consider(off)
        off += grid

    if best is None:
        n, cand, res = 0, 0.0, []
    else:
        cand, res = best[1], best[2]
        n = int(best[0][0])
    if res:
        refined = float(cand + float(np.median(res)))
        if allowed(refined):
            n2, res2 = score_offset(demo_b, csi_b, refined, window_s)
            if n2 >= n:
                n, cand, res = n2, refined, res2

    resid = np.asarray(res, dtype=float)
    out = {
        "offset_s": float(cand),
        "matched": int(n),
        "total_bursts": int(demo_b.size),
        "model": "offset_only",
        "median_residual_ms": None,
        "residual_std_ms": None,
        "residual_p10_ms": None,
        "residual_p90_ms": None,
    }
    if resid.size:
        out["median_residual_ms"] = float(np.median(resid) * 1000.0)
        out["residual_std_ms"] = float(np.std(resid) * 1000.0)
        out["residual_p10_ms"] = float(np.quantile(resid, 0.10) * 1000.0)
        out["residual_p90_ms"] = float(np.quantile(resid, 0.90) * 1000.0)
    return out


def align_demos(demos: list, csi_b, margin_s: float = 30.0) -> list:
    """Place each demo independently; session occupancy does not overlap."""
    csi_b = np.sort(np.asarray(csi_b, dtype=float))
    occupied: list[tuple[float, float]] = []
    order = sorted(range(len(demos)), key=lambda i: -len(np.asarray(demos[i]["bursts"])))
    results = [None] * len(demos)
    for i in order:
        d = demos[i]
        bursts = np.sort(np.asarray(d["bursts"], dtype=float))
        tmin = float(d.get("tmin", bursts[0] if bursts.size else 0.0))
        tmax = float(d.get("tmax", bursts[-1] if bursts.size else 0.0))

        def allowed(off, _tmin=tmin, _tmax=tmax):
            lo = off + _tmin - margin_s
            hi = off + _tmax + margin_s
            return all(hi <= a or lo >= b for a, b in occupied)

        found = search_offset(bursts, csi_b, allowed_fn=allowed)
        off = found["offset_s"]
        occupied.append((off + tmin - margin_s, off + tmax + margin_s))
        rec = dict(found)
        rec["id"] = d.get("id")
        rec["map"] = d.get("map")
        results[i] = rec
    return results


def fit_offset_s(t_demo_s: np.ndarray, t_csi_s: np.ndarray) -> float:
    return float(np.median(np.asarray(t_csi_s, dtype=float) - np.asarray(t_demo_s, dtype=float)))


def fit_slope_offset(t_demo_s: np.ndarray, t_csi_s: np.ndarray) -> tuple[float, float]:
    x = np.asarray(t_demo_s, dtype=float)
    y = np.asarray(t_csi_s, dtype=float)
    if x.size < 2:
        b = fit_offset_s(x, y) if x.size else 0.0
        return 1.0, b
    a, b = np.polyfit(x, y, 1)
    return float(a), float(b)


def summarize_shots(df) -> dict:
    live_mask = ~df["in_warmup"].astype(bool) & ~df["in_freeze"].astype(bool)
    live = df.loc[live_mask]
    fb = live.loc[live["is_first_bullet"].astype(bool)]
    rifles = fb.loc[fb["is_headline_rifle"].astype(bool)] if len(fb) else fb
    if len(rifles):
        duck = rifles["duck_at_fire"].astype(float) if "duck_at_fire" in rifles.columns else 0.0
        air = rifles["is_airborne"].astype(bool) if "is_airborne" in rifles.columns else False
        standing = rifles.loc[~air & (duck.fillna(0.0) < DUCK_STANDING_MAX)]
        crouched = rifles.loc[~air & (duck.fillna(0.0) >= DUCK_CROUCHED_MIN)]
    else:
        standing = rifles
        crouched = rifles

    def n_acc(g) -> int:
        if g is None or len(g) == 0 or "rifle_accurate" not in g.columns:
            return 0
        return int((g["rifle_accurate"] == True).sum())  # noqa: E712

    n_mouse = int(fb["mouse1_ms"].notna().sum()) if "mouse1_ms" in fb.columns else 0
    n_sync = int(fb["sync_ok"].astype(bool).sum()) if "sync_ok" in fb.columns else 0
    return {
        "shots": int(len(df)),
        "live": int(len(live)),
        "first_bullets": int(len(fb)),
        "rifles": int(len(rifles)),
        "rifles_accurate": n_acc(rifles),
        "standing_rifles": int(len(standing)),
        "standing_accurate": n_acc(standing),
        "crouched_rifles": int(len(crouched)),
        "crouched_accurate": n_acc(crouched),
        "sync_ok": n_sync,
        "mouse1_candidates": n_mouse,
    }
