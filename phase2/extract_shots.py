"""Phase 3 shot extraction: 1-tick speed, native-velocity check, CSI windows.

Dense contiguous parse_ticks per demo (sparse fire-tick lists make
demoparser2 return NaN/garbage velocity). Speed source is 1-tick
horizontal posdiff; native velocity at the exact fire tick is a check
column only (never ffilled over posdiff).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A

USER = "76561198158590364"
USER_INT = int(USER)
REPLAYS = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays"
CSI = r"C:\Users\lorem\Desktop\strafes\phase2\2026-09-01_135047.csi"
ALIGN_PATH = r"C:\Users\lorem\Desktop\strafes\phase2\alignment.json"
OUT = r"C:\Users\lorem\Desktop\strafes\phase2"
DEMO_GLOB = "match730_0038402*.dem"

GUNS = set(A.WEAPON_MAX_SPEED.keys())
BIT_W, BIT_A, BIT_S, BIT_D, BIT_M1 = 0x01, 0x02, 0x04, 0x08, 0x80
LEAD = 4          # ticks before fire included in the dense span
WINDOW_HALF = 16  # old ±16 estimator
PRE_MS, POST_MS = 400.0, 50.0
TICK_PROPS = [
    "X", "Y", "velocity_X", "velocity_Y",
    "shots_fired", "duck_amount", "is_scoped", "is_airborne",
    "is_warmup_period", "is_freeze_period",
]


def load_csi(path: str):
    raw = open(path, "rb").read()
    body = np.frombuffer(raw[96:], dtype=np.dtype([
        ("dt", "<u4"), ("w", "<u2"), ("a", "<u2"), ("s", "<u2"), ("d", "<u2"),
        ("mask", "<u2"), ("flags", "<u2"),
    ]))
    t_ms = np.cumsum(body["dt"].astype(np.float64)) / 1000.0
    return t_ms, body


def _col(df, name):
    return name if name in df.columns else None


def parse_user_ticks(parser: DemoParser, lo: int, hi: int) -> pd.DataFrame:
    """Contiguous tick range — required for velocity to resync."""
    ticks = list(range(max(0, lo), hi))
    try:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks, players=[USER_INT])
    except TypeError:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks)
    if pos is None or len(pos) == 0:
        pos = parser.parse_ticks(TICK_PROPS, ticks=ticks)
    if "steamid" in pos.columns:
        pos = pos[pos.steamid.astype(str) == USER].copy()
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


def extract_demo(demo: str, offset_s: float, t_ms, body, edge_ms) -> list[dict]:
    p = DemoParser(demo)
    map_name = p.parse_header()["map_name"]
    ev = p.parse_event("weapon_fire")
    mine = ev[ev.user_steamid.astype(str) == USER].copy()
    guns = mine[mine.weapon.isin(GUNS)].sort_values("tick").reset_index(drop=True)
    if not len(guns):
        print(f"  {map_name}: no gun fires")
        return []
    fire_ticks = [int(t) for t in guns.tick.tolist()]
    lo = min(fire_ticks) - WINDOW_HALF - LEAD
    hi = max(fire_ticks) + WINDOW_HALF + 2
    print(f"  {map_name}: {len(fire_ticks)} fires, ticks [{lo}, {hi}) contiguous")
    pos = parse_user_ticks(p, lo, hi)
    print(f"    user tick rows {len(pos)} cols {list(pos.columns)}")
    by_tick = {int(r.tick): r.to_dict() for _, r in pos.iterrows()}

    rows = []
    prev_fire = None
    for _, g in guns.iterrows():
        ft = int(g.tick)
        rec = lookup_row(by_tick, ft)
        prev = lookup_row(by_tick, ft - 1)
        r16a = lookup_row(by_tick, ft - WINDOW_HALF)
        r16b = lookup_row(by_tick, ft + WINDOW_HALF)

        if rec is None or prev is None:
            spd = float("nan")
        else:
            spd = A.horizontal_speed(prev["X"], prev["Y"], rec["X"], rec["Y"], 1)

        native = float("nan")
        if rec is not None and _col(pos, "velocity_X") and rec.get("velocity_X") == rec.get("velocity_X"):
            vx, vy = rec.get("velocity_X"), rec.get("velocity_Y")
            if vy == vy:
                native = float(np.hypot(float(vx), float(vy)))

        if r16a is not None and r16b is not None:
            win = A.horizontal_speed(
                r16a["X"], r16a["Y"], r16b["X"], r16b["Y"], 2 * WINDOW_HALF,
            )
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

        wmax = A.max_speed(g.weapon, scoped)
        thresh = A.accuracy_threshold(g.weapon, scoped)
        ratio = (spd / wmax) if (wmax and spd == spd) else float("nan")
        margin = (thresh - spd) if (thresh is not None and spd == spd) else float("nan")
        acc = A.rifle_accurate(g.weapon, spd, scoped)

        local_ms = (ft / A.TICKRATE + offset_s) * 1000.0
        resid, matched = A.nearest_residual_ms(local_ms, edge_ms)

        lo_i = int(np.searchsorted(t_ms, local_ms - PRE_MS))
        hi_i = int(np.searchsorted(t_ms, local_ms + POST_MS))
        mid_i = int(np.searchsorted(t_ms, local_ms))
        sl = slice(lo_i, hi_i)
        pre = slice(lo_i, mid_i)
        w_max = int(body["w"][sl].max()) if hi_i > lo_i else 0
        a_max = int(body["a"][sl].max()) if hi_i > lo_i else 0
        s_max = int(body["s"][sl].max()) if hi_i > lo_i else 0
        d_max = int(body["d"][sl].max()) if hi_i > lo_i else 0
        mask_pre = body["mask"][pre]
        a_down = (mask_pre & BIT_A) != 0
        d_down = (mask_pre & BIT_D) != 0
        gap_ms, overlap_ms = A.ad_counter_metrics(a_down, d_down)

        cls = A.classify_input(
            map_name=map_name,
            residual_ms=resid,
            speed=spd if spd == spd else 0.0,
            w_max=w_max, a_max=a_max, s_max=s_max, d_max=d_max,
            gap_ms=gap_ms, overlap_ms=overlap_ms,
        )

        rows.append({
            "map": map_name,
            "tick": ft,
            "weapon": g.weapon,
            "speed_at_fire": None if spd != spd else round(float(spd), 1),
            "speed_native_check": None if native != native else round(float(native), 1),
            "speed_at_fire_window": None if win != win else round(float(win), 1),
            "shots_fired": None if sf != sf else int(sf),
            "is_first_bullet": first,
            "is_first_bullet_old": bool(first_old),
            "is_scoped": scoped,
            "duck_at_fire": None if duck is None or duck != duck else round(float(duck), 3),
            "is_airborne": bool(air),
            "in_warmup": warmup,
            "in_freeze": freeze,
            "weapon_max_speed": wmax,
            "accuracy_threshold": None if thresh is None else round(float(thresh), 1),
            "speed_ratio": None if ratio != ratio else round(float(ratio), 3),
            "margin_to_threshold": None if margin != margin else round(float(margin), 1),
            "rifle_accurate": acc,
            "is_headline_rifle": A.is_headline_rifle(g.weapon),
            "local_ms": round(float(local_ms), 1),
            "mouse1_ms": None if matched is None else round(float(matched), 1),
            "residual_ms": None if resid is None else round(float(resid), 1),
            "w_max_pre": w_max, "a_max_pre": a_max, "s_max_pre": s_max, "d_max_pre": d_max,
            "gap_ms": gap_ms, "overlap_ms": overlap_ms,
            "sync_ok": cls["sync_ok"],
            "ws_involved": cls["ws_involved"],
            "ad_involved": cls["ad_involved"],
            "input_class": cls["input_class"],
        })
    return rows


def attach_slope(df: pd.DataFrame) -> dict:
    """Fit t_csi = a * t_demo + b per map on first-bullet Mouse1 matches; store residual_slope_ms."""
    stats = {}
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
        }
        idx = use.index
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


def write_report(df: pd.DataFrame, slope_stats: dict):
    live = df[~df.in_warmup & ~df.in_freeze]
    fb = live[live.is_first_bullet]
    rifles = fb[fb.is_headline_rifle]
    sync = fb[fb.sync_ok]
    lines = []
    lines.append("# Phase 3 — corrected first-bullet analysis")
    lines.append("")
    lines.append("Measurement pass after the ±16 / 130 u/s / leftover-RELEASE_ONLY review.")
    lines.append("Speed = 1-tick horizontal posdiff at the fire tick. Native velocity is a check column.")
    lines.append("Headline accuracy = rifles only, speed < 34% of weapon max. Input-cause gated on |Mouse1 residual| ≤ 30 ms.")
    lines.append("Cache excluded from input-cause. Do not compare these numbers to the previous 86.4% / 50% RELEASE_ONLY headlines.")
    lines.append("")
    lines.append("## Dataset")
    lines.append(f"- Session CSI: `{os.path.basename(CSI)}`")
    maps = sorted(df["map"].astype(str).unique())
    lines.append(f"- Gun shots: {len(df)} across {', '.join(maps)}")
    lines.append(f"- Live (not warmup/freeze): {len(live)}")
    lines.append(f"- First bullets (`shots_fired==1`): {len(fb)} (old 1s-gap rule would have kept {int(live.is_first_bullet_old.sum())})")
    lines.append(f"- Headline rifles among first bullets: {len(rifles)}")
    lines.append("")
    lines.append(old_vs_new(df))
    lines.append("## Rifle first-bullet accuracy (live, `shots_fired==1`)")
    if len(rifles):
        acc = rifles[rifles.rifle_accurate == True]  # noqa: E712
        lines.append(f"- Accurate (< 34% max): **{len(acc)}/{len(rifles)} = {len(acc)/len(rifles):.1%}**")
        lines.append(f"- Median speed {rifles.speed_at_fire.median():.1f} u/s; p90 {rifles.speed_at_fire.quantile(0.9):.1f}")
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
    lines.append("## Sync (first bullets, live)")
    r = fb.residual_ms.dropna()
    if len(r):
        lines.append(
            f"- Offset-only residual: n={len(r)} median {r.median():.1f} ms  "
            f"std {r.std():.1f} ms  |resid|≤30 ms {(r.abs()<=30).mean():.1%}"
        )
    lines.append("")
    lines.append("| map | n pairs | offset resid std | slope resid std | ppm | |off|≤30 | |slope|≤30 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for m, s in slope_stats.items():
        if "ppm" not in s:
            lines.append(f"| {m} | {s.get('n', 0)} | — | — | — | — | — |")
            continue
        lines.append(
            f"| {m} | {s['n']} | {s['offset_resid_std_ms']} | {s['slope_resid_std_ms']} | "
            f"{s['ppm']} | {s['frac_sync_ok_offset']:.0%} | {s['frac_sync_ok_slope']:.0%} |"
        )
    lines.append("")
    lines.append("Slope is a comparison only. Classification below uses the existing offset-only alignment.")
    lines.append("")
    lines.append("## Input-cause classes")
    lines.append("Only first bullets with `|residual_ms| ≤ 30` (cache always `SKIP_CACHE`).")
    lines.append("No leftover `RELEASE_ONLY` bin. `RELEASE_NO_COUNTER` requires A/D analog and no opposite-key onset.")
    lines.append("")
    vc = fb.input_class.value_counts()
    lines.append("| class | n | % of first bullets | median speed |")
    lines.append("|---|---:|---:|---:|")
    for cls, n in vc.items():
        med = fb.loc[fb.input_class == cls, "speed_at_fire"].median()
        lines.append(f"| {cls} | {n} | {n/len(fb):.1%} | {med} |")
    lines.append("")
    ws = fb[fb.ws_involved]
    lines.append(f"- W/S involved in window: {len(ws)}/{len(fb)} first bullets ({len(ws)/len(fb):.0%})" if len(fb) else "")
    n_ok = int(fb.sync_ok.sum())
    lines.append(f"- Sync-ok first bullets (input-cause trusted): {n_ok}/{len(fb)}")
    lines.append("")
    ok = fb[fb.sync_ok]
    if len(ok):
        lines.append("Among sync-ok first bullets only:")
        lines.append("")
        lines.append("| class | n | % of sync-ok | median speed |")
        lines.append("|---|---:|---:|---:|")
        for cls, n in ok.input_class.value_counts().items():
            med = ok.loc[ok.input_class == cls, "speed_at_fire"].median()
            lines.append(f"| {cls} | {n} | {n/len(ok):.1%} | {med} |")
        lines.append("")
        lines.append(f"- W/S involved among sync-ok: {int(ok.ws_involved.sum())}/{len(ok)}")
        lines.append("")
    lines.append("## What this does *not* say")
    lines.append("- Do not train on a '50% release-only' figure. That class no longer exists as a default bin.")
    lines.append("- Initiation-gap medians among `COUNTER_*` rows are CSI-internal; they are only attached to a demo fire when `sync_ok`.")
    lines.append("- Pistols/SMG/AWP are stored but excluded from the rifle headline.")
    lines.append("")
    path = os.path.join(OUT, "PHASE3_REPORT.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"wrote {path}")


def main():
    align = json.load(open(ALIGN_PATH))
    print("loading CSI…")
    t_ms, body = load_csi(CSI)
    m1 = (body["mask"] & BIT_M1) != 0
    edge_ms = t_ms[A.rising_edges(m1)]
    print(f"csi: {len(t_ms)} samples, {t_ms[-1]/1000:.0f}s, {len(edge_ms)} Mouse1 rising edges")

    demos = sorted(glob.glob(os.path.join(REPLAYS, DEMO_GLOB)))
    print("demos", [os.path.basename(d) for d in demos])
    rows = []
    for demo in demos:
        p0 = DemoParser(demo)
        map_name = p0.parse_header()["map_name"]
        if map_name not in align:
            print("skip unaligned", map_name)
            continue
        off = float(align[map_name]["offset_s"])
        print(f"{map_name} offset_s={off}")
        rows.extend(extract_demo(demo, off, t_ms, body, edge_ms))

    df = pd.DataFrame(rows)
    slope_stats = attach_slope(df)
    print("slope stats:", json.dumps(slope_stats, indent=2))

    parquet = os.path.join(OUT, "shots.parquet")
    csv = os.path.join(OUT, "first_bullets_classified.csv")
    df.to_parquet(parquet, index=False)
    fb = df[df.is_first_bullet].copy()
    fb.to_csv(csv, index=False)
    print(f"{len(df)} shots → {parquet}")
    print(f"{len(fb)} first bullets → {csv}")
    print(df.groupby(["map", "is_first_bullet"]).size())
    print(old_vs_new(df))
    print("input_class:\n", fb.input_class.value_counts().to_string())
    write_report(df, slope_stats)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--report-only":
        df = pd.read_parquet(os.path.join(OUT, "shots.parquet"))
        slope_stats = attach_slope(df)
        print(old_vs_new(df))
        write_report(df, slope_stats)
    else:
        main()
