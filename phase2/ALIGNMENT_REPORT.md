Phase 2 alignment — session 2026-09-01_135047.csi

Session: 7021.1s, 6.64M samples, all analog-valid, started 2026-09-01 13:50:47.250000+00:00 UTC

Mouse1: 1662 rising edges -> 732 click-bursts (1.5s grouping from previous edge)
Demos: 4 Valve MM matches (mirage, cache, inferno, dust2), user steamid 76561198158590364 ('lorem')

Per-match alignment (t_demo_tick/64 + offset = t_local, offset-only model).
Keyed by demo filename (schema 2) so two Infernos cannot collide.

  de_mirage   match730_003840286046407361025_1338603495_187.dem  offset  26.01s  matched 39/52 bursts   median resid  -9.8ms  std 134ms
  de_cache    match730_003840287571120751068_0576888176_184.dem  offset 1787.93s  matched  3/ 6 bursts   median resid -146.2ms (low sample)
  de_inferno  match730_003840291013537038882_0808600625_274.dem  offset 2868.41s  matched 27/27 bursts   median resid  +0.0ms  std 122ms
  de_dust2    match730_003840296025763873033_0870652904_272.dem  offset 4566.56s  matched 58/78 bursts   median resid -41.3ms  std 150ms

These offsets are what shot extraction used. `python align.py` rediscovers
mirage/inferno/dust2 within 0.1 s. For cache it prefers 2101.29s (6/6 bursts,
~25 ms residual std) over 1787.93s (3/6). Cache is SKIP_CACHE for input-cause,
so the committed 1787.93s offset is kept for this dataset.

Regenerate (writes alignment.json + ALIGNMENT_REPORT.md):

  python align.py --csi <session.csi> --replays <dir> --output-dir phase2

Per-shot matching in extract_shots.py is one-to-one monotonic, not nearest-edge.
