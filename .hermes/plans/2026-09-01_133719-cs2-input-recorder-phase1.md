# Plan: Phase 1 — CS2 Input Telemetry Recorder (`cs2-input-recorder`)

## Goal

A passive, read-only Rust console program that runs beside CS2, samples Wooting analog W/A/S/D plus digital W/A/S/D/Space/Ctrl/Shift/Mouse1/Mouse2 at 1 kHz into a 16-byte-per-sample binary `.csi` file, auto-starts/stops with `cs2.exe`, and ships with an offline `inspect`/`export-csv` decoder and tests — nothing else.

## Current context / assumptions

- **Workspace** `C:\Users\lorem\Desktop\strafes` is empty (verified). All work happens there. Git repo: run `git init` first.
- **Rust is NOT installed** on this machine (verified: no `rustc`/`cargo` on PATH). **Task 0 installs it.**
- **Wooting Analog SDK is NOT installed** here (no Wooting dirs, `winget list` empty — verified). The user's Wooting keyboard with the Analog SDK is on the gaming machine; the SDK redistributable ships a system-wide install via the Wooting dashboard. Plan handles both: SDK present → analog works; absent → recorder must fail loudly or degrade with flags, never fake analog data.
- **Wooting SDK research (done, do not redo):**
  - Official repo: `WootingKb/wooting-analog-sdk`, latest release **v0.9.1** (2026-03-25). This is the current, maintained SDK — not abandoned.
  - The old `wooting-sdk` crate on crates.io is **dead** (last publish 2020-06-19, v0.1.1) and is API-incompatible (`read_analog_key` returns u8, no `read_full_buffer` in its analog module). **Do NOT use it.**
  - Official docs say: "The updated Rust crate is coming soon!" — there is **no current official Rust wrapper**. The supported consumption path is the C ABI of `wooting_analog_sdk_dist.dll` shipped in the release archive. Therefore: bind the C ABI directly with `#[link(name = "wooting_analog_sdk_dist")]`, ~5 functions, tiny hand-written `unsafe` module. This is more boring and auditable than any wrapper.
  - C ABI (verified from official `docs/SDK_USAGE.md` on develop branch):
    - `WootingAnalogResult wooting_analog_initialise(void)` → `>=0` = device count, `<0` = error enum
    - `bool wooting_analog_is_initialised(void)`
    - `WootingAnalogResult wooting_analog_uninitialise(void)` → 0 = OK
    - `int wooting_analog_get_connected_devices_info(WootingAnalog_DeviceInfo_FFI **buffer, unsigned int len)` → `>=0` count
    - `WootingAnalogResult wooting_analog_set_keycode_mode(WootingAnalog_KeycodeType mode)` → 0 = OK
    - `int wooting_analog_read_full_buffer(unsigned short *code_buffer, float *analog_buffer, unsigned int len)` → `>=0` pairs filled; released keys reported once with 0.0f; `<0` = error
    - `float wooting_analog_read_analog(unsigned short code)` → `0.0..1.0`, `<0` = error enum
  - Error enum values (from `wooting-analog-plugin/src`): Ok=0, UnInitialized=-1, NoDevices=-2, FunctionNotFound=-3, NoPlugins=-4, DLLNotFound=-5, IncompatibleVersion=-6, InvalidArgument=-7, NotAvailable=-8, NoMapping=-9, TOutOfDate=-10, Success=1 (also used as ok-ish sentinel). We only need to *name* these in a `pub const` block for diagnostics; exact mapping is asserted by a test.
  - Keycode mode: use **`VirtualKey` (value 2)** — simplest, and matches what `GetAsyncKeyState` uses, so analog and digital columns share one key identity. (`VirtualKeyTranslate`=3 exists on Windows but we don't need layout translation; user plays on standard WASD.) Values: HID=0, ScanCode1=1, VirtualKey=2, VirtualKeyTranslate=3.
  - SDK distributor loads the system SDK at runtime; our app dynamically links `wooting_analog_sdk_dist` and must ship/place that DLL next to the exe.
- **Digital sampling**: `GetAsyncKeyState(VK)` per key (9 keys) per tick — read the low bit via `(state & 0x8000u16) != 0`. Deliberately NOT Raw Input (brief §7).
- **Timing**: `QueryPerformanceCounter`/`Frequency` for timestamps; wait on a **high-resolution waitable timer** (`CreateWaitableTimerExW` with `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION` (0x2), 1 ms period) — available Win10 1803+; fallback documented: `timeBeginPeriod(1)` + `Sleep(1)` only if `CreateWaitableTimerExW` with the flag fails (report which path is active at startup).
- **CS2 detection**: poll `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)` for a process named `cs2.exe` every 1 s while idle. Do not open handles to it.
- **Toolchain on this host**: git-bash on Windows; use `C:/Users/...`-style paths for native tools. `$LOCALAPPDATA/Temp` for scratch.

## Architecture

Three small crates in one workspace, zero async, zero threads beyond what the SDK spawns internally:

```
strafes/
├── Cargo.toml            # workspace
├── README.md
├── wooting-analog-sdk/   # dist SDK downloaded by Task 1 (DLL + headers) — NOT in git
├── recorder/
│   ├── Cargo.toml
│   └── src/
│       ├── main.rs       # arg parsing, supervisor loop, console state transitions
│       ├── wooting.rs    # unsafe FFI to wooting_analog_sdk_dist (5 fns), device discovery
│       ├── keys.rs       # GetAsyncKeyState sampling -> DigitalMask
│       ├── timer.rs      # QPC + high-res waitable timer, dt measurement, late-wake handling
│       ├── csi.rs        # header + 16-byte sample encode/decode, pure & testable
│       ├── session.rs    # buffered writer, .part->.csi rename, flush/sync, finalize
│       └── bench.rs      # internal interval statistics (used by tests & the CLI)
├── csi-decode/           # decoder: thin bin over the same csi.rs (path dependency)
│   ├── Cargo.toml
│   └── src/main.rs       # `inspect` + `export-csv` subcommands
└── tests/                # (inside each crate's #[cfg(test)]; no separate crate)
```

Data flow per 1 ms tick (single thread, no allocations in loop): wait timer → QPC now → compute `dt_us` → read digital mask → read analog via `read_full_buffer` → update held W/A/S/D analog values (released keys→0) → quantize to u16 → append 16-byte record to `Vec` reused as buffer → when buffer ≥ 1 s worth (1000 records), write to `BufWriter` and flush. Session ends on CS2 exit/Ctrl+C/duration: flush + `sync_all` + rename `.part`→`.csi`.

## Step-by-step tasks

### Task 0 — Toolchain + repo bootstrap (~5 min)

```bash
winget install --id Rustlang.Rustup -e   # then in a NEW terminal: rustup default stable-x86_64-pc-windows-msvc
```
(If `winget` lacks it: download https://win.rustup.rs/x86_64 and run `rustup-init.exe -y --default-toolchain stable-x86_64-pc-windows-msvc`.) MSVC Build Tools are required if not present: `winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--add Microsoft.VisualStudio.Workload.VCTools --passive"`.

Verify:
```bash
rustc -V   # expect: rustc 1.8x.x
cargo -V
git init C:/Users/lorem/Desktop/strafes && cd C:/Users/lorem/Desktop/strafes
git commit --allow-empty -m "chore: empty repo"
```

### Task 1 — Fetch official Wooting Analog SDK redistributable (~5 min)

Download the Windows x64 SDK zip from the latest release (v0.9.1) and extract just the dist library:

```bash
mkdir -p wooting-analog-sdk && cd wooting-analog-sdk
curl -sL -o sdk.zip https://github.com/WootingKb/wooting-analog-sdk/releases/download/v0.9.1/wooting-analog-sdk-v0.9.1-windows-x64.zip
unzip -o sdk.zip && ls -R
```
Expected: a `wooting_analog_sdk_dist.dll` (name may be `wooting_analog_sdk.dll`/`dist` variant — record the exact filename found) and headers/`.h` files. Copy the exact dll filename into `wooting.rs`'s link name. Add `wooting-analog-sdk/` to `.gitignore` (binary blob, re-downloadable).

Verify: the extracted tree contains the dist DLL. If the release asset layout differs, adapt the URL from the GitHub releases page and note the filename — do NOT hand-derive it.

### Task 2 — Workspace + crate skeletons (~5 min)

`Cargo.toml` (workspace root):
```toml
[workspace]
resolver = "2"
members = ["recorder", "csi-decode"]

[workspace.dependencies]
windows = { version = "0.58", features = [
  "Win32_Foundation", "Win32_System_Threading", "Win32_UI_Input_KeyboardAndMouse",
  "Win32_System_Diagnostics_ToolHelp", "Win32_System_Time", "Win32_System_SystemServices",
] }
clap = { version = "4", features = ["derive"] }
anyhow = "1"
```

`recorder/Cargo.toml`:
```toml
[package]
name = "cs2-input-recorder"
version = "0.1.0"
edition = "2021"

[dependencies]
windows.workspace = true
clap.workspace = true
anyhow.workspace = true

[dev-dependencies]
tempfile = "3"

[profile.release]
lto = "thin"
codegen-units = 1
```

`csi-decode/Cargo.toml`:
```toml
[package]
name = "csi-decode"
version = "0.1.0"
edition = "2021"

[dependencies]
clap.workspace = true
anyhow.workspace = true
csi = { path = "../recorder" }   # recorder exposes lib target re-exporting csi.rs
```
In `recorder/Cargo.toml` add `[lib] name = "csi" path = "src/lib.rs"` and create `recorder/src/lib.rs`:
```rust
pub mod csi;
pub mod keys;
pub mod timer;
```
(`wooting.rs` and `session.rs` stay bin-only, since they touch the SDK/FS; `csi.rs`, `keys.rs`, `timer.rs` are pure or thin enough to unit-test.)

Verify:
```bash
cargo check   # expect: passes, downloads windows crate
```
Commit: `git add -A && git commit -m "chore: workspace scaffold"`.

### Task 3 — `.csi` binary format, TDD (~20 min, most important task)

Write tests FIRST in `recorder/src/csi.rs` `#[cfg(test)] mod tests`, then implement.

Constants and structs:
```rust
pub const MAGIC: &[u8; 8] = b"CS2INP01";
pub const FORMAT_VERSION: u16 = 1;

pub const BIT_W: u16 = 1 << 0;
pub const BIT_A: u16 = 1 << 1;
pub const BIT_S: u16 = 1 << 2;
pub const BIT_D: u16 = 1 << 3;
pub const BIT_SPACE: u16 = 1 << 4;
pub const BIT_CTRL: u16 = 1 << 5;
pub const BIT_SHIFT: u16 = 1 << 6;
pub const BIT_MOUSE1: u16 = 1 << 7;
pub const BIT_MOUSE2: u16 = 1 << 8;

pub const FLAG_ANALOG_VALID: u16 = 1 << 0;
pub const FLAG_TIMER_LATE: u16 = 1 << 1;
pub const FLAG_SDK_ERROR: u16 = 1 << 2;

#[repr(C, packed)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Sample {
    pub dt_us: u32,      // actual elapsed since previous sample
    pub w: u16,          // 0..=65535, 65535 == 1.0
    pub a: u16,
    pub s: u16,
    pub d: u16,
    pub digital_mask: u16,
    pub status_flags: u16,
} // exactly 16 bytes — asserted by a test

#[repr(C, packed)]
pub struct Header {
    pub magic: [u8; 8],          // "CS2INP01"
    pub format_version: u16,
    pub header_size: u16,        // bytes, = 96 initially (forward-compat: readers stop at header_size)
    pub sample_rate_hz: u16,
    pub reserved0: u16,
    pub qpc_frequency: u64,
    pub session_start_qpc: u64,
    pub utc_start_unix_ms: u64,
    pub cs2_pid: u32,            // 0 = none
    pub wooting_device_id: u32,  // 0 = unknown
    pub enabled_key_mask: u16,   // which digital bits are sampled
    pub status_config_flags: u16,// e.g. forced-mode bit
    pub recorder_version: u32,   // crate semver packed as (major<<16)|(minor<<8)|patch
    pub reserved: [u8; 24],      // future expansion
} // exactly 96 bytes — asserted by a test
```

API to implement:
```rust
pub const fn quantize_analog(f: f32) -> u16;    // clamp 0.0..1.0, f*65535.0 rounded, NaN->0
pub const fn dequantize_analog(q: u16) -> f32;  // q as f32 / 65535.0
pub fn encode_sample(s: &Sample) -> [u8; 16];   // explicit little-endian, no serde
pub fn decode_sample(b: &[u8; 16]) -> Sample;
pub fn write_header(w: &mut impl std::io::Write, h: &Header) -> std::io::Result<()>;
pub fn read_header(r: &mut impl std::io::Read) -> Result<Header, CsiError>;
pub enum CsiError { BadMagic, UnsupportedVersion(u16), Io(std::io::Error), Truncated }
```
LE encoding: manual `to_le_bytes()` per field — no serde/bincode in the hot path.

Required tests (write before impl, run `cargo test -p csi` expecting compile fail, then impl, then pass):
1. `sample_size_is_16` — `assert_eq!(std::mem::size_of::<Sample>(), 16)`
2. `header_size_is_96`
3. `header_roundtrip` — write_header→read_header, all fields equal
4. `sample_roundtrip` — random-ish sample, encode→decode, fields equal
5. `sample_known_bytes` — a hand-written 16-byte literal compared to `encode_sample` of a known sample (locks the on-disk layout forever)
6. `version_rejected` — header with version=999 → `CsiError::UnsupportedVersion(999)`
7. `truncated_file` — header claims 10 samples, file has 5.5 → decoder returns `Truncated` (decode-side helper `read_samples_count()` returns count actually present + flag)
8. `quantize_edges` — 0.0→0, 1.0→65535, 0.5→32768 (round: (0.5*65535.0).round()=32768), -0.1→0, 1.5→65535, NaN→0
9. `digital_bits_set_unset` — set/clear every BIT_* const, assert mask math
10. `multiple_keys_mask` — BIT_W|BIT_MOUSE1 == 0x81

Verify:
```bash
cargo test -p csi   # expect: all pass, 0 failed
```
Commit: `git commit -am "feat(csi): binary format + quantization + tests"`.

### Task 4 — Timer module, TDD (~10 min)

`recorder/src/timer.rs`. Tests first:
```rust
#[test] fn dt_math_no_underflow() { assert_eq!(elapsed_us(1000, 1999, 1_000_000), 999); }
#[test] fn qpc_wrap_not_expected_but_handled() { /* elapsed_us uses wrapping_sub on raw counts */ }
#[test] fn late_wake_flags() { // classify: dt_us > 2 * expected_us => late
    assert!(is_late(5001, 1000)); assert!(!is_late(1004, 1000)); }
```
Implementation (unsafe Win32, commented per brief §24):
```rust
pub struct QpcClock { freq: u64 }
impl QpcClock {
    pub fn new() -> Self { unsafe { QueryPerformanceFrequency(&mut f); ... } }
    pub fn now(&self) -> u64 { unsafe { QueryPerformanceCounter(&mut c); c } }
}
pub fn elapsed_us(prev_qpc: u64, now_qpc: u64, freq: u64) -> u64 {
    now_qpc.wrapping_sub(prev_qpc) * 1_000_000 / freq   // freq < ~2^20 so no overflow
}
pub fn is_late(dt_us: u64, expected_us: u64) -> bool { dt_us > expected_us * 2 }
```
Waitable timer (win32): `CreateWaitableTimerExW(null, null, CREATE_WAITABLE_TIMER_MANUAL_RESET | 0x2 /*HIGH_RESOLUTION*/, TIMER_ALL_ACCESS)`; per tick `SetWaitableTimer(h, &li_due(-1 /*relative*/), 0, ..)` then `WaitForSingleObject(h, INFINITE)`. Fallback path (only if creation fails): `timeBeginPeriod(1)` + `Sleep(1)`, and print which timer mode is active at startup. Encapsulate both behind `struct TickTimer { handle: isize, high_res: bool }` with `fn wait_next(&mut self)`.

No unit test for the Win32 wait itself (brief §13: don't unit-test Windows); the math above is what's tested.

Verify: `cargo test -p csi` (module compiles under lib) — expect pass. Commit: `feat(timer): qpc + high-res waitable timer`.

### Task 5 — Digital keys module, TDD (~10 min)

`recorder/src/keys.rs`:
```rust
pub const VK_W: u16 = 0x57; // 'W'
pub const VK_A: u16 = 0x41;
pub const VK_S: u16 = 0x53;
pub const VK_D: u16 = 0x44;
pub const VK_SPACE: u16 = 0x20;
pub const VK_LCTRL: u16 = 0xA2;
pub const VK_LSHIFT: u16 = 0xA0;
pub const VK_LBUTTON: u16 = 0x01;
pub const VK_RBUTTON: u16 = 0x02;

#[inline]
pub fn sample_digital() -> u16 { // GetAsyncKeyState per VK, bit set iff high bit (& 0x8000) set
    let s = |vk: u16| -> bool { unsafe { GetAsyncKeyState(vk as i32) as u16 & 0x8000 != 0 } };
    (s(VK_W) as u16) << 0 | (s(VK_A) as u16) << 1 | (s(VK_S) as u16) << 2 | (s(VK_D) as u16) << 3
        | (s(VK_SPACE) as u16) << 4 | (s(VK_LCTRL) as u16) << 5 | (s(VK_LSHIFT) as u16) << 6
        | (s(VK_LBUTTON) as u16) << 7 | (s(VK_RBUTTON) as u16) << 8
}
```
Tests (mask-assembly logic only, not the Win32 call — factor the bit-packing into `fn pack_mask(w,a,s,d,sp,ct,sh,m1,m2) -> u16` which is testable):
- all 9 individual keys → correct single bit
- W+Mouse1 together → 0x81
- none → 0

Verify: `cargo test -p csi`. Commit: `feat(keys): digital sampling mask`.

### Task 6 — Wooting FFI module (~15 min)

`recorder/src/wooting.rs`. No unit tests possible without hardware/SDK (and none without DLL) — keep this module dead-simple and exercised in Task 8 manually.

```rust
// Link the redistributable. Exact DLL filename from Task 1 goes here.
#[link(name = "wooting_analog_sdk_dist")]
extern "C" {
    fn wooting_analog_initialise() -> i32;
    fn wooting_analog_is_initialised() -> bool;
    fn wooting_analog_uninitialise() -> i32;
    fn wooting_analog_get_connected_devices_info(
        buffer: *mut *mut DeviceInfoFfi, len: u32) -> i32;
    fn wooting_analog_set_keycode_mode(mode: u32) -> i32;
    fn wooting_analog_read_full_buffer(
        code_buffer: *mut u16, analog_buffer: *mut f32, len: u32) -> i32;
}
#[repr(C)] pub struct DeviceInfoFfi { /* device_id: *const c_void, ... per SDK header; only need vendor/product/device_id — copy from the SDK's wooting-analog-plugin header shipped in the release zip */ }

pub const KEYCODE_MODE_VIRTUAL_KEY: u32 = 2;
pub const ERR_NO_PLUGINS: i32 = -4;
pub const ERR_DLL_NOT_FOUND: i32 = -5;

pub struct Wooting { codes: [u16; 16], analogs: [f32; 16], device_name: String }
impl Wooting {
    pub fn init() -> Result<Self, InitError>; // returns reason string, NO silent success
    pub fn device_name(&self) -> &str;
    /// Returns analog values [w,a,s,d] in 0..1; released keys -> 0.0.
    /// read_full_buffer returns only PRESSED keys (+ one 0.0 report on release),
    /// so maintain previous state here: start all 0, update from returned pairs.
    pub fn read_wasda(&mut self) -> Result<[f32; 4], i32>;
    pub fn recover(&mut self) -> bool; // re-init attempt for mid-session dropout
}
```
`read_wasda` implementation sketch (no allocation, fixed buffers, reused across calls):
```rust
pub fn read_wasda(&mut self) -> Result<[f32; 4], i32> {
    unsafe {
        let n = wooting_analog_read_full_buffer(self.codes.as_mut_ptr(),
                                                self.analogs.as_mut_ptr(), 16);
        if n < 0 { return Err(n); }
        for i in 0..n as usize {
            match self.codes[i] {
                VK_W => self.w = self.analogs[i], VK_A => self.a = self.analogs[i],
                VK_S => self.s = self.analogs[i], VK_D => self.d = self.analogs[i],
                _ => {}
            }
        }
    }
    Ok([self.w, self.a, self.s, self.d])
}
```
Startup print (per brief §6):
```
Wooting device found: Wooting 80HE (id 0x...)
Analog SDK: wooting_analog_sdk_dist v0.9.1
Sample rate: 1000 Hz
```
On init failure print the named error (e.g. `Analog SDK failed: NoPlugins (-4) — install the Wooting Analog SDK / dashboard`) and (CLI flag `--analog optional`) either abort or continue digital-only with `FLAG_ANALOG_VALID` cleared. Default: abort (never silently pretend analog works).

Verify: `cargo build -p cs2-input-recorder` compiles (link may fail if DLL/lib absent — see Build step below: the dist dll must sit next to the built exe; a `.dll` copy step is part of Task 8's build script). Commit: `feat(wooting): c-abi bindings to official analog sdk dist`.

### Task 7 — Session writer + supervisor loop (~20 min)

`recorder/src/session.rs`:
```rust
pub struct Session {
    file: BufWriter<File>,       // 8 MB capacity
    path_final: PathBuf,         // .../2026-09-01_123418.csi
    path_part: PathBuf,          // same + ".part"
    samples: u64, samples_valid: u64, sdk_errors: u64,
    intervals: IntervalStats,    // running mean/count/p99-reservoir/max — small fixed struct
}
impl Session {
    pub fn start(out_dir: &Path, header: &Header) -> io::Result<Self>; // writes header, opens .part
    pub fn push(&mut self, s: &Sample) -> io::Result<()>;             // encode into buf; flush+sync every ~1 s of samples
    pub fn finalize(mut self) -> io::Result<Summary>;                 // flush, sync_all, rename .part->.csi, return stats
}
```
`.part` rename on clean close; on crash the `.part` file is simply left behind (documented in README as the recovery story: decode it with `csi-decode` — format is prefix-tolerant thanks to the truncated-file test).

`recorder/src/main.rs` supervisor (no threads):
```rust
enum State { Waiting, Recording(Session) }
loop {
  match state {
    Waiting => every 1 s: find_cs2_pid("cs2.exe") (Toolhelp32Snapshot; name-compare only; no handle open)
               if found -> Session::start(...), log "CS2 detected, PID {pid}"
    Recording => run sample loop (Task 4 timer, Task 5 keys, Task 6 wooting, Session::push)
               each tick also (cheaply, every 60 ticks) re-check cs2.exe still alive;
               gone -> finalize, print stats block, back to Waiting
               ctrl-c (SetConsoleCtrlHandler or plain ctrlc crate-free: std handling via `console` crate? NO — use `ctrlc = "3"` crate, single tiny dep, or `SetConsoleCtrlHandler` via windows crate — pick `ctrlc` crate for boring-ness) -> finalize, exit
  }
}
```
Stats block on session end (brief §11): samples, mean/p99/max interval, SDK errors, analog-unavailable %.

CLI (clap):
```
--force            record immediately, no cs2.exe needed
--duration <secs>  auto-stop after N seconds (implies --force if cs2 absent? NO: independent flag)
--hz <n>           sample rate, default 1000
--output-dir <p>   default ./sessions
--analog optional  continue without analog on init failure (flagged samples)
```

Verify:
```bash
cargo build --release
# quick smoke WITHOUT hardware-dependent analog (SDK not installed here):
./target/release/cs2-input-recorder.exe --force --duration 2 --output-dir ./tmp-session
# expect: exits cleanly; if SDK absent: prints clear init failure and (with --analog optional) records digital-only session
./target/release/csi-decode.exe inspect ./tmp-session/*.csi --expect samples ≈ 2000
```
Commit: `feat: supervisor + session writer + cli`.

### Task 8 — csi-decode binary (~10 min)

`csi-decode/src/main.rs`, clap subcommands:
```
inspect <file.csi>
  prints: header fields; duration; sample count; interval mean/p95/p99/max;
          analog-valid %; key transition counts (rising edges per bit); mouse1 transitions
export-csv <file.csi> --start <sec> --duration <sec> [--out file.csv]
  columns exactly: time_ms,w,a,s,d,w_down,a_down,s_down,d_down,space,ctrl,shift,mouse1,mouse2,status
  refuses to export >60 s (--duration 61 → error exit 2): "CSV is for small windows"
```
Analog values printed as `0.000000` floats (dequantized); digital as 0/1.

Verify:
```bash
./target/release/csi-decode.exe inspect ./tmp-session/*.csi
./target/release/csi-decode.exe export-csv ./tmp-session/*.csi --start 0 --duration 1 --out ./tmp-session/win.csv && head -3 ./tmp-session/win.csv
./target/release/csi-decode.exe export-csv ./tmp-session/*.csi --start 0 --duration 61; echo "exit=$?"  # expect exit 2
```
Commit: `feat(decode): inspect + bounded csv export`.

### Task 9 — Session lifecycle tests (~10 min)

`recorder/tests/lifecycle.rs` (integration test, Windows-only APIs are NOT tested; only the pure state logic):
- Start a Session on a tempdir, push 3000 synthetic samples with varying dt, finalize → file exists without `.part`, `inspect`-equivalent decode of the file yields 3000 samples, correct stats.
- Simulated late samples: push dt=50_000 µs sample → IntervalStats flags late, no crash, no fake catch-up samples (count stays 1).
- Truncated: write header + 10 samples, drop last 5 bytes, decode → error variant is `Truncated`, count of fully-readable samples is correct.
- Ctrl+C behavior: not unit-testable; covered manually in Task 11 validation. Document this in README.

Verify: `cargo test --workspace` — expect all green. Commit: `test: session lifecycle + truncation`.

### Task 10 — Release build + size/dep audit (~5 min)

```bash
cargo build --release
ls -la target/release/cs2-input-recorder.exe target/release/csi-decode.exe   # expect < 1 MB each
```
Record in README: exact dependency list (`windows` crate w/ feature list, `clap`, `anyhow`, `tempfile` (dev), nothing else — no tokio, no serde, no compression). Copy `wooting_analog_sdk_dist.dll` next to built exe in a `dist/` step and document that the DLL ships beside the exe.

Commit: `chore: release profile + README dep list`.

### Task 11 — Benchmarks + README (~15 min)

`recorder/src/main.rs` gains `--bench-internal <secs>`: runs the sample loop at `--hz` without CS2 wait, prints interval stats at end. Run:

```bash
./target/release/cs2-input-recorder.exe --force --duration 1800 --output-dir ./bench  # 30 min, observe Task Manager
```
Record in README under `## Benchmarks (this machine)`:
- CPU % (Task Manager, recorder process)
- Working set MB
- Disk writes (Task Manager, MB/h ≈ 57.6 MB/h expected for raw payload)
- interval mean/p95/p99/max from `inspect` of the bench session

README sections: build (incl. Rust + MSVC + SDK zip fetch), run, CLI flags table, `.csi` format spec (byte-for-byte table mirroring `csi.rs`), decoder usage, crash-recovery note (`.part` files decode fine), Phase-1 validation sequence copied from brief §14 (Test A–D), explicit "not implemented" list (mirror brief §0).

Commit: `docs: README + benchmark results`.

## Tests / validation summary

- TDD order enforced in Tasks 3–5: failing test → minimal impl → green → commit.
- `cargo test --workspace` must pass before every commit.
- Hardware-independent acceptance: Tasks 3–5, 7, 8, 9, 10 run fully on this dev machine; analog verification (Test A in brief §14) requires the gaming machine with the Wooting SDK installed — the plan flags this as the manual step, with exact commands ready.
- Manual validation sequence (user, on gaming PC): Test A desktop keystrokes → CSV eyeball; Test B 20–30 min release run → Task Manager + `inspect` stats; Test C CS2 offline → auto-start/stop; Test D Valve matches → `.csi`+`.dem` dataset for Phase 2. STOP after Phase 1.

## Risks, tradeoffs, open questions

- **No official Rust wrapper exists** for the current SDK (docs say "coming soon"); we hand-bind 6 C functions. Risk: `DeviceInfoFfi` layout must be copied from the SDK's C header shipped in the release zip — Task 6 says to take it from there, not invent it. If the zip's headers disagree with `docs/SDK_USAGE.md`, trust the header and note the discrepancy.
- **SDK not installed on this dev machine** — analog path can't be verified here. Mitigation: `--analog optional` mode exercises the full pipeline digitally; Test A on the real machine validates analog.
- **`GetAsyncKeyState` returns OS-visible state**, which for CS2 may be suppressed when the game window is unfocused (and historically games could consume input) — this is exactly what brief §7 accepts for MVP; if Test C shows gaps, the documented future fix is a Raw Input keyboard backend (Phase 1.5, explicitly out of scope).
- **Timer**: `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION` requires Win10 1803+; fallback (`timeBeginPeriod(1)`+`Sleep(1)`) changes global timer resolution — documented, only used if the flag fails, and reported in console at startup.
- **Disk math**: 16 B × 1000 Hz = 16 KB/s ≈ 57.6 MB/h + header; BufWriter at 8 MB means ~4 flushes/min — negligible.
- **Open question for the user**: is the gaming machine (where the Wooting + CS2 live) this same machine? If yes, Tasks 1 and 8 should be re-verified on it before Test A. If no, this dev machine produces the toolchain + binary, and the user runs the built exe there.
- **Not done (per brief §0)**: GUI, overlay, demo parser, counter-strafe scoring, DB, cloud, memory reading, injection, input synthesis/modification. Phase 2/3/4 are explicitly out of scope.