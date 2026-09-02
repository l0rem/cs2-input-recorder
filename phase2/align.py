"""Burst-level CSI↔demo timeline alignment.

Groups weapon_fire and Mouse1 rising edges into 1.5 s bursts, searches a
single offset per demo (one-to-one burst match), and refuses to overlay two
demos on the same session interval so a second Inferno/Mirage gets its own
record.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A
import csiutil

USER = "76561198158590364"
REPLAYS = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays"
CSI = r"C:\Users\lorem\Desktop\strafes\phase2\2026-09-01_135047.csi"
OUT = r"C:\Users\lorem\Desktop\strafes\phase2"
DEMO_GLOB = "match730_0038402*.dem"
GUNS = set(A.WEAPON_MAX_SPEED.keys())


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Align Valve MM demos to a CSI session")
    p.add_argument("--steamid", default=USER)
    p.add_argument("--csi", default=CSI)
    p.add_argument("--replays", default=REPLAYS)
    p.add_argument("--demo", action="append", default=[])
    p.add_argument("--demo-glob", default=DEMO_GLOB)
    p.add_argument("--output-dir", default=OUT)
    p.add_argument("--session-id", default=None)
    return p.parse_args(argv)


def resolve_demos(args) -> list[str]:
    if args.demo:
        return [os.path.abspath(d) for d in args.demo]
    return sorted(glob.glob(os.path.join(args.replays, args.demo_glob)))


def demo_gun_times(demo: str, steamid: str) -> tuple[str, np.ndarray]:
    p = DemoParser(demo)
    map_name = p.parse_header()["map_name"]
    ev = p.parse_event("weapon_fire")
    mine = ev[ev.user_steamid.astype(str) == steamid]
    guns = mine[mine.weapon.isin(GUNS)]
    ticks = np.sort(guns.tick.to_numpy().astype(float))
    return map_name, ticks / A.TICKRATE


def write_alignment_report(path: str, session_id: str, csi_s: float, n_edges: int,
                           n_bursts: int, matches: list):
    lines = [
        f"Phase 2 alignment — session {session_id}.csi",
        "",
        f"Session: {csi_s:.1f}s, Mouse1 rising edges {n_edges} -> {n_bursts} click-bursts "
        f"(1.5s grouping from previous edge)",
        "",
        "Per-match alignment (t_demo_tick/64 + offset = t_local, offset-only model):",
    ]
    for m in matches:
        med = m.get("median_residual_ms")
        std = m.get("residual_std_ms")
        med_s = f"{med:7.1f}ms" if med is not None else "    n/a"
        std_s = f"{std:.0f}ms" if std is not None else "n/a"
        lines.append(
            f"  {m['map']:<12} {m.get('demo', ''):<48}  offset {m['offset_s']:8.2f}s  "
            f"matched {m['matched']:3d}/{m['total_bursts']:3d} bursts   "
            f"median resid {med_s}  std {std_s}"
        )
    lines.append("")
    lines.append("Offsets are keyed by demo filename, not map name — two Infernos do not collide.")
    lines.append("Per-shot matching in extract_shots.py is one-to-one monotonic, not nearest-edge.")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    demos = resolve_demos(args)
    if not demos:
        print("no demos found", file=sys.stderr)
        return 2
    session_id = args.session_id or csiutil.session_id_from_csi(args.csi)
    print(f"loading CSI {args.csi}")
    t_ms, body = csiutil.load_csi(args.csi)
    edge_s = csiutil.mouse1_edge_ms(t_ms, body) / 1000.0
    csi_bursts = A.group_bursts(edge_s)
    print(f"csi {t_ms[-1]/1000:.0f}s, {len(edge_s)} Mouse1 edges, {len(csi_bursts)} bursts")

    items = []
    for demo in demos:
        map_name, times_s = demo_gun_times(demo, args.steamid)
        bursts = A.group_bursts(times_s)
        print(f"  {os.path.basename(demo)} {map_name} fires={len(times_s)} bursts={len(bursts)}")
        if bursts.size == 0:
            continue
        items.append({
            "id": os.path.basename(demo),
            "demo": os.path.basename(demo),
            "demo_path": demo,
            "map": map_name,
            "bursts": bursts,
            "tmin": float(times_s[0]),
            "tmax": float(times_s[-1]),
        })

    placed = A.align_demos(items, csi_bursts)
    matches = []
    for item, rec in zip(items, placed):
        m = {
            "demo": item["demo"],
            "demo_path": item["demo_path"],
            "match_id": Path(item["demo"]).stem,
            "map": item["map"],
            "model": "offset_only",
            "offset_s": rec["offset_s"],
            "matched": rec["matched"],
            "total_bursts": rec["total_bursts"],
            "median_residual_ms": rec.get("median_residual_ms"),
            "residual_std_ms": rec.get("residual_std_ms"),
            "residual_p10_ms": rec.get("residual_p10_ms"),
            "residual_p90_ms": rec.get("residual_p90_ms"),
            "tmin_s": item["tmin"],
            "tmax_s": item["tmax"],
        }
        matches.append(m)
        print(f"  -> {m['map']} offset={m['offset_s']:.4f}s matched {m['matched']}/{m['total_bursts']}")

    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "schema": 2,
        "session_id": session_id,
        "csi": os.path.abspath(args.csi),
        "steamid": str(args.steamid),
        "matches": matches,
    }
    # map-keyed copy only when unique, for older readers
    by_map = {}
    for m in matches:
        by_map.setdefault(m["map"], m)
    if len(by_map) == len(matches):
        payload["by_map"] = {k: {"offset_s": v["offset_s"], "model": v["model"],
                                 "matched": v["matched"], "total_bursts": v["total_bursts"],
                                 "median_residual_ms": v["median_residual_ms"],
                                 "residual_std_ms": v["residual_std_ms"],
                                 "residual_p10_ms": v["residual_p10_ms"],
                                 "residual_p90_ms": v["residual_p90_ms"]}
                             for k, v in by_map.items()}

    align_path = os.path.join(out_dir, "alignment.json")
    with open(align_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")
    report_path = os.path.join(out_dir, "ALIGNMENT_REPORT.md")
    write_alignment_report(report_path, session_id, float(t_ms[-1] / 1000.0),
                           len(edge_s), len(csi_bursts), matches)
    print(f"wrote {align_path}")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
