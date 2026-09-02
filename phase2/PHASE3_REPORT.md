# Phase 3 — corrected first-bullet analysis

Speed = 1-tick horizontal posdiff. Native velocity is a check column.
All-posture rifle accuracy = speed < 34% of weapon max.
Primary counter-strafe headline = standing, on-ground rifles only.
Input-cause gated on |classification residual| ≤ 30 ms (existing-alignment, monotonic match).
Refit offset/affine residuals are comparison-only and are not used for classes.
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

## Rifle first-bullet accuracy
- All-posture (any duck/air): **115/170 = 67.6%**
- Standing, on-ground (primary): **92/145 = 63.4%**
- Fully crouched: **22/24 = 91.7%**
- Median speed (all-posture) 39.4 u/s; p90 173.7
- Median speed (standing) 45.6 u/s; p90 181.5

| weapon | n | threshold | median speed | accurate |
|---|---:|---:|---:|---:|
| weapon_ak47 | 51 | 73.1 | 32.7 | 63% |
| weapon_famas | 12 | 74.8 | 70.5 | 75% |
| weapon_galilar | 42 | 73.1 | 39.1 | 64% |
| weapon_m4a1_silencer | 65 | 76.5 | 40.6 | 72% |

## Native-velocity check
- Shots with |posdiff − native| > 30 u/s: 11 (kept on posdiff; flagged `measurement_uncertain`).
- Native is not promoted. Disagreements are typically native reading high.

| map | tick | weapon | posdiff | native | first |
|---|---:|---|---:|---:|---|
| de_dust2 | 6638 | weapon_usp_silencer | 110.4 | 142.2 | True |
| de_dust2 | 6758 | weapon_usp_silencer | 189.0 | 224.0 | True |
| de_dust2 | 54136 | weapon_ssg08 | 105.4 | 136.3 | True |
| de_dust2 | 89411 | weapon_galilar | 145.2 | 176.8 | True |
| de_dust2 | 90614 | weapon_galilar | 132.4 | 163.2 | True |
| de_dust2 | 93619 | weapon_galilar | 86.3 | 207.4 | True |
| de_inferno | 68318 | weapon_m4a1_silencer | 60.8 | 95.2 | False |
| de_inferno | 73762 | weapon_glock | 138.3 | 172.5 | True |
| de_inferno | 85008 | weapon_deagle | 127.0 | 159.3 | True |
| de_mirage | 2237 | weapon_glock | 134.7 | 167.6 | True |
| de_mirage | 69869 | weapon_ak47 | 42.2 | 87.7 | False |

## Sync (first bullets, live)
- Mouse1 candidates (monotonic match within 400 ms): 365/393
- Inside ±30 ms gate: 37/365 candidates
- Trusted input-cause after Cache skip: 36/393
- Classification residual (existing alignment): n=365 median -3.1 ms  std 206.2 ms  |resid|≤30 ms 10.1%

Refit comparison (not used for classification):

| map | n pairs | classif ≤30 | refit-offset std | refit-affine std | ppm | refit-off ≤30 | refit-aff ≤30 |
|---|---:|---:|---:|---:|---:|---:|---:|
| de_cache | 6 | 17% | 170.5 | 138.4 | 1962.0 | 0% | 0% |
| de_dust2 | 168 | 4% | 247.4 | 66.8 | -396.2 | 6% | 59% |
| de_inferno | 82 | 17% | 168.8 | 82.9 | -335.1 | 18% | 30% |
| de_mirage | 109 | 14% | 151.8 | 109.3 | -189.6 | 9% | 18% |

## Input-cause classes
Only first bullets with `|classification_residual_ms| ≤ 30` (cache always `SKIP_CACHE`).
Classes are positive evidence. `LATERAL_RELEASE_WITHOUT_OPPOSITE` is an analog-down A/D window with no paired opposite onset — not a verified release-only mechanic.
`MIXED_AXIS_UNRESOLVED` is W/S+A/D activity without a verified opposing-diagonal pair. `DIAGONAL_COUNTER` requires WA→SD / WD→SA / mirrors.

| class | n | % of first bullets | median speed |
|---|---:|---:|---:|
| SYNC_UNCERTAIN | 343 | 87.3% | 52.5 |
| LATERAL_COUNTER | 33 | 8.4% | 45.7 |
| SKIP_CACHE | 14 | 3.6% | 49.25 |
| STATIONARY_NO_RECENT_MOVEMENT | 1 | 0.3% | 0.0 |
| DIAGONAL_COUNTER | 1 | 0.3% | 18.2 |
| LATERAL_HELD_THROUGH_SHOT | 1 | 0.3% | 227.7 |

- W/S involved in window: 156/393 first bullets (40%)
- Sync-ok first bullets (input-cause trusted): 36/393

Among sync-ok first bullets only:

| class | n | % of sync-ok | median speed |
|---|---:|---:|---:|
| LATERAL_COUNTER | 33 | 91.7% | 45.7 |
| STATIONARY_NO_RECENT_MOVEMENT | 1 | 2.8% | 0.0 |
| DIAGONAL_COUNTER | 1 | 2.8% | 18.2 |
| LATERAL_HELD_THROUGH_SHOT | 1 | 2.8% | 227.7 |

- W/S involved among sync-ok: 15/36
- LATERAL_COUNTER quality: CLEAN 29, OVERLAP 2, DELAYED 2

## What this does *not* say
- Do not train on a '50% release-only' figure. That class no longer exists as a default bin.
- Standing/on-ground accuracy is the counter-strafe-oriented headline; all-posture mixes in crouch.
- Trusted input-cause coverage is still a minority of first bullets. Do not generalise class mix to all shots.
- Initiation-gap values are CSI-internal and only attached to a demo fire when `sync_ok`.
- Pistols/SMG/AWP are stored but excluded from the rifle headline.

