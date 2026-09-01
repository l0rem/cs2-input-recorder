# Phase 3 — first counter-strafe analysis (4 matches, 2026-09-01)

## Dataset
- 783 gun shots across 4 matches (mirage 292, dust2 321, inferno 121, cache 49 gun fires)
- 184 first bullets analyzed
- 46 with clear A<->D counter-strafe transitions in the prior 400 ms

## Headline: movement at first bullet
- Accurate at shot (<130 u/s): **86.4% overall** (mirage 82.8%, dust2 87.6%, inferno 90.3%)
- Median first-bullet speed: 54.7 u/s; p90: 133 u/s
- 25 shots (13.6%) fired while still moving fast (>=130 u/s)

## Input execution (46 counter-strafe first bullets)
- Initiation gap (old-key release -> new-key digital onset):
  median 14 ms, p75 40 ms, worst 272 ms
- Digital overlap (both opposing keys down): median 0 ms, p75 7 ms, max 104 ms
- Initiation gap does NOT correlate with shot speed here (r=0.05):
  your slow initiations happen when you were already slowing, so they don't
  cost accuracy on the FIRST bullet — but 8 shots with gap >50 ms were still
  all accurate, meaning the counter completed anyway.

## Preliminary diagnosis (small sample!)
1. Your counter-strafing execution is fundamentally clean (88-100% accurate
   regardless of gap length).
2. The rare bad shots (10 >=200 u/s) are worth watching in replay — these are
   the 'shot before correction complete' class.
3. Sample sizes are too small for diagonal-specific conclusions yet; keep
   recording matches.

## Recorder fixes queued (from data):
- dt drift ~4% under load (p99 1214us in Test B): consider busy-wait hybrid later
- GetAsyncKeyState can miss sub-ms taps at 1 kHz: acceptable, noted
