# Phase 3 — corrected first-bullet analysis

Measurement pass after the ±16 / 130 u/s / leftover-RELEASE_ONLY review.
Speed = 1-tick horizontal posdiff at the fire tick. Native velocity is a check column.
Headline accuracy = rifles only, speed < 34% of weapon max. Input-cause gated on |Mouse1 residual| ≤ 30 ms.
Cache excluded from input-cause. Do not compare these numbers to the previous 86.4% / 50% RELEASE_ONLY headlines.

## Dataset
- Session CSI: `2026-09-01_135047.csi`
- Gun shots: 783 across de_cache, de_dust2, de_inferno, de_mirage
- Live (not warmup/freeze): 783
- First bullets (`shots_fired==1`): 393 (old 1s-gap rule would have kept 184)
- Headline rifles among first bullets: 170

## Old vs new (this rerun)

- Gun shots extracted: 783 (live 783, warmup/freeze 0)
- First bullets: old 1s-gap 184 → shots_fired==1 393
- Speed 1-tick vs ±16 window: n=783 corr=0.853 median |diff|=16.4 u/s p90=57.6
- Speed 1-tick vs native-at-tick: n=782 corr=0.985 median |diff|=2.3 disagree>30=11
- Rifle first-bullet 'accurate': ±16 window <130 = 93.5% → 1-tick < 34% max = 67.6% (n=170)
- Rifle first-bullet median speed: window 40.3 → 1-tick 39.4 u/s

## Rifle first-bullet accuracy (live, `shots_fired==1`)
- Accurate (< 34% max): **115/170 = 67.6%**
- Median speed 39.4 u/s; p90 173.7

| weapon | n | threshold | median speed | accurate |
|---|---:|---:|---:|---:|
| weapon_ak47 | 51 | 73.1 | 32.7 | 63% |
| weapon_famas | 12 | 74.8 | 70.5 | 75% |
| weapon_galilar | 42 | 73.1 | 39.1 | 64% |
| weapon_m4a1_silencer | 65 | 76.5 | 40.6 | 72% |

## Sync (first bullets, live)
- Offset-only residual: n=368 median -0.8 ms  std 161.9 ms  |resid|≤30 ms 16.3%

| map | n pairs | offset resid std | slope resid std | ppm | |off|≤30 | |slope|≤30 |
|---|---:|---:|---:|---:|---:|---:|
| de_cache | 8 | 215.8 | 199.3 | 1866.0 | 0% | 0% |
| de_dust2 | 168 | 184.1 | 145.0 | -188.6 | 14% | 11% |
| de_inferno | 82 | 113.0 | 99.1 | -123.9 | 30% | 27% |
| de_mirage | 110 | 147.1 | 138.2 | -90.0 | 13% | 17% |

Slope is a comparison only. Classification below uses the existing offset-only alignment.

## Input-cause classes
Only first bullets with `|residual_ms| ≤ 30` (cache always `SKIP_CACHE`).
No leftover `RELEASE_ONLY` bin. `RELEASE_NO_COUNTER` requires A/D analog and no opposite-key onset.

| class | n | % of first bullets | median speed |
|---|---:|---:|---:|
| SYNC_UNCERTAIN | 321 | 81.7% | 52.8 |
| RELEASE_NO_COUNTER | 22 | 5.6% | 46.65 |
| COUNTER_CLEAN | 17 | 4.3% | 40.4 |
| SKIP_CACHE | 14 | 3.6% | 49.25 |
| DIAGONAL | 13 | 3.3% | 76.3 |
| FORWARD_BACK | 3 | 0.8% | 0.0 |
| STATIONARY | 2 | 0.5% | 0.0 |
| DELAYED_OPPOSITE | 1 | 0.3% | 14.8 |

- W/S involved in window: 172/393 first bullets (44%)
- Sync-ok first bullets (input-cause trusted): 58/393

Among sync-ok first bullets only:

| class | n | % of sync-ok | median speed |
|---|---:|---:|---:|
| RELEASE_NO_COUNTER | 22 | 37.9% | 46.65 |
| COUNTER_CLEAN | 17 | 29.3% | 40.4 |
| DIAGONAL | 13 | 22.4% | 76.3 |
| FORWARD_BACK | 3 | 5.2% | 0.0 |
| STATIONARY | 2 | 3.4% | 0.0 |
| DELAYED_OPPOSITE | 1 | 1.7% | 14.8 |

- W/S involved among sync-ok: 22/58

## What this does *not* say
- Do not train on a '50% release-only' figure. That class no longer exists as a default bin.
- Initiation-gap medians among `COUNTER_*` rows are CSI-internal; they are only attached to a demo fire when `sync_ok`.
- Pistols/SMG/AWP are stored but excluded from the rifle headline.

