"""Phase 3 shot extraction: 1-tick speed, native-velocity check, CSI windows.

Dense contiguous parse_ticks per demo (sparse fire-tick lists make
demoparser2 return NaN/garbage velocity). Speed source is 1-tick
horizontal posdiff; native velocity at the exact fire tick is a check
column only (never ffilled over posdiff).

Input-cause uses one-to-one monotonic Mouse1 matching and timestamped
WASD events. Classification residual is the existing-alignment residual,
never a post-hoc refit.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from demoparser2 import DemoParser
from pathlib import PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A
import csiutil

USER = "76561198158590364"
REPLAYS = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays"
CSI = r"C:\Users\lorem\Desktop\strafes\phase2\2026-09-01_135047.csi"
ALIGN_PATH = r"C:\Users\lorem\Desktop\strafes\phase2\alignment.json"
OUT = r"C:\Users\lorem\Desktop\strafes\phase2"
DEMO_GLOB = "match730_0038402*.dem"

GUNS = set(A.WEAPON_MAX_SPEED.keys())
# weapon_fire names that are expected but intentionally not analysed
# (grenades/knife/nade-like). Anything else unrecognised is reported.
NON_GUN_PREFIXES = ("weapon_flashbang", "weapon_hegrenade", "weapon_molotov",
                    "weapon_incgrenade", "weapon_decoy", "weapon_smokegrenade",
                    "weapon_knife", "weapon_c4", "weapon_taser")
BIT_W, BIT_A, BIT_S, BIT_D, BIT_M1 = 0x01, 0x02, 0x04, 0x08, 0x80
LEAD = 4
WINDOW_HALF = 16
PRE_MS, POST_MS = 400.0, 50.0
TICK_PROPS = [
    "X", "Y", "velocity_X", "velocity_Y",
    "shots_fired", "duck_amount", "is_scoped", "is_airborne",
    "is_warmup_period", "is_freeze_period",
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Extract and classify first-bullet shots")
    p.add_argument("--steamid", default=USER)
    p.add_argument("--csi", default=CSI)
    p.add_argument("--replays", default=REPLAYS)
    p.add_argument("--demo", action="append", default=[])
    p.add_argument("--demo-glob", default=DEMO_GLOB)
    p.add_argument("--alignment", default=ALIGN_PATH)
    p.add_argument("--output-dir", default=OUT)
    p.add_argument("--session-id", default=None)
    p.add_argument("--report-only", action="store_true")
    p.add_argument("--reclassify", action="store_true",
                   help="Recompute matching/classes from shots.parquet + CSI; no demo parse")
    return p.parse_args(argv)


def resolve_demos(args) -> list[str]:
    if args.demo:
        return [os.path.abspath(d) for d in args.demo]
    return sorted(glob.glob(os.path.join(args.replays, args.demo_glob)))


def load_alignment(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def offset_for(align: dict, map_name: str, demo_name: str | None = None) -> float:
    matches = align.get("matches")
    if matches:
        if demo_name:
            base = os.path.basename(demo_name)
            for m in matches:
                if os.path.basename(m.get("demo", "")) == base:
                    return float(m["offset_s"])
        hits = [m for m in matches if m.get("map") == map_name]
        if len(hits) == 1:
            return float(hits[0]["offset_s"])
        if len(hits) > 1:
            raise SystemExit(
                f"multiple alignments for {map_name}; pass --demo so each file is unique"
            )
        raise SystemExit(f"no alignment for {map_name}")
    if map_name in align:
        return float(align[map_name]["offset_s"])
    by_map = align.get("by_map", {})
    if map_name in by_map:
        return float(by_map[map_name]["offset_s"])
    raise SystemExit(f"no alignment for {map_name}")


def offset_by_demo(align: dict) -> dict[str, float]:
    """Map demo basename -> offset (schema 2 key).

    Handles same-map collision the way align.py intended: each demo file
    gets its own offset. PureWindowsPath so forward- and back-slash demo
    paths both resolve to their basename.
    """
    matches = align.get("matches")
    if not matches:
        return {}
    out: dict[str, float] = {}
    for m in matches:
        base = PureWindowsPath(m.get("demo", "")).name
        if not base:
            continue
        if base in out and abs(out[base] - float(m["offset_s"])) > 1e-6:
            raise SystemExit(f"two alignments for demo {base} with different offsets")
        out[base] = float(m["offset_s"])
    return out


def _col(df, name):
    return name if name in df.columns else None


def parse_user_ticks(parser: DemoParser, lo: int, hi: int, steamid: str) -> pd.DataFrame:
    ticks = list(range(max(0, lo), hi))
    user_int = int(steamid)
    try:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks, players=[user_int])
    except TypeError:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks)
    if pos is None or len(pos) == 0:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks)
    if "steamid" in pos.columns:
        pos = pos[pos.steamid.astype(str) == steamid].copy()
    return pos.sort_values("tick").reset_index(drop=True)


def lookup_row(by_tick: dict, tick: int):
    return by_tick.get(int(tick))


def ffill_shots(by_tick: dict, ft: int, back: int = 3):
    vals = []
    for t in range(ft - back, ft + 1):
        r = by_tick.get(t)
        if r is None:
            vals.append(np.nan)
            continue
        v = r.get("shots_fired", np.nan)
        vals.append(float(v) if v is not None else np.nan)
    filled = A.ffill_numeric(np.array(vals, dtype=float))
    return filled[-1]


def extract_demo(demo: str, offset_s: float, steamid: str, session_id: str) -> list[dict]:
    p = DemoParser(demo)
    map_name = p.parse_header()["map_name"]
    ev = p.parse_event("weapon_fire")
    mine = ev[ev.user_steamid.astype(str) == steamid].copy()
    unknown = sorted(set(mine.weapon) - GUNS - {w for w in mine.weapon if w.startswith(NON_GUN_PREFIXES)})
    if unknown:
        print(f"  {map_name}: WARNING unrecognised weapons (not in WEAPON_MAX_SPEED, not filtered): {unknown}")
    guns = mine[mine.weapon.isin(GUNS)].sort_values("tick").reset_index(drop=True)
    if not len(guns):
        print(f"  {map_name}: no gun fires")
        return []
    fire_ticks = [int(t) for t in guns.tick.tolist()]
    lo = min(fire_ticks) - WINDOW_HALF - LEAD
    hi = max(fire_ticks) + WINDOW_HALF + 2
    print(f"  {map_name}: {len(fire_ticks)} fires, ticks [{lo}, {hi}) contiguous")
    pos = parse_user_ticks(p, lo, hi, steamid)
    print(f"    user tick rows {len(pos)} cols {list(pos.columns)}")
    by_tick = {int(r.tick): r.to_dict() for _, r in pos.iterrows()}
    demo_name = os.path.basename(demo)

    rows = []
    prev_fire = None
    n_missing_ticks = 0
    for _, g in guns.iterrows():
        ft = int(g.tick)
        rec = lookup_row(by_tick, ft)
        prev = lookup_row(by_tick, ft - 1)
        r16a = lookup_row(by_tick, ft - WINDOW_HALF)
        r16b = lookup_row(by_tick, ft + WINDOW_HALF)

        if rec is None or prev is None:
            spd = float("nan")
            n_missing_ticks += 1
            print(f"  {map_name}: WARNING tick {ft} missing player row; speed will be NaN")
        else:
            spd = A.horizontal_speed(prev["X"], prev["Y"], rec["X"], rec["Y"], 1)

        native = float("nan")
        if rec is not None and _col(pos, "velocity_X") and rec.get("velocity_X") == rec.get("velocity_X"):
            vx, vy = rec.get("velocity_X"), rec.get("velocity_Y")
            if vy == vy:
                native = float(np.hypot(float(vx), float(vy)))

        if r16a is not None and r16b is not None:
            win = A.horizontal_speed(r16a["X"], r16a["Y"], r16b["X"], r16b["Y"], 2 * WINDOW_HALF)
        else:
            win = float("nan")

        sf = ffill_shots(by_tick, ft)
        first = A.is_first_bullet(sf)
        first_old = prev_fire is None or (ft - prev_fire) > 64
        prev_fire = ft

        scoped = bool(rec.get("is_scoped", False)) if rec else False
        duck = rec.get("duck_amount") if rec else None
        air = rec.get("is_airborne", False) if rec else False
        warmup = bool(rec.get("is_warmup_period", False)) if rec else False
        freeze = bool(rec.get("is_freeze_period", False)) if rec else False
        posture = A.posture_label(air, duck)

        wmax = A.max_speed(g.weapon, scoped)
        thresh = A.accuracy_threshold(g.weapon, scoped)
        ratio = (spd / wmax) if (wmax and spd == spd) else float("nan")
        margin = (thresh - spd) if (thresh is not None and spd == spd) else float("nan")
        acc = A.rifle_accurate(g.weapon, spd, scoped)
        disagree = A.speed_disagreement(spd, native)
        local_ms = (ft / A.TICKRATE + offset_s) * 1000.0

        rows.append({
            "session_id": session_id,
            "steamid": str(steamid),
            "demo": demo_name,
            "match_id": Path(demo_name).stem,
            "map": map_name,
            "tick": ft,
            "weapon": g.weapon,
            "speed_at_fire": None if spd != spd else round(float(spd), 1),
            "speed_native_check": None if native != native else round(float(native), 1),
            "speed_at_fire_window": None if win != win else round(float(win), 1),
            "speed_disagree": bool(disagree),
            "measurement_uncertain": bool(disagree),
            "shots_fired": None if sf != sf else int(sf),
            "is_first_bullet": first,
            "is_first_bullet_old": bool(first_old),
            "is_scoped": scoped,
            "duck_at_fire": None if duck is None or duck != duck else round(float(duck), 3),
            "is_airborne": bool(air),
            "posture": posture,
            "in_warmup": warmup,
            "in_freeze": freeze,
            "weapon_max_speed": wmax,
            "accuracy_threshold": None if thresh is None else round(float(thresh), 1),
            "speed_ratio": None if ratio != ratio else round(float(ratio), 3),
            "margin_to_threshold": None if margin != margin else round(float(margin), 1),
            "rifle_accurate": acc,
            "is_headline_rifle": A.is_headline_rifle(g.weapon),
            "local_ms": round(float(local_ms), 1),
            "alignment_offset_s": float(offset_s),
        })
    if n_missing_ticks:
        print(f"  {map_name}: {n_missing_ticks} shots missing tick rows (speed NaN, flagged)")
    return rows


def _window(t_ms, body, center_ms: float):
    lo_i = int(np.searchsorted(t_ms, center_ms - PRE_MS))
    hi_i = int(np.searchsorted(t_ms, center_ms + POST_MS))
    if hi_i <= lo_i:
        return None
    sl = slice(lo_i, hi_i)
    return {
        "t": t_ms[sl],
        "w": body["w"][sl],
        "a": body["a"][sl],
        "s": body["s"][sl],
        "d": body["d"][sl],
        "mask": body["mask"][sl],
    }


def apply_input_analysis(df: pd.DataFrame, t_ms, body, offsets: dict[str, float]) -> pd.DataFrame:
    """Monotonic Mouse1 match + timestamped WASD events. Mutates a copy."""
    df = df.copy()
    edge_ms = csiutil.mouse1_edge_ms(t_ms, body)
    n = len(df)
    resid_exist = np.full(n, np.nan)
    mouse1 = np.full(n, np.nan)
    edge_index = np.full(n, np.nan)
    cls = np.array(["SYNC_UNCERTAIN"] * n, dtype=object)
    quality = np.array([None] * n, dtype=object)
    sync_ok = np.zeros(n, dtype=bool)
    ws_inv = np.zeros(n, dtype=bool)
    ad_inv = np.zeros(n, dtype=bool)
    gap_col = np.full(n, np.nan)
    overlap_col = np.full(n, np.nan)
    w_max = np.zeros(n, dtype=np.int64)
    a_max = np.zeros(n, dtype=np.int64)
    s_max = np.zeros(n, dtype=np.int64)
    d_max = np.zeros(n, dtype=np.int64)

    pos = {idx: i for i, idx in enumerate(df.index)}

    for demo_name, g in df.groupby("demo"):
        base = PureWindowsPath(str(demo_name)).name
        if base not in offsets:
            continue
        off = offsets[base]
        fb = g[g.is_first_bullet & ~g.in_warmup & ~g.in_freeze]
        if not len(fb):
            continue
        pred = (fb["tick"].to_numpy(dtype=float) / A.TICKRATE + off) * 1000.0
        matched = A.match_monotonic(pred, edge_ms)
        for i, idx in enumerate(fb.index):
            j = pos[idx]
            r = matched["residual_ms"][i]
            m = matched["matched_ms"][i]
            e = matched["edge_index"][i]
            resid_exist[j] = np.nan if r is None else float(r)
            mouse1[j] = np.nan if m is None else float(m)
            edge_index[j] = np.nan if e is None else int(e)

    for i, row in enumerate(df.itertuples(index=False)):
        if not bool(row.is_first_bullet):
            continue
        center = mouse1[i] if mouse1[i] == mouse1[i] else float(row.local_ms)
        win = _window(t_ms, body, center)
        if win is None:
            continue
        w_max[i] = int(win["w"].max())
        a_max[i] = int(win["a"].max())
        s_max[i] = int(win["s"].max())
        d_max[i] = int(win["d"].max())
        a_down = win["a"] >= A.ANALOG_DOWN
        d_down = win["d"] >= A.ANALOG_DOWN
        w_down = win["w"] >= A.ANALOG_DOWN
        s_down = win["s"] >= A.ANALOG_DOWN
        ad_tr = A.axis_transition(win["t"], a_down, d_down, left="A", right="D")
        ws_tr = A.axis_transition(win["t"], w_down, s_down, left="W", right="S")
        if ad_tr and ad_tr.get("gap_ms") is not None:
            gap_col[i] = float(ad_tr["gap_ms"])
            overlap_col[i] = float(ad_tr.get("overlap_ms") or 0.0)
        resid = resid_exist[i]
        resid_arg = None if resid != resid else float(resid)
        result = A.classify_input(
            map_name=row.map,
            residual_ms=resid_arg,
            speed=float(row.speed_at_fire) if row.speed_at_fire == row.speed_at_fire else 0.0,
            w_max=int(w_max[i]), a_max=int(a_max[i]), s_max=int(s_max[i]), d_max=int(d_max[i]),
            ad_transition=ad_tr,
            ws_transition=ws_tr,
        )
        cls[i] = result["input_class"]
        quality[i] = result.get("counter_quality")
        sync_ok[i] = bool(result["sync_ok"])
        ws_inv[i] = bool(result["ws_involved"])
        ad_inv[i] = bool(result["ad_involved"])

    df["residual_existing_alignment_ms"] = np.round(resid_exist, 1)
    df["classification_residual_ms"] = df["residual_existing_alignment_ms"]
    df["residual_ms"] = df["classification_residual_ms"]
    df["classification_alignment_model"] = "offset_only"
    df["mouse1_ms"] = np.round(mouse1, 1)
    df["mouse1_edge_index"] = edge_index
    df["w_max_pre"] = w_max
    df["a_max_pre"] = a_max
    df["s_max_pre"] = s_max
    df["d_max_pre"] = d_max
    df["gap_ms"] = np.round(gap_col, 1)
    df["overlap_ms"] = np.round(overlap_col, 1)
    df["sync_ok"] = sync_ok
    df["ws_involved"] = ws_inv
    df["ad_involved"] = ad_inv
    df["input_class"] = cls
    df["counter_quality"] = quality
    if "posture" not in df.columns:
        df["posture"] = [
            A.posture_label(a, d) for a, d in zip(df.get("is_airborne", False), df.get("duck_at_fire", 0))
        ]
    if "speed_disagree" not in df.columns:
        df["speed_disagree"] = [
            A.speed_disagreement(p, n)
            for p, n in zip(df["speed_at_fire"], df["speed_native_check"])
        ]
        df["measurement_uncertain"] = df["speed_disagree"]
    return df


def attach_slope(df: pd.DataFrame) -> dict:
    """Refit comparison only. Does not replace classification residual."""
    stats = {}
    df["residual_refit_offset_ms"] = np.nan
    df["residual_refit_affine_ms"] = np.nan
    df["residual_slope_ms"] = np.nan
    df["t_demo_s"] = df["tick"] / A.TICKRATE
    df["t_csi_s"] = df["mouse1_ms"] / 1000.0
    for map_name, g in df.groupby("map"):
        use = g[g.is_first_bullet & g.mouse1_ms.notna() & ~g.in_warmup & ~g.in_freeze]
        if len(use) < 3:
            stats[map_name] = {"n": int(len(use)), "note": "too few pairs"}
            continue
        t_demo = use["t_demo_s"].to_numpy()
        t_csi = use["t_csi_s"].to_numpy()
        off = A.fit_offset_s(t_demo, t_csi)
        a, b = A.fit_slope_offset(t_demo, t_csi)
        resid_off = (t_csi - (t_demo + off)) * 1000.0
        resid_ab = (t_csi - (a * t_demo + b)) * 1000.0
        ppm = (a - 1.0) * 1e6
        cls_resid = use["classification_residual_ms"].to_numpy(dtype=float) if "classification_residual_ms" in use.columns else use["residual_ms"].to_numpy(dtype=float)
        cls_ok = np.isfinite(cls_resid) & (np.abs(cls_resid) <= A.SYNC_GATE_MS)
        stats[map_name] = {
            "n": int(len(use)),
            "offset_s": round(float(off), 4),
            "a": round(float(a), 8),
            "b_s": round(float(b), 4),
            "ppm": round(float(ppm), 1),
            "offset_resid_std_ms": round(float(np.std(resid_off)), 1),
            "offset_resid_median_ms": round(float(np.median(resid_off)), 1),
            "slope_resid_std_ms": round(float(np.std(resid_ab)), 1),
            "slope_resid_median_ms": round(float(np.median(resid_ab)), 1),
            "frac_sync_ok_offset": round(float((np.abs(resid_off) <= A.SYNC_GATE_MS).mean()), 3),
            "frac_sync_ok_slope": round(float((np.abs(resid_ab) <= A.SYNC_GATE_MS).mean()), 3),
            "frac_sync_ok_classification": round(float(cls_ok.mean()), 3) if cls_resid.size else None,
        }
        idx = use.index
        df.loc[idx, "residual_refit_offset_ms"] = np.round(resid_off, 1)
        df.loc[idx, "residual_refit_affine_ms"] = np.round(resid_ab, 1)
        df.loc[idx, "residual_slope_ms"] = np.round(resid_ab, 1)
    return stats


def old_vs_new(df: pd.DataFrame) -> str:
    lines = ["## Old vs new (this rerun)", ""]
    live = df[~df.in_warmup & ~df.in_freeze]
    lines.append(f"- Gun shots extracted: {len(df)} (live {len(live)}, warmup/freeze {len(df)-len(live)})")
    lines.append(
        f"- First bullets: old 1s-gap {int(live.is_first_bullet_old.sum())} → "
        f"shots_fired==1 {int(live.is_first_bullet.sum())}"
    )
    both = live.dropna(subset=["speed_at_fire", "speed_at_fire_window"])
    if len(both):
        d = (both.speed_at_fire - both.speed_at_fire_window).abs()
        lines.append(
            f"- Speed 1-tick vs ±16 window: n={len(both)} corr={both.speed_at_fire.corr(both.speed_at_fire_window):.3f} "
            f"median |diff|={d.median():.1f} u/s p90={d.quantile(0.9):.1f}"
        )
    nat = live.dropna(subset=["speed_at_fire", "speed_native_check"])
    if len(nat):
        d = (nat.speed_at_fire - nat.speed_native_check).abs()
        lines.append(
            f"- Speed 1-tick vs native-at-tick: n={len(nat)} corr={nat.speed_at_fire.corr(nat.speed_native_check):.3f} "
            f"median |diff|={d.median():.1f} disagree>30={int((d > 30).sum())}"
        )
    fb = live[live.is_first_bullet]
    rifles = fb[fb.is_headline_rifle]
    if len(rifles):
        old_acc = (rifles.speed_at_fire_window < 130).mean()
        new_acc = rifles.rifle_accurate.mean()
        lines.append(
            f"- Rifle first-bullet 'accurate': ±16 window <130 = {old_acc:.1%} → "
            f"1-tick < 34% max = {new_acc:.1%} (n={len(rifles)})"
        )
        lines.append(
            f"- Rifle first-bullet median speed: window {rifles.speed_at_fire_window.median():.1f} → "
            f"1-tick {rifles.speed_at_fire.median():.1f} u/s"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(df: pd.DataFrame, slope_stats: dict, out_dir: str, csi_name: str):
    live = df[~df.in_warmup & ~df.in_freeze]
    fb = live[live.is_first_bullet]
    rifles = fb[fb.is_headline_rifle]
    s = A.summarize_shots(df)
    standing = rifles[(~rifles.is_airborne) & (rifles.duck_at_fire.fillna(0) < A.DUCK_STANDING_MAX)]
    crouched = rifles[(~rifles.is_airborne) & (rifles.duck_at_fire.fillna(0) >= A.DUCK_CROUCHED_MIN)]
    lines = []
    lines.append("# Phase 3 — corrected first-bullet analysis")
    lines.append("")
    lines.append("Speed = 1-tick horizontal posdiff. Native velocity is a check column.")
    lines.append("All-posture rifle accuracy = speed < 34% of weapon max.")
    lines.append("Primary counter-strafe headline = standing, on-ground rifles only.")
    lines.append("Input-cause gated on |classification residual| ≤ 30 ms (existing-alignment, monotonic match).")
    lines.append("Refit offset/affine residuals are comparison-only and are not used for classes.")
    lines.append("Cache excluded from input-cause. Do not compare these numbers to the previous 86.4% / 50% RELEASE_ONLY headlines.")
    lines.append("")
    lines.append("## Dataset")
    lines.append(f"- Session CSI: `{csi_name}`")
    maps = sorted(df["map"].astype(str).unique())
    lines.append(f"- Gun shots: {s['shots']} across {', '.join(maps)}")
    lines.append(f"- Live (not warmup/freeze): {s['live']}")
    lines.append(
        f"- First bullets (`shots_fired==1`): {s['first_bullets']} "
        f"(old 1s-gap rule would have kept {int(live.is_first_bullet_old.sum())})"
    )
    lines.append(f"- Headline rifles among first bullets: {s['rifles']}")
    lines.append("")
    lines.append(old_vs_new(df))
    lines.append("## Rifle first-bullet accuracy")
    if s["rifles"]:
        lines.append(
            f"- All-posture (any duck/air): **{s['rifles_accurate']}/{s['rifles']} = "
            f"{s['rifles_accurate']/s['rifles']:.1%}**"
        )
        lines.append(
            f"- Standing, on-ground (primary): **{s['standing_accurate']}/{s['standing_rifles']} = "
            f"{s['standing_accurate']/s['standing_rifles']:.1%}**"
        )
        if s["crouched_rifles"]:
            lines.append(
                f"- Fully crouched: **{s['crouched_accurate']}/{s['crouched_rifles']} = "
                f"{s['crouched_accurate']/s['crouched_rifles']:.1%}**"
            )
        lines.append(f"- Median speed (all-posture) {rifles.speed_at_fire.median():.1f} u/s; p90 {rifles.speed_at_fire.quantile(0.9):.1f}")
        if len(standing):
            lines.append(
                f"- Median speed (standing) {standing.speed_at_fire.median():.1f} u/s; "
                f"p90 {standing.speed_at_fire.quantile(0.9):.1f}"
            )
        lines.append("")
        lines.append("| weapon | n | threshold | median speed | accurate |")
        lines.append("|---|---:|---:|---:|---:|")
        for w, g in rifles.groupby("weapon"):
            th = g.accuracy_threshold.iloc[0]
            lines.append(
                f"| {w} | {len(g)} | {th} | {g.speed_at_fire.median():.1f} | "
                f"{(g.rifle_accurate==True).mean():.0%} |"  # noqa: E712
            )
    else:
        lines.append("- none")
    lines.append("")
    n_disc = int(live.get("speed_disagree", pd.Series(False, index=live.index)).sum()) if "speed_disagree" in live.columns else int(
        (live.speed_at_fire - live.speed_native_check).abs().fillna(0).gt(30).sum()
    )
    lines.append("## Native-velocity check")
    lines.append(f"- Shots with |posdiff − native| > 30 u/s: {n_disc} (kept on posdiff; flagged `measurement_uncertain`).")
    lines.append("- Native is not promoted. Disagreements are typically native reading high.")
    if "speed_disagree" in live.columns:
        disc = live[live.speed_disagree]
        if len(disc):
            lines.append("")
            lines.append("| map | tick | weapon | posdiff | native | first |")
            lines.append("|---|---:|---|---:|---:|---|")
            for _, r in disc.sort_values(["map", "tick"]).iterrows():
                lines.append(
                    f"| {r['map']} | {int(r.tick)} | {r.weapon} | {r.speed_at_fire} | "
                    f"{r.speed_native_check} | {bool(r.is_first_bullet)} |"
                )
    lines.append("")
    lines.append("## Sync (first bullets, live)")
    r = fb["classification_residual_ms"].dropna() if "classification_residual_ms" in fb.columns else fb["residual_ms"].dropna()
    n_cand = int(fb.mouse1_ms.notna().sum())
    n_gate = int((r.abs() <= A.SYNC_GATE_MS).sum()) if len(r) else 0
    n_ok = int(fb.sync_ok.sum())
    if s["first_bullets"]:
        lines.append(f"- Mouse1 candidates (monotonic match within 400 ms): {n_cand}/{s['first_bullets']}")
        lines.append(f"- Inside ±30 ms gate: {n_gate}/{n_cand if n_cand else 0} candidates")
        lines.append(f"- Trusted input-cause after Cache skip: {n_ok}/{s['first_bullets']}")
    if len(r):
        lines.append(
            f"- Classification residual (existing alignment): n={len(r)} median {r.median():.1f} ms  "
            f"std {r.std():.1f} ms  |resid|≤30 ms {(r.abs()<=30).mean():.1%}"
        )
    lines.append("")
    lines.append("Refit comparison (not used for classification):")
    lines.append("")
    lines.append("| map | n pairs | classif ≤30 | refit-offset std | refit-affine std | ppm | refit-off ≤30 | refit-aff ≤30 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for m, st in slope_stats.items():
        if "ppm" not in st:
            lines.append(f"| {m} | {st.get('n', 0)} | — | — | — | — | — | — |")
            continue
        cls_frac = st.get("frac_sync_ok_classification")
        cls_s = f"{cls_frac:.0%}" if cls_frac is not None else "—"
        lines.append(
            f"| {m} | {st['n']} | {cls_s} | {st['offset_resid_std_ms']} | {st['slope_resid_std_ms']} | "
            f"{st['ppm']} | {st['frac_sync_ok_offset']:.0%} | {st['frac_sync_ok_slope']:.0%} |"
        )
    lines.append("")
    lines.append("## Input-cause classes")
    lines.append("Only first bullets with `|classification_residual_ms| ≤ 30` (cache always `SKIP_CACHE`).")
    lines.append("Classes are positive evidence. `LATERAL_RELEASE_WITHOUT_OPPOSITE` is an analog-down A/D window with no paired opposite onset — not a verified release-only mechanic.")
    lines.append("`MIXED_AXIS_UNRESOLVED` is W/S+A/D activity without a verified opposing-diagonal pair. `DIAGONAL_COUNTER` requires WA→SD / WD→SA / mirrors.")
    lines.append("")
    vc = fb.input_class.value_counts()
    lines.append("| class | n | % of first bullets | median speed |")
    lines.append("|---|---:|---:|---:|")
    for name, n in vc.items():
        med = fb.loc[fb.input_class == name, "speed_at_fire"].median()
        lines.append(f"| {name} | {n} | {n/len(fb):.1%} | {med} |")
    lines.append("")
    ws = fb[fb.ws_involved]
    if len(fb):
        lines.append(f"- W/S involved in window: {len(ws)}/{len(fb)} first bullets ({len(ws)/len(fb):.0%})")
    lines.append(f"- Sync-ok first bullets (input-cause trusted): {n_ok}/{len(fb)}")
    lines.append("")
    ok = fb[fb.sync_ok]
    if len(ok):
        lines.append("Among sync-ok first bullets only:")
        lines.append("")
        lines.append("| class | n | % of sync-ok | median speed |")
        lines.append("|---|---:|---:|---:|")
        for name, n in ok.input_class.value_counts().items():
            med = ok.loc[ok.input_class == name, "speed_at_fire"].median()
            lines.append(f"| {name} | {n} | {n/len(ok):.1%} | {med} |")
        lines.append("")
        lines.append(f"- W/S involved among sync-ok: {int(ok.ws_involved.sum())}/{len(ok)}")
        if "counter_quality" in ok.columns:
            lat = ok[ok.input_class == "LATERAL_COUNTER"]
            if len(lat):
                q = lat.counter_quality.value_counts()
                bits = ", ".join(f"{k} {int(v)}" for k, v in q.items())
                lines.append(f"- LATERAL_COUNTER quality: {bits}")
        lines.append("")
    lines.append("## What this does *not* say")
    lines.append("- Do not train on a '50% release-only' figure. That class no longer exists as a default bin.")
    lines.append("- Standing/on-ground accuracy is the counter-strafe-oriented headline; all-posture mixes in crouch.")
    lines.append("- Trusted input-cause coverage is still a minority of first bullets. Do not generalise class mix to all shots.")
    lines.append("- Initiation-gap values are CSI-internal and only attached to a demo fire when `sync_ok`.")
    lines.append("- Pistols/SMG/AWP are stored but excluded from the rifle headline.")
    lines.append("")
    path = os.path.join(out_dir, "PHASE3_REPORT.md")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def write_outputs(df: pd.DataFrame, slope_stats: dict, out_dir: str, csi_name: str):
    os.makedirs(out_dir, exist_ok=True)
    parquet = os.path.join(out_dir, "shots.parquet")
    csv = os.path.join(out_dir, "first_bullets_classified.csv")
    df.to_parquet(parquet, index=False)
    fb = df[df.is_first_bullet].copy()
    fb.to_csv(csv, index=False)
    print(f"{len(df)} shots → {parquet}")
    print(f"{len(fb)} first bullets → {csv}")
    write_report(df, slope_stats, out_dir, csi_name)


def run_extract(args) -> pd.DataFrame:
    align = load_alignment(args.alignment)
    session_id = args.session_id or align.get("session_id") or csiutil.session_id_from_csi(args.csi)
    print("loading CSI…")
    t_ms, body = csiutil.load_csi(args.csi)
    print(f"csi: {len(t_ms)} samples, {t_ms[-1]/1000:.0f}s")
    demos = resolve_demos(args)
    print("demos", [os.path.basename(d) for d in demos])
    rows = []
    for demo in demos:
        p0 = DemoParser(demo)
        map_name = p0.parse_header()["map_name"]
        try:
            off = offset_for(align, map_name, demo)
        except SystemExit as e:
            print("skip", os.path.basename(demo), e)
            continue
        print(f"{map_name} {os.path.basename(demo)} offset_s={off}")
        rows.extend(extract_demo(demo, off, args.steamid, session_id))
    df = pd.DataFrame(rows)
    df = apply_input_analysis(df, t_ms, body, offset_by_demo(align))
    return df


def run_reclassify(args) -> pd.DataFrame:
    parquet = os.path.join(args.output_dir, "shots.parquet")
    df = pd.read_parquet(parquet)
    align = load_alignment(args.alignment)
    if "demo" not in df.columns:
        # apply_input_analysis groupbys demo; backfill from alignment first
        # so old parquets still reclassify (by-map fallback for schema 1).
        matches = align.get("matches") or []
        by_map = {m["map"]: m for m in matches}
        df["demo"] = df["map"].map(lambda m: (by_map.get(m) or {}).get("demo"))
        df["match_id"] = df["map"].map(lambda m: (by_map.get(m) or {}).get("match_id"))
    print("loading CSI for reclassify…")
    t_ms, body = csiutil.load_csi(args.csi)
    df = apply_input_analysis(df, t_ms, body, offset_by_demo(align))
    if "session_id" not in df.columns:
        df["session_id"] = args.session_id or align.get("session_id") or csiutil.session_id_from_csi(args.csi)
    if "steamid" not in df.columns:
        df["steamid"] = str(args.steamid)
    return df


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.report_only and not args.reclassify:
        df = pd.read_parquet(os.path.join(args.output_dir, "shots.parquet"))
        slope_stats = attach_slope(df)
        print(old_vs_new(df))
        write_report(df, slope_stats, args.output_dir, os.path.basename(args.csi))
        return 0
    if args.reclassify:
        df = run_reclassify(args)
    else:
        df = run_extract(args)
    slope_stats = attach_slope(df)
    print("slope stats:", json.dumps(slope_stats, indent=2))
    print(df.groupby(["map", "is_first_bullet"]).size() if "map" in df.columns else "")
    print(old_vs_new(df))
    fb = df[df.is_first_bullet]
    print("input_class:\n", fb.input_class.value_counts().to_string())
    write_outputs(df, slope_stats, args.output_dir, os.path.basename(args.csi))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
