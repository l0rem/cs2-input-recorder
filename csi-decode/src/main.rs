//! csi-decode — offline decoder/debug exporter for `.csi` session files.

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use csi::csi::{dequantize_analog, read_header, CsiError};
use std::io::{BufReader, Read, Write};

#[derive(Parser)]
#[command(
    name = "csi-decode",
    version,
    about = "Decode/export .csi session files"
)]
struct Args {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Print session header + stats
    Inspect { file: std::path::PathBuf },
    /// Export a small time window to CSV
    ExportCsv {
        file: std::path::PathBuf,
        /// start offset in seconds
        #[arg(long, default_value_t = 0.0)]
        start: f64,
        /// window duration in seconds (max 60)
        #[arg(long)]
        duration: f64,
        /// output path (default stdout)
        #[arg(long)]
        out: Option<std::path::PathBuf>,
    },
}

struct SessionData {
    header: csi::csi::Header,
    samples: Vec<csi::csi::Sample>,
    truncated: bool,
}

fn load(path: &std::path::Path) -> Result<SessionData> {
    let mut f = BufReader::new(
        std::fs::File::open(path).with_context(|| format!("open {}", path.display()))?,
    );
    let header = read_header(&mut f).map_err(|e| match e {
        CsiError::Io(io) => io.into(),
        other => anyhow::Error::new(other).context(format!("decode {}", path.display())),
    })?;
    let mut samples = Vec::new();
    let mut buf = [0u8; 16];
    let mut truncated = false;
    loop {
        match f.read_exact(&mut buf) {
            Ok(()) => samples.push(csi::csi::decode_sample(&buf)),
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
                // EOF while a full 16-byte record was expected: either a
                // clean end (impossible — we only get here if read_exact
                // failed) or a partial trailing record from a killed writer.
                // All full samples read before this point remain valid.
                truncated = true;
                break;
            }
            Err(e) => return Err(e.into()),
        }
    }
    Ok(SessionData {
        header,
        samples,
        truncated,
    })
}

fn transitions(samples: &[csi::csi::Sample], bit: u16) -> u64 {
    let mut count = 0;
    let mut prev = false;
    for s in samples {
        let now = s.digital_mask & bit != 0;
        if now && !prev {
            count += 1;
        }
        prev = now;
    }
    count
}

fn cmd_inspect(file: &std::path::Path) -> Result<()> {
    let data = load(file)?;
    let h = &data.header;
    let rate = h.sample_rate_hz.max(1) as u64;
    let n = data.samples.len() as u64;
    let duration_s = n as f64 / rate as f64;

    println!("=== header ===");
    println!("magic:            {}", String::from_utf8_lossy(&h.magic));
    println!("format version:   {}", h.format_version);
    println!("sample rate:      {} Hz", h.sample_rate_hz);
    println!("QPC frequency:    {}", h.qpc_frequency);
    println!(
        "session start:    QPC {} / unix {} ms",
        h.session_start_qpc, h.utc_start_unix_ms
    );
    println!("cs2 pid:          {}", h.cs2_pid);
    println!("wooting device:   id {:08x}", h.wooting_device_id);
    println!("enabled keys:     {:#06x}", h.enabled_key_mask);
    println!(
        "recorder version: v{}.{}.{}",
        h.recorder_version >> 16,
        (h.recorder_version >> 8) & 0xFF,
        h.recorder_version & 0xFF
    );
    println!("=== session ===");
    println!("duration:         {duration_s:.3} s");
    println!("samples:          {n}");
    if data.truncated {
        println!(
            "NOTE: file ends mid-sample (killed/crashed session); full samples above are valid"
        );
    }

    if !data.samples.is_empty() {
        let mut sum = 0u64;
        let mut max = 0u32;
        let mut dts: Vec<u32> = Vec::with_capacity(data.samples.len());
        for s in &data.samples {
            sum += s.dt_us as u64;
            max = max.max(s.dt_us);
            dts.push(s.dt_us);
        }
        dts.sort_unstable();
        let p = |f: f64| dts[(f * (dts.len() - 1) as f64) as usize];
        println!("mean interval:    {:.1} us", sum as f64 / n as f64);
        println!("p95 interval:     {} us", p(0.95));
        println!("p99 interval:     {} us", p(0.99));
        println!("max interval:     {max} us");
    }

    let analog_valid = data
        .samples
        .iter()
        .filter(|s| s.status_flags & csi::csi::FLAG_ANALOG_VALID != 0)
        .count();
    println!("analog valid:     {analog_valid}/{n}");
    println!(
        "sdk errors:       {}",
        data.samples
            .iter()
            .filter(|s| s.status_flags & csi::csi::FLAG_SDK_ERROR != 0)
            .count()
    );
    println!(
        "late samples:     {}",
        data.samples
            .iter()
            .filter(|s| s.status_flags & csi::csi::FLAG_TIMER_LATE != 0)
            .count()
    );
    println!("=== transitions (rising edges) ===");
    for (name, bit) in [
        ("W", csi::csi::BIT_W),
        ("A", csi::csi::BIT_A),
        ("S", csi::csi::BIT_S),
        ("D", csi::csi::BIT_D),
        ("Space", csi::csi::BIT_SPACE),
        ("Ctrl", csi::csi::BIT_CTRL),
        ("Shift", csi::csi::BIT_SHIFT),
        ("Mouse1", csi::csi::BIT_MOUSE1),
        ("Mouse2", csi::csi::BIT_MOUSE2),
    ] {
        println!("{name:<7} = {}", transitions(&data.samples, bit));
    }
    Ok(())
}

fn cmd_export_csv(
    file: &std::path::Path,
    start: f64,
    duration: f64,
    out: Option<std::path::PathBuf>,
) -> Result<()> {
    if duration > 60.0 {
        bail!(
            "CSV export is for small debug windows; --duration max is 60 s (asked for {duration})"
        );
    }
    if duration <= 0.0 {
        bail!("--duration must be > 0");
    }
    let data = load(file)?;
    let rate = data.header.sample_rate_hz.max(1) as f64;
    let start_idx = (start * rate) as usize;
    let end_idx = (((start + duration) * rate) as usize).min(data.samples.len());
    if start_idx >= data.samples.len() {
        bail!(
            "--start {start} is past end of session ({} samples)",
            data.samples.len()
        );
    }

    let mut w: Box<dyn Write> = match out {
        Some(p) => Box::new(std::fs::File::create(p)?),
        None => Box::new(std::io::stdout().lock()),
    };
    writeln!(
        w,
        "time_ms,w,a,s,d,w_down,a_down,s_down,d_down,space,ctrl,shift,mouse1,mouse2,status"
    )?;
    for (i, s) in data.samples[start_idx..end_idx].iter().enumerate() {
        let t_ms = (start_idx + i) as f64 * 1000.0 / rate;
        writeln!(
            w,
            "{:.3},{:.6},{:.6},{:.6},{:.6},{},{},{},{},{},{},{},{},{},{:#06x}",
            t_ms,
            dequantize_analog(s.w),
            dequantize_analog(s.a),
            dequantize_analog(s.s),
            dequantize_analog(s.d),
            s.digital_mask & csi::csi::BIT_W != 0,
            s.digital_mask & csi::csi::BIT_A != 0,
            s.digital_mask & csi::csi::BIT_S != 0,
            s.digital_mask & csi::csi::BIT_D != 0,
            s.digital_mask & csi::csi::BIT_SPACE != 0,
            s.digital_mask & csi::csi::BIT_CTRL != 0,
            s.digital_mask & csi::csi::BIT_SHIFT != 0,
            s.digital_mask & csi::csi::BIT_MOUSE1 != 0,
            s.digital_mask & csi::csi::BIT_MOUSE2 != 0,
            s.status_flags,
        )?;
    }
    Ok(())
}

fn main() -> Result<()> {
    let args = Args::parse();
    match args.cmd {
        Cmd::Inspect { file } => cmd_inspect(&file),
        Cmd::ExportCsv {
            file,
            start,
            duration,
            out,
        } => cmd_export_csv(&file, start, duration, out),
    }
}

// keep CsiError import used (read_header returns it)
#[allow(dead_code)]
fn _assert_error_traits(_: CsiError) {}
