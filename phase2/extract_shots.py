
"""Phase 3 shot extraction: demo first-bullets + aligned csi input windows."""
import json, struct, sys
import numpy as np
import pandas as pd
from demoparser2 import DemoParser

USER = "76561198158590364"
REPLAYS = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays"
CSI = r"C:\Users\lorem\Desktop\strafes\phase2\2026-09-01_135047.csi"
OUT = r"C:\Users\lorem\Desktop\strafes\phase2"
TICKRATE = 64.0
EXCLUDE_WEAPONS = {"weapon_knife","weapon_knife_butterfly","weapon_flashbang",
    "weapon_smokegrenade","weapon_hegrenade","weapon_molotov","weapon_incgrenade","weapon_taser"}
GUNS = {"weapon_ak47","weapon_m4a1_silencer","weapon_m4a1","weapon_galilar","weapon_famas",
        "weapon_awp","weapon_ssg08","weapon_deagle","weapon_usp_silencer","weapon_glock",
        "weapon_tec9","weapon_elite","weapon_mac10"}

def load_csi(path):
    raw = open(path,"rb").read()
    body = raw[96:]
    n = len(body)//16
    dt = np.empty(n, dtype=np.uint32)
    w = np.empty(n, dtype=np.uint16); a = np.empty(n, dtype=np.uint16)
    s = np.empty(n, dtype=np.uint16); d = np.empty(n, dtype=np.uint16)
    mask = np.empty(n, dtype=np.uint16); flags = np.empty(n, dtype=np.uint16)
    for i in range(n):
        b = body[i*16:(i+1)*16]
        dt[i] = int.from_bytes(b[0:4],"little")
        w[i] = int.from_bytes(b[4:6],"little"); a[i] = int.from_bytes(b[6:8],"little")
        s[i] = int.from_bytes(b[8:10],"little"); d[i] = int.from_bytes(b[10:12],"little")
        mask[i] = int.from_bytes(b[12:14],"little"); flags[i] = int.from_bytes(b[14:16],"little")
    t_ms = np.cumsum(dt, dtype=np.float64) / 1000.0  # ms since session start
    return t_ms, w, a, s, d, mask, flags

def main():
    align = json.load(open(r"C:\Users\lorem\Desktop\strafes\phase2\alignment.json"))
    t_ms, W, A, S, D, MASK, FLAGS = load_csi(CSI)
    session_s = t_ms[-1]/1000.0
    print(f"csi: {len(t_ms)} samples, {session_s:.0f}s")

    import glob, os
    demos = sorted(glob.glob(os.path.join(REPLAYS, "match730_0038402*.dem")))
    rows = []
    for demo in demos:
        p = DemoParser(demo)
        map_name = p.parse_header()["map_name"]
        if map_name not in align: continue
        al = align[map_name]
        off = al["offset_s"]
        ev = p.parse_event("weapon_fire")
        mine = ev[ev.user_steamid.astype(str)==USER].copy()
        guns = mine[mine.weapon.isin(GUNS)].sort_values("tick").reset_index(drop=True)
        if not len(guns): continue
        lo = max(0, int(guns.tick.min())-100); hi = int(guns.tick.max())+100
        pos = p.parse_ticks(["X","Y","duck_amount"], ticks=list(range(lo, hi, 4)))
        mp = pos[pos.steamid.astype(str)==USER].sort_values("tick").reset_index(drop=True)
        tp = mp.tick.values.astype(float); xp = mp.X.values; yp = mp.Y.values; dk = mp.duck_amount.values
        prev_fire_tick = None
        for _, r in guns.iterrows():
            ft = float(r.tick)
            # first bullet: >1.0s since previous gun fire
            first = prev_fire_tick is None or (ft - prev_fire_tick) > 64.0
            prev_fire_tick = ft
            # velocity via position difference over +-16 ticks
            i0 = np.searchsorted(tp, ft-16); i1 = np.searchsorted(tp, ft+16)
            i0, i1 = max(0,i0-1), min(len(tp)-1, i1)
            if i1 <= i0 or tp[i1]==tp[i0]:
                vx = vy = 0.0
            else:
                dt_s = (tp[i1]-tp[i0])/TICKRATE
                vx = (mp.X.values[i1]-mp.X.values[i0])/dt_s
                vy = (mp.Y.values[i1]-mp.Y.values[i0])/dt_s
            speed = (vx*vx+vy*vy)**0.5
            # duck at fire tick (nearest sample)
            j = min(len(tp)-1, np.searchsorted(tp, ft))
            duck = dk[j]
            # csi window: local_t = demo_t + off; find nearest sample
            local_ms = (ft/TICKRATE + off)*1000.0
            c = np.searchsorted(t_ms, local_ms)
            if c >= len(t_ms) or c < 0: continue
            # window: -400ms .. +50ms
            lo_i = np.searchsorted(t_ms, local_ms-400)
            hi_i = np.searchsorted(t_ms, local_ms+50)
            win_w = W[lo_i:hi_i]; win_a = A[lo_i:hi_i]; win_s = S[lo_i:hi_i]; win_d = D[lo_i:hi_i]
            win_m = MASK[lo_i:hi_i]
            rows.append({
                "map": map_name, "tick": int(ft), "weapon": r.weapon,
                "is_first_bullet": first,
                "speed_at_fire": round(float(speed),1),
                "duck_at_fire": round(float(dk[j if j<len(dk) else -1]),3) if len(dk) else None,
                "local_ms": round(float(local_ms),1),
                "w_max_pre": int(win_w.max()) if len(win_w) else None,
                "a_max_pre": int(win_a.max()) if len(win_a) else None,
                "s_max_pre": int(win_s.max()) if len(win_s) else None,
                "d_max_pre": int(win_d.max()) if len(win_d) else None,
            })
    df = pd.DataFrame(rows)
    df.to_parquet(r"C:\Users\lorem\Desktop\strafes\phase2\shots.parquet")
    print(f"{len(df)} shots extracted")
    print(df.groupby(['map','is_first_bullet']).size())
    print(df[df.is_first_bullet][['map','weapon','speed_at_fire']].describe())

if __name__ == "__main__":
    main()
