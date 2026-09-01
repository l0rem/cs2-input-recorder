Phase 2 alignment — session 2026-09-01_135047.csi

Session: 7021.1s, 6.64M samples, all analog-valid, started 2026-09-01 13:50:47.250000+00:00 UTC

Mouse1: 1662 rising edges -> 732 click-bursts (1.5s grouping)
Demos: 4 Valve MM matches (mirage, cache, inferno, dust2), user steamid 76561198158590364 ('lorem')

Per-match alignment (t_demo_tick/64 + offset = t_local, offset-only model):
  de_mirage   offset  26.01s  matched 39/52 bursts   median resid  -9.8ms  std 134ms
  de_cache    offset 1787.93s  matched  3/ 6 bursts   median resid -146.2ms (low sample)
  de_inferno  offset 2868.41s  matched 27/27 bursts   median resid  +0.0ms  std 122ms
  de_dust2    offset 4566.56s  matched 58/78 bursts   median resid -41.3ms  std 150ms

Random-baseline match rate ~8-12%; observed 60-100% => alignment is real.
Residual std 120-150ms reflects burst-start ambiguity (merged clicks,
weapon_fire tick quantization 15.6ms, plus genuine click->shot variance).
Inferno drift check: -279 ppm when fitting t=a*t+b; within Phase-2 spec
('a should be very close to 1'); offset-only is sufficient for now.

Unmatched bursts (~20-25%): clicks during buy/freezetime or aborted sprays —
normal for burst-level matching; per-shot Phase 3 matching will be tighter.
