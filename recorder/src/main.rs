//! cs2-input-recorder — passive, read-only CS2 input telemetry recorder.
//!
//! No injection, no memory reading, no input modification. Telemetry only.

mod session;
mod wooting;

use anyhow::{Context, Result};
use clap::Parser;
use csi::keys;
use csi::timer::{elapsed_us, is_late, QpcClock, TickTimer};
use csi::{quantize_analog, FLAG_ANALOG_VALID, FLAG_SDK_ERROR, FLAG_TIMER_LATE, HEADER_SIZE};
use session::SessionSummary;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::SystemTime;
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};

#[derive(Parser, Debug)]
#[command(
    name = "cs2-input-recorder",
    version,
    about = "Passive CS2 input telemetry recorder (Phase 1)"
)]
struct Args {
    /// Record immediately even if cs2.exe is not running
    #[arg(long)]
    force: bool,

    /// Stop recording after N seconds (0 = until CS2 exits / Ctrl+C)
    #[arg(long, default_value_t = 0)]
    duration: u64,

    /// Sample rate in Hz
    #[arg(long, default_value_t = 1000)]
    hz: u32,

    /// Directory for .csi session files
    #[arg(long, default_value = "sessions")]
    output_dir: PathBuf,

    /// Continue without analog if the Wooting SDK init fails (samples flagged)
    #[arg(long)]
    analog_optional: bool,
}

fn utc_civil(secs: u64) -> (i64, u32, u32, u32, u32, u32) {
    // Howard Hinnant's civil-from-days algorithm; no chrono dependency.
    let days = (secs / 86400) as i64;
    let rem = secs % 86400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let z = days + 719_468;
    let era = z / 146_097;
    let doe = z % 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    (y as i64, mo as u32, d as u32, h as u32, m as u32, s as u32)
}

fn now_timestamp() -> String {
    let secs = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let (y, mo, d, h, m, s) = utc_civil(secs);
    format!("{y:04}-{mo:02}-{d:02}_{h:02}{m:02}{s:02}")
}

fn utc_unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}

fn log(msg: &str) {
    let secs = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let (_y, _mo, _d, h, m, s) = utc_civil(secs);
    println!("[{h:02}:{m:02}:{s:02}] {msg}");
}

/// Find a PID for an exe name (case-insensitive). 0 = not found.
/// Conservative: process-list snapshot only; never opens the process.
fn find_process_pid(name: &str) -> u32 {
    // SAFETY: snapshot handle closed below; entry initialized with dwSize
    // before each Process32 call per Win32 contract.
    unsafe {
        let snapshot = match CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) {
            Ok(h) => h,
            Err(_) => return 0,
        };
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        let mut found = 0u32;
        if Process32FirstW(snapshot, &mut entry).is_ok() {
            loop {
                let len = entry
                    .szExeFile
                    .iter()
                    .position(|&c| c == 0)
                    .unwrap_or(entry.szExeFile.len());
                let exe = String::from_utf16_lossy(&entry.szExeFile[..len]);
                if exe.eq_ignore_ascii_case(name) {
                    found = entry.th32ProcessID;
                    break;
                }
                if Process32NextW(snapshot, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = CloseHandle(snapshot);
        found
    }
}

fn make_header(clock: &QpcClock, args: &Args, cs2_pid: u32, wooting_device_id: u32) -> csi::Header {
    csi::Header {
        magic: *csi::MAGIC,
        format_version: csi::FORMAT_VERSION,
        header_size: HEADER_SIZE,
        sample_rate_hz: args.hz as u16,
        reserved0: 0,
        qpc_frequency: clock.freq(),
        session_start_qpc: clock.now(),
        utc_start_unix_ms: utc_unix_ms(),
        cs2_pid,
        wooting_device_id,
        enabled_key_mask: 0x01FF,
        status_config_flags: args.force as u16,
        recorder_version: (0u32 << 16) | (1u32 << 8) | 0u32, // v0.1.0
        reserved: [0; 40],
    }
}

fn print_summary(s: &SessionSummary) {
    println!("  samples: {}", s.samples);
    if let Some(m) = s.mean_interval_us {
        println!("  mean interval: {m:.1} us");
    }
    if let Some(p) = s.p95_interval_us {
        println!("  p95 interval: {p} us");
    }
    if let Some(p) = s.p99_interval_us {
        println!("  p99 interval: {p} us");
    }
    println!("  max interval: {} us", s.max_interval_us);
    println!("  late samples: {}", s.late_samples);
    println!("  SDK errors: {}", s.sdk_errors);
    println!(
        "  analog valid: {} / {} samples",
        s.analog_valid_samples, s.samples
    );
    println!("  file: {}", s.path.display());
}

fn finalize_and_print(s: session::Session, label: &str) -> Result<()> {
    let summary = s
        .finalize()
        .with_context(|| "failed to finalize session file")?;
    log(&format!("recording finished ({label})"));
    print_summary(&summary);
    Ok(())
}

fn main() -> Result<()> {
    let args = Args::parse();
    let expected_us = 1_000_000u64 / args.hz.max(1) as u64;

    log("recorder started");

    // --- Wooting init: never silently pretend analog works (brief §6) ---
    let mut wooting: Option<wooting::Wooting> = match wooting::Wooting::init() {
        Ok(w) => {
            let ver = wooting::Wooting::version();
            println!(
                "Wooting device found: {} (vid {:04x} pid {:04x})",
                w.device_name, w.vendor_id, w.product_id
            );
            println!("Analog SDK: v{ver} (wooting_analog_sdk_dist)");
            Some(w)
        }
        Err(e) => {
            if args.analog_optional {
                eprintln!("WARNING: analog unavailable: {}", e.message());
                eprintln!("         continuing digital-only (--analog-optional)");
                None
            } else {
                eprintln!("ERROR: analog unavailable: {}", e.message());
                eprintln!("       fix the SDK or pass --analog-optional");
                std::process::exit(2);
            }
        }
    };
    println!("Sample rate: {} Hz", args.hz);
    let timer = TickTimer::new();
    println!(
        "Timer: {}",
        if timer.is_high_res() {
            "high-resolution waitable timer"
        } else {
            "FALLBACK plain waitable timer (coarse ~15.6 ms ticks)"
        }
    );

    let running = Arc::new(AtomicBool::new(true));
    {
        let running = running.clone();
        ctrlc::set_handler(move || running.store(false, Ordering::SeqCst))
            .context("failed to install Ctrl+C handler")?;
    }

    let clock = QpcClock::new();
    let mut active: Option<session::Session> = None;
    let mut force_session_done = false;

    if args.force {
        log("recording (forced, no CS2 wait)");
        let header = make_header(&clock, &args, 0, wooting_device_id(&wooting));
        let ts = now_timestamp();
        let s = session::Session::start(&args.output_dir, &header, &ts)
            .context("failed to create session file")?;
        log(&format!("recording -> {}", ts));
        active = Some(s);
    } else {
        log("waiting for cs2.exe");
    }

    let duration_deadline = if args.duration > 0 {
        Some(std::time::Instant::now() + std::time::Duration::from_secs(args.duration))
    } else {
        None
    };

    'supervisor: loop {
        if !running.load(Ordering::SeqCst) {
            log("ctrl-c received, stopping");
            break 'supervisor;
        }
        if let Some(dl) = duration_deadline {
            if std::time::Instant::now() >= dl {
                log("duration reached, stopping");
                break 'supervisor;
            }
        }

        if let Some(s) = active.as_mut() {
            // ===================== sample loop =====================
            let mut prev_qpc = clock.now();
            let mut tick_count: u64 = 0;
            let mut exit_reason: Option<&str> = None;
            let period_ms = (expected_us / 1000).max(1) as i64;
            timer.start_periodic(period_ms);

            loop {
                if !running.load(Ordering::SeqCst) {
                    exit_reason = Some("ctrl-c");
                    break;
                }
                if let Some(dl) = duration_deadline {
                    if std::time::Instant::now() >= dl {
                        exit_reason = Some("duration");
                        break;
                    }
                }
                timer.wait_tick();

                let now_qpc = clock.now();
                let dt_us = elapsed_us(prev_qpc, now_qpc, clock.freq());
                prev_qpc = now_qpc;
                let dt_us32 = dt_us.min(u32::MAX as u64) as u32;
                let late = is_late(dt_us, expected_us);

                // digital state (OS-visible)
                let digital = keys::sample_digital();

                // analog state
                let mut flags: u16 = 0;
                let mut analog = [0u16; 4];
                if let Some(w) = wooting.as_mut() {
                    match w.read_wasda() {
                        Ok([wv, av, sv, dv]) => {
                            flags |= FLAG_ANALOG_VALID;
                            analog = [
                                quantize_analog(wv),
                                quantize_analog(av),
                                quantize_analog(sv),
                                quantize_analog(dv),
                            ];
                        }
                        Err(_code) => {
                            flags |= FLAG_SDK_ERROR;
                            s.sdk_errors += 1;
                            // periodic recovery attempt (~every 5 s)
                            if tick_count % 5000 == 0 && w.recover() {
                                log("wooting reconnected");
                            }
                        }
                    }
                }
                if late {
                    flags |= FLAG_TIMER_LATE;
                }

                let sample = csi::Sample {
                    dt_us: dt_us32,
                    w: analog[0],
                    a: analog[1],
                    s: analog[2],
                    d: analog[3],
                    digital_mask: digital,
                    status_flags: flags,
                };
                if flags & FLAG_ANALOG_VALID != 0 {
                    s.analog_valid_samples += 1;
                }
                s.stats.observe(dt_us32, expected_us as u32, late);
                if let Err(e) = s.push(&sample) {
                    eprintln!("disk write failed: {e}");
                    exit_reason = Some("io-error");
                    break;
                }

                // cheap liveness re-check every ~60 ticks
                tick_count += 1;
                if !args.force && tick_count % 60 == 0 {
                    if find_process_pid("cs2.exe") == 0 {
                        exit_reason = Some("cs2-exited");
                        break;
                    }
                }
            }

            let s = active.take().unwrap();
            if exit_reason == Some("ctrl-c") {
                finalize_and_print(s, "clean shutdown")?;
                break 'supervisor;
            } else {
                finalize_and_print(s, exit_reason.unwrap_or("unknown"))?;
                if args.force {
                    force_session_done = true;
                    break 'supervisor;
                }
                log("waiting for cs2.exe");
            }
        } else {
            // ===================== waiting loop =====================
            let pid = find_process_pid("cs2.exe");
            if pid != 0 {
                log(&format!("CS2 detected, PID {pid}"));
                let header = make_header(&clock, &args, pid, wooting_device_id(&wooting));
                let ts = now_timestamp();
                let s = session::Session::start(&args.output_dir, &header, &ts)
                    .context("failed to create session file")?;
                log(&format!("recording -> {ts}.csi"));
                active = Some(s);
            } else {
                std::thread::sleep(std::time::Duration::from_millis(1000));
            }
        }
        let _ = force_session_done; // reserved for future use
    }

    if let Some(s) = active.take() {
        finalize_and_print(s, "clean shutdown")?;
    }
    log("recorder stopped");
    Ok(())
}

fn wooting_device_id(w: &Option<wooting::Wooting>) -> u32 {
    w.as_ref()
        .map(|w| (w.device_id & 0xFFFF_FFFF) as u32)
        .unwrap_or(0)
}
