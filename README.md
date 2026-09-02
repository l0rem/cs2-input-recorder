# cs2-input-recorder

Passive, read-only keyboard/mouse telemetry recorder for Counter-Strike 2
counter-strafe analysis. Runs beside the game, samples Wooting analog
W/A/S/D plus OS-visible digital keys at ~1 kHz into a compact binary file,
follows the CS2 process lifetime automatically, and shows its state in the
system tray. An offline analysis pipeline (Python) aligns recorded sessions
with match demos and classifies counter-strafe execution per first bullet.

**The recorder is telemetry only.** It never reads game memory, injects,
hooks, modifies or generates input, or touches the network.

## Status: complete and in daily use

All four validation tests passed (see [Validation history](#validation-history)).
The tool is production-stable; development is data-driven — new work only
starts when recorded match data justifies it (see [Future improvements](#future-improvements)).

## Components

| Piece | Purpose |
|---|---|
| `cs2-input-recorder.exe` | The recorder: waits for `cs2.exe`, samples at ~1 kHz, writes `.csi`, tray icon |
| `csi-decode.exe` | Offline decoder: `inspect` + bounded `export-csv` |
| `phase2/` | Alignment + analysis: demo↔session timeline matching, per-shot extraction, error classification |
| `start-recorder-hidden.vbs` | Hidden-window launcher (full timer precision, no visible console) |

## Build

Requirements: Windows 10/11, [Rust](https://rustup.rs) (MSVC toolchain),
VS Build Tools with the C++ workload. The analyzer additionally needs
`pip install demoparser2 pandas`.

```bash
cargo build --release
# the dist SDK DLL must sit next to the recorder exe:
cp wooting-analog-sdk/release/wooting_analog_sdk_dist.dll target/release/
```

## Run

```bash
cs2-input-recorder.exe                        # wait for cs2.exe, record while it runs
cs2-input-recorder.exe --force                # record immediately (testing)
cs2-input-recorder.exe --force --duration 30  # forced 30 s capture
cs2-input-recorder.exe --hz 1000              # sample rate (default 1000)
cs2-input-recorder.exe --output-dir D:\logs   # default: ./sessions
cs2-input-recorder.exe --analog-optional      # continue without the Wooting SDK (samples flagged)
```

State transitions are logged to the console; nothing is logged per-sample.
Ctrl+C (or tray → Stop recorder) flushes, finalizes and exits cleanly.

### Tray icon

- **Red dot** — waiting for `cs2.exe`
- **Green dot** — CS2 detected, recording
- **Left- or right-click → "Stop recorder"** — ends the current session
  cleanly (flush + finalize) and exits. Same path as Ctrl+C.

The tray lives on its own thread and communicates with the sampler through
two atomics only (one store per state change, one relaxed load per tick):
measured impact on the hot loop: none.

### Running without a terminal window

Windows 11 throttles timer precision for *windowless* processes (a process
with no window at all — e.g. agent-spawned — gets coalesced to ~1.5 ms ticks
regardless of API calls). A window that exists but is hidden is enough to
keep full precision. `start-recorder-hidden.vbs` launches the recorder that
way — measured 1013 µs mean. A desktop shortcut `CS2 Recorder` is
preconfigured to use it. To stop: tray icon → Stop recorder, or Task
Manager → end `cs2-input-recorder.exe`.

## The `.csi` format

Versioned, little-endian, fixed-size; trivially decodable.

### Header — 96 bytes

| Offset | Type | Field |
|-------:|------|-------|
| 0  | `[u8;8]` | magic `CS2INP01` |
| 8  | `u16` | format version (1) |
| 10 | `u16` | header size (96) |
| 12 | `u16` | sample rate Hz |
| 14 | `u16` | reserved |
| 16 | `u64` | QPC frequency |
| 24 | `u64` | session-start QPC |
| 32 | `u64` | session-start unix ms (UTC) |
| 40 | `u32` | cs2 PID (0 = none) |
| 44 | `u32` | Wooting device id (0 = unknown) |
| 48 | `u16` | enabled key mask |
| 50 | `u16` | status/config flags (bit0 = forced mode) |
| 52 | `u32` | recorder version (major<<16 \| minor<<8 \| patch) |
| 56 | `[u8;40]` | reserved |

### Sample — 16 bytes, little-endian

| Offset | Type | Field |
|-------:|------|-------|
| 0  | `u32` | `dt_us` — actual µs since previous sample (the truth, never a fake catch-up) |
| 4  | `u16` | W analog (0..65535; 65535 = 1.0) |
| 6  | `u16` | A |
| 8  | `u16` | S |
| 10 | `u16` | D |
| 12 | `u16` | digital mask |
| 14 | `u16` | status flags |

Digital mask bits: 0 W · 1 A · 2 S · 3 D · 4 Space · 5 LCtrl · 6 LShift · 7 Mouse1 · 8 Mouse2.

Status flags: bit0 analog-valid · bit1 timer-late · bit2 sdk-error.

At ~1 kHz: 16 KB/s ≈ **57.6 MB/hour** of raw payload. No compression (deliberate).

Crash recovery: sessions are written to `<timestamp>.csi.part` and renamed to
`.csi` on clean close. A killed session leaves the `.part` file, which
`csi-decode` reads fine — all complete 16-byte samples before the cut are valid.

## Decoder usage

```bash
csi-decode.exe inspect session.csi          # header, duration, interval stats, transitions
csi-decode.exe export-csv session.csi --start 10 --duration 5 --out window.csv
```

`export-csv` refuses windows over 60 s — CSV is a small-window debugging tool,
never an export path for whole sessions.

Columns: `time_ms,w,a,s,d,w_down,a_down,s_down,d_down,space,ctrl,shift,mouse1,mouse2,status`

## How the SDK is integrated

The recorder binds the **official** Wooting Analog SDK v0.9.1
(`WootingKb/wooting-analog-sdk`), consuming the `wooting_analog_sdk_dist.dll`
redistributable from the release zip. There is no official Rust wrapper yet
("coming soon" per the SDK docs; the old `wooting-sdk` crate is abandoned
since 2020), so the project hand-binds seven C-ABI functions
(`initialise`, `is_initialised`, `uninitialise`, `version_semver`,
`get_connected_devices_info`, `set_keycode_mode`, `read_full_buffer`).

The DLL is loaded at **runtime** via `LoadLibraryW` + `GetProcAddress`, so a
missing/mismatched SDK degrades to flagged digital-only recording instead of
blocking startup. One full-buffer `read_full_buffer` call per tick returns all
pressed keys; held analog state for W/A/S/D is maintained locally (the SDK
reports a released key as 0.0 once). Keycode mode is `VirtualKey` so analog
and `GetAsyncKeyState` identities match 1:1.

Note: the dist DLL first tries to delegate to a system-wide Analog SDK install
and prints `failed to delagate to system dll: DLLNotFound` if absent (the
v0.9.1 MSI would install it). This is **cosmetic** — the dist handles the
calls itself and analog capture is unaffected (verified: 100% analog-valid
samples across all sessions).

## Timer architecture

`QueryPerformanceCounter` is the event clock. Ticks come from a
high-resolution **automatic-reset waitable timer** (`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`,
1 ms period, armed once per session; `SetWaitableTimerEx` with zero tolerable
delay defeats coalescing). Three Win11-specific stabilizers are active:

1. `timeBeginPeriod(1)` for the recorder's lifetime — Win11's effective timer
   period tracks the system resolution, which other processes can silently lower.
2. `SetProcessInformation(ProcessPowerThrottling)` with throttling disabled —
   otherwise Windows 11 EcoQoS burst-coalesces background 1 ms timers to ~1.5 ms.
3. The same call opts out of `IGNORE_TIMER_RESOLUTION` (Win11 24H2), which
   otherwise lets the OS silently ignore timer-resolution requests from
   background processes.

Known limitation: a process with **no window at all** (headless agent parent)
is still coalesced to ~1.5 ms by Windows regardless of the above. Any launch
path that creates a window — normal console, or the hidden VBS launcher —
measures 1013–1041 µs mean. Whatever the effective rate, every sample stores
its true `dt_us`; late wakes are flagged, never papered over with synthetic
samples.

## Benchmarks (this machine: 16 logical cores, Win11, Wooting 80HE)

| Session | Duration | Mean interval | p99 | Late | CPU |
|---------|---------:|--------------:|----:|-----:|----:|
| 30 s forced (Test A) | 30 s | 1021 µs | 1069 µs | 0 | 0.12% of total |
| 30 min forced (Test B) | 30 min | 1021 µs | 1069 µs | 0.003% | 0.12% of total |
| 2 h auto, CS2 + 4 matches (Test D) | 1h 57m | 1057 µs | 1834 µs | 1.6% | ~0.12% of total |

Other: working set 7.6 MB · disk ≈ 57.6 MB/h · binaries ~750 KB each ·
SDK errors 0 in every session.

The Test D late-sample uptick is expected: CS2's own scheduling competes for
timer slots. Analysis is unaffected (true `dt_us` per sample).

## Validation history

All Phase 1 acceptance criteria passed (2026-09-01):

- **Test A (desktop):** analog curves sane; digital-on at ~6–7% analog depth
  (that constant is what Phase 3 uses); overlaps, diagonals, slow/fast
  depressions all captured.
- **Test B (performance):** 30 min run, 0.12% total CPU, 7.6 MB, 0.003% late.
- **Test C (lifecycle):** auto-attach on CS2 start, auto-stop on exit,
  files finalized, 0 SDK errors across a 2 h session.
- **Test D (matches):** 4 Valve Competitive matches recorded continuously
  (6.64 M samples, 100% analog-valid) and aligned with their demos.

## Analysis pipeline (Phase 2 + 3)

`phase2/` contains the offline tooling. Run from `phase2/`:

```bash
python -m unittest test_analysis -q
python align.py                              # CSI + demos → alignment.json
python extract_shots.py                      # demos + CSI → shots.parquet
python extract_shots.py --reclassify         # redo match/classes from parquet+CSI
python extract_shots.py --report-only        # regenerate PHASE3_REPORT.md
```

`--steamid`, `--csi`, `--demo` / `--replays` / `--demo-glob`, `--alignment`,
`--output-dir` override the hard-coded 2026-09-01 defaults. Alignment is
keyed by **demo filename** (schema 2), not map name.

1. **Alignment** (`align.py` → `alignment.json`, `ALIGNMENT_REPORT.md`):
   group user `weapon_fire` and CSI Mouse1 edges into 1.5 s bursts; search
   a single offset per demo (one-to-one burst match). Four-match set:
   mirage 39/52, cache 3/6, inferno **27/27**, dust2 58/78.
2. **Shot extraction** (`extract_shots.py` → `shots.parquet`): every gun shot
   mapped to 1-tick demo speed at fire (native `velocity_*` is a check
   column only — sparse fire-tick queries return NaN/garbage).
3. **Classification** (`first_bullets_classified.csv`): first bullets =
   `shots_fired==1`; rifle accuracy = speed < 34% of weapon max. Input-cause
   uses **one-to-one monotonic** Mouse1 matching and timestamped WASD
   events; classes only when `|classification residual| ≤ 30 ms`. Cache is
   excluded. No leftover `RELEASE_ONLY` bin.

Corrected first-dataset numbers (4 matches, 2026-09-01; **do not use the
old 86.4% / 50% RELEASE_ONLY headlines**):

- First bullets: 393 (`shots_fired==1`) vs 184 under the old 1 s gap
- All-posture rifle first-bullet accurate (AK/M4/Galil/FAMAS):
  **115/170 = 67.6%**
- Standing, on-ground (primary counter-strafe headline):
  **92/145 = 63.4%** (crouched 22/24 = 91.7%)
- Input-cause trusted on **36/393** first bullets after the 30 ms gate and
  Cache skip (one-to-one match; nearest-edge used to report 58/393). Among
  those 36: 33 `LATERAL_COUNTER`, 1 `DIAGONAL_COUNTER`, 1 held, 1 stationary.
  Too few to change training.
- Full report: `phase2/PHASE3_REPORT.md`

## Future improvements

Deliberately deferred — each is triggered by evidence, not by speculation:

| Item | Trigger / precondition | Notes |
|------|------------------------|-------|
| Auto-start at login | User convenience; one Task Scheduler entry | No code changes needed |
| Diagonal counter classifier (WA→SD etc.) | More matches **and** tighter CSI↔demo residual | `DIAGONAL_COUNTER` exists (WA→SD / WD→SA); 1 trusted example in this dataset |
| Rolling cross-match stats (brief §21) | ~10+ matches | Only useful once per-match analysis proves stable |
| Raw Input keyboard backend | Evidence of missed digital transitions in real data | `GetAsyncKeyState` has never missed one so far |
| Busy-wait timer hybrid (exact 1000.0 Hz) | Only if sub-sample timing ever matters | Costs ~1–2% CPU; `dt_us` already makes it unnecessary |
| Demo auto-download (brief §22 / Phase 4) | Only if the metrics change training decisions | Explicitly out of scope for now |

## Known issues / small things

- **Effective sample rate is ~960–980 Hz**, not 1000.0: the Windows scheduler
  timer grid rounds the 1 ms period up slightly. Analytically irrelevant
  (true `dt_us` per sample), cosmetically noted here.
- **Late samples during CS2** (~1.6% in Test D, all flagged): scheduling
  pressure from the game itself. No data loss; flagged per sample.
- **Console prints `failed to delagate to system dll: DLLNotFound`** at
  startup (sic — typo is in the SDK's own message): the dist DLL finds no
  system-wide Analog SDK and falls back to handling calls itself. Harmless;
  installing `wooting_analog_sdk_v0.9.1.msi` would silence it. Analog capture
  works either way (verified).
- **Tray icon is hand-drawn GDI** (16×16 filled dot). Works; no icon asset.
- **Stop via tray from the hidden launcher**: session finalizes cleanly;
  the VBS launcher itself exits immediately (recorder is a detached process).
- **Killed sessions leave `.csi.part`** — intentional; the file decodes fully
  up to the cut. Delete stray `.part` files whenever.
- **`--duration` and `--force` interplay**: `--force --duration N` records
  exactly N seconds then exits; without `--duration` a forced session runs
  until Ctrl+C/tray stop.

## Dependencies

Rust runtime: `windows` 0.58 (feature-gated), `clap` 4, `anyhow` 1, `ctrlc` 3.
Dev: `tempfile`. No async runtime, no serde, no compression — by design.
Python analyzer: `demoparser2`, `pandas` (+ `numpy`, `pyarrow` transitively).

## Not implemented (on purpose)

GUI, overlay, web service, FACEIT demo download, demo parser in Rust,
counter-strafe live scoring, database, cloud, game-memory access, injection
(any form), drivers, input modification/synthesis, macros, Raw Input backend,
compression. The offline demo parsing lives in `phase2/` as Python by design
(performance doesn't matter offline; the recorder stays dependency-light).
