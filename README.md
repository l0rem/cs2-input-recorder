# cs2-input-recorder

Passive, read-only keyboard/mouse telemetry recorder for Counter-Strike 2
counter-strafe analysis. Runs beside the game, samples Wooting analog
W/A/S/D plus OS-visible digital keys at 1 kHz into a compact binary file,
and follows the CS2 process lifetime automatically.

**The recorder is telemetry only.** It never reads game memory, injects,
hooks, modifies or generates input, or touches the network.

## Components

| Binary | Purpose |
|---|---|
| `cs2-input-recorder.exe` | The recorder: waits for `cs2.exe`, samples, writes `.csi` |
| `csi-decode.exe` | Offline decoder: `inspect` + bounded `export-csv` |

## Build

Requirements: Windows 10/11, [Rust](https://rustup.rs) (MSVC toolchain),
VS Build Tools with the C++ workload, and the
[Wooting Analog SDK v0.9.1](https://github.com/WootingKb/wooting-analog-sdk/releases)
(the keyboard's system SDK, installed via the Wooting dashboard).

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
cs2-input-recorder.exe --analog-optional      # keep running without the Wooting SDK (samples flagged)
```

State transitions are logged to the console; nothing is logged per-sample.
Ctrl+C flushes, finalizes and exits cleanly.

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

At 1 kHz: 16 KB/s ≈ **57.6 MB/hour** of raw payload. No compression (deliberate).

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

## Timer architecture

`QueryPerformanceCounter` is the event clock. Ticks come from a
high-resolution **automatic-reset waitable timer** (`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`,
1 ms period, armed once per session; `SetWaitableTimerEx` with zero tolerable
delay defeats coalescing). Two Win11-specific stabilizers are active and
documented:

1. `timeBeginPeriod(1)` for the recorder's lifetime — Win11's effective timer
   period tracks the system resolution, which other processes can silently lower.
2. `SetProcessInformation(ProcessPowerThrottling)` with throttling disabled —
   otherwise Windows 11 EcoQoS burst-coalesces background 1 ms timers to ~1.5 ms.

Every sample stores the true measured `dt_us`; late wakes are flagged, never
papered over with synthetic samples.

## Benchmarks (this machine: 16 logical cores, Win11, Wooting 80HE)

Release build, 30 s forced session:

| Metric | Value |
|--------|-------|
| CPU | 1.98 % of one core = **0.12 % of total machine CPU** |
| Working set | 7.56 MB (peak 7.56 MB) |
| Disk payload | 16 KB/s ≈ 57.6 MB/h + 96-byte header |
| Mean interval | 1025 µs |
| p95 | 1043 µs |
| p99 | 1102 µs |
| Max | 6.8 ms (background burst; flagged `timer-late`) |
| Binary size | ~750 KB per exe |
| SDK errors | 0 |

## Phase 1 validation sequence

- **Test A (desktop):** `--force --duration 30`; perform A, D, A+D overlap,
  W+A, W+D, slow/fast depressions, Mouse1; `export-csv --start 10 --duration 5`
  and eyeball the analog curves + where digital transitions fire.
- **Test B (performance):** 20–30 min release run; Task Manager CPU/RAM/disk;
  `inspect` for internal interval stats.
- **Test C (CS2 offline):** launch recorder, then CS2; verify auto-start,
  no FPS impact, auto-stop on exit, file readable.
- **Test D (Valve matchmaking):** record several Competitive matches on the
  same map, download the `.dem`s — the Phase 2 dataset. Stop here.

## Dependencies

Runtime: `windows` 0.58 (feature-gated), `clap` 4, `anyhow` 1, `ctrlc` 3.
Dev: `tempfile`. No async runtime, no serde, no compression — by design.

## Not implemented (on purpose)

GUI, overlay, web service, FACEIT demo download, demo parser, counter-strafe
scoring, database, cloud, game-memory access, injection (any form), drivers,
input modification/synthesis, macros, Raw Input backend, compression.
