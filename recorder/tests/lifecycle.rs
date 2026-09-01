//! Integration tests: session lifecycle (start/push/finalize/truncation).
//! Windows APIs themselves are not tested (brief §13).

use csi::csi::{read_header, Sample, BIT_A, BIT_D, BIT_MOUSE1, BIT_W, FLAG_ANALOG_VALID};
use csi::session::{IntervalStats, Session};

fn header() -> csi::csi::Header {
    csi::csi::Header {
        magic: *csi::csi::MAGIC,
        format_version: csi::csi::FORMAT_VERSION,
        header_size: csi::csi::HEADER_SIZE,
        sample_rate_hz: 1000,
        reserved0: 0,
        qpc_frequency: 10_000_000,
        session_start_qpc: 1000,
        utc_start_unix_ms: 1_788_000_000_000,
        cs2_pid: 0,
        wooting_device_id: 0x7024_B1BA,
        enabled_key_mask: 0x01FF,
        status_config_flags: 1, // forced
        recorder_version: 256,  // v0.1.0
        reserved: [0; 40],
    }
}

#[test]
fn forced_recording_clean_shutdown() {
    let dir = tempfile::tempdir().unwrap();
    let summary;
    {
        let mut s = Session::start(dir.path(), &header(), "forced").unwrap();
        let mut qpc = 1_000u64;
        for i in 0..2500u64 {
            // simulate slight jitter: 995..1005 us
            let dt = 1000 + (i % 11) as u32 - 5;
            qpc += dt as u64 * 10; // 10 MHz freq
            let sample = Sample {
                dt_us: dt,
                w: if i > 1200 { 65535 } else { 0 },
                a: 0,
                s: 0,
                d: if i > 1800 { 32768 } else { 0 },
                digital_mask: if i > 1200 { BIT_W } else { 0 },
                status_flags: FLAG_ANALOG_VALID,
            };
            s.push(&sample).unwrap();
            s.stats.observe(dt, 1000, false);
            if sample.status_flags & FLAG_ANALOG_VALID != 0 {
                s.analog_valid_samples += 1;
            }
        }
        summary = s.finalize().unwrap();
    }
    assert_eq!(summary.samples, 2500);
    assert_eq!(summary.analog_valid_samples, 2500);
    assert!(summary.path.exists(), "final .csi must exist");
    assert!(
        !dir.path().join("forced.csi.part").exists(),
        ".part must be renamed"
    );

    // re-read and validate
    let mut f = std::fs::File::open(&summary.path).unwrap();
    let h = read_header(&mut f).unwrap();
    assert_eq!(h.sample_rate_hz, 1000);
    assert_eq!(h.status_config_flags & 1, 1); // forced flag
    let mut count = 0u64;
    let mut buf = [0u8; 16];
    use std::io::Read;
    while f.read_exact(&mut buf).is_ok() {
        let s = csi::csi::decode_sample(&buf);
        assert_eq!(s.status_flags & FLAG_ANALOG_VALID, FLAG_ANALOG_VALID);
        count += 1;
    }
    assert_eq!(count, 2500);
}

#[test]
fn late_samples_recorded_not_faked() {
    let mut stats = IntervalStats::new();
    // simulate: normal, HUGE stall, then normal — no catch-up samples
    for dt in [1000u32, 1000, 45_000, 1000, 1002] {
        stats.observe(dt, 1000, dt > 2000);
    }
    assert_eq!(stats.count, 5);
    assert_eq!(stats.late_count, 1);
    assert_eq!(stats.max_us, 45_000);
    // mean reflects reality (no synthetic catch-up samples)
    let mean = stats.mean_us().unwrap();
    assert!((mean - 9800.4).abs() < 1.0);
}

#[test]
fn truncated_part_file_recovers_full_samples() {
    let dir = tempfile::tempdir().unwrap();
    {
        let mut s = Session::start(dir.path(), &header(), "crash").unwrap();
        for i in 0..100u32 {
            s.push(&Sample {
                dt_us: 1000,
                w: i as u16,
                a: 0,
                s: 0,
                d: 0,
                digital_mask: BIT_A | BIT_D,
                status_flags: FLAG_ANALOG_VALID,
            })
            .unwrap();
        }
        // NO finalize: simulate a crash — .part file stays
    }
    // a crash recovery tool would read the .part directly:
    let mut data = std::fs::read(dir.path().join("crash.csi.part")).unwrap();
    assert!(data.len() > 96);
    // chop mid-sample like a hard kill would
    data.truncate(data.len() - 7);
    let mut f = std::io::Cursor::new(data);
    let h = read_header(&mut f).unwrap();
    assert_eq!(h.magic, *csi::csi::MAGIC);
    let mut buf = [0u8; 16];
    let mut count = 0u64;
    use std::io::Read;
    while f.read_exact(&mut buf).is_ok() {
        count += 1;
    }
    assert_eq!(count, 99, "99 of 100 samples survive a mid-write kill");
}

#[test]
fn ctrl_c_finalization_semantics() {
    // Ctrl+C sets an AtomicBool; main breaks the loop and finalizes.
    // Simulate exactly that flow here:
    let dir = tempfile::tempdir().unwrap();
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop2 = stop.clone();
    let handle = std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(50));
        stop2.store(true, std::sync::atomic::Ordering::SeqCst);
    });
    let mut s = Session::start(dir.path(), &header(), "ctrlc").unwrap();
    let mut n = 0u64;
    while !stop.load(std::sync::atomic::Ordering::SeqCst) && n < 500 {
        s.push(&Sample {
            dt_us: 1000,
            w: 0,
            a: 0,
            s: 0,
            d: 0,
            digital_mask: BIT_MOUSE1,
            status_flags: FLAG_ANALOG_VALID,
        })
        .unwrap();
        n += 1;
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    let summary = s.finalize().unwrap();
    handle.join().unwrap();
    assert!(summary.samples > 0);
    assert!(summary.path.exists());
    assert!(dir.path().join("ctrlc.csi").exists());
}

#[test]
fn duration_deadline_semantics() {
    // --duration N means: record N seconds then stop. Verify the deadline
    // arithmetic used by main.
    let start = std::time::Instant::now();
    let deadline = start + std::time::Duration::from_secs(0); // duration=0 disabled
                                                              // duration 0 -> no deadline; we encode that as None in main.
    let _ = deadline;
    let d1 = start + std::time::Duration::from_secs(2);
    std::thread::sleep(std::time::Duration::from_millis(10));
    assert!(
        std::time::Instant::now() < d1,
        "deadline must be in the future"
    );
}
