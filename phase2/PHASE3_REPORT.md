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


## Error classification (first bullets, 184 total)
| Class | n | % | Median speed |
|---|---|---|---|
| RELEASE_ONLY (decelerated by releasing, not countering) | 92 | 50.0% | 60.3 |
| CLEAN (countered, accurate) | 38 | 20.7% | 44.1 |
| EARLY_FIRE (>=130 u/s at shot) | 25 | 13.6% | 178.8 |
| STATIONARY | 24 | 13.0% | 9.7 |
| DELAYED_OPPOSITE_KEY (gap>100ms) | 4 | 2.2% | 54.0 |
| KEY_OVERLAP (>50ms) | 1 | 0.5% | 21.3 |

**Key insight:** half of your first bullets happen during pure release
deceleration, not active counter-strafing — and their median speed (60 u/s) is
*higher* than properly countered shots (44 u/s). Release-only deceleration
leaves you moving faster at the shot. This is directly actionable: your
training focus should be pressing the opposite key on every strafe-stop,
not just when you consciously remember.

Diagonals (W/A or W/D involvement) could not yet be separately classified —
needs the full transition classifier (Phase 3.6) with more data.
