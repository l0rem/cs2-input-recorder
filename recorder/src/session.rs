//! Session: buffered `.csi` writer with clean-finalize and `.part` rename.

use csi::csi::{encode_sample, write_header, Header, Sample};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

/// Running interval statistics — fixed memory, no allocation per sample.
pub struct IntervalStats {
    pub count: u64,
    pub sum_us: u64,
    pub max_us: u32,
    /// 32-bucket histogram of dt in units of expected interval (0.5x..2.5x+)
    pub buckets: [u32; 32],
    pub late_count: u64,
}

impl IntervalStats {
    pub fn new() -> Self {
        IntervalStats {
            count: 0,
            sum_us: 0,
            max_us: 0,
            buckets: [0; 32],
            late_count: 0,
        }
    }

    #[inline]
    pub fn observe(&mut self, dt_us: u32, expected_us: u32, late: bool) {
        self.count += 1;
        self.sum_us += dt_us as u64;
        if dt_us > self.max_us {
            self.max_us = dt_us;
        }
        if late {
            self.late_count += 1;
        }
        // bucket: 0.5x intervals wide, from 0 to 16x expected, clamp above
        let b = (dt_us / (expected_us / 2).max(1)) as usize;
        self.buckets[b.min(31)] += 1;
    }

    pub fn mean_us(&self) -> Option<f64> {
        if self.count == 0 {
            None
        } else {
            Some(self.sum_us as f64 / self.count as f64)
        }
    }

    /// Approximate p95/p99 from the 0.5x-interval histogram.
    pub fn percentile_us(&self, expected_us: u32, p: f64) -> Option<u32> {
        if self.count == 0 {
            return None;
        }
        let target = (p * self.count as f64) as u64;
        let mut acc = 0u64;
        for (i, c) in self.buckets.iter().enumerate() {
            acc += *c as u64;
            if acc >= target {
                return Some((i as u32) * (expected_us / 2).max(1));
            }
        }
        Some(self.max_us)
    }
}

impl Default for IntervalStats {
    fn default() -> Self {
        Self::new()
    }
}

pub struct SessionSummary {
    pub path: PathBuf,
    pub samples: u64,
    pub mean_interval_us: Option<f64>,
    pub p95_interval_us: Option<u32>,
    pub p99_interval_us: Option<u32>,
    pub max_interval_us: u32,
    pub late_samples: u64,
    pub sdk_errors: u64,
    pub analog_valid_samples: u64,
}

pub struct Session {
    writer: BufWriter<std::fs::File>,
    path_final: PathBuf,
    path_part: PathBuf,
    /// samples pushed since last flush
    unflushed: u64,
    pub stats: IntervalStats,
    pub sdk_errors: u64,
    pub analog_valid_samples: u64,
    pub total_samples: u64,
}

const FLUSH_EVERY: u64 = 1000; // ~1 s of samples at 1 kHz

impl Session {
    pub fn start(
        out_dir: &Path,
        header: &Header,
        timestamp_name: &str,
    ) -> std::io::Result<Session> {
        std::fs::create_dir_all(out_dir)?;
        let path_final = out_dir.join(format!("{timestamp_name}.csi"));
        let path_part = out_dir.join(format!("{timestamp_name}.csi.part"));
        let file = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&path_part)?;
        let mut writer = BufWriter::with_capacity(8 * 1024 * 1024, file);
        write_header(&mut writer, header)?;
        Ok(Session {
            writer,
            path_final,
            path_part,
            unflushed: 0,
            stats: IntervalStats::new(),
            sdk_errors: 0,
            analog_valid_samples: 0,
            total_samples: 0,
        })
    }

    #[inline]
    pub fn push(&mut self, s: &Sample) -> std::io::Result<()> {
        let bytes = encode_sample(s);
        self.writer.write_all(&bytes)?;
        self.total_samples += 1;
        self.unflushed += 1;
        if self.unflushed >= FLUSH_EVERY {
            self.writer.flush()?;
            self.unflushed = 0;
        }
        Ok(())
    }

    /// Flush, sync, rename .part -> .csi. Consumes the session.
    pub fn finalize(mut self) -> std::io::Result<SessionSummary> {
        self.writer.flush()?;
        self.writer.get_ref().sync_all()?;
        drop(self.writer);
        if self.path_part.exists() {
            std::fs::rename(&self.path_part, &self.path_final)?;
        }
        Ok(SessionSummary {
            path: self.path_final,
            samples: self.total_samples,
            mean_interval_us: self.stats.mean_us(),
            p95_interval_us: self.stats.percentile_us(1000, 0.95),
            p99_interval_us: self.stats.percentile_us(1000, 0.99),
            max_interval_us: self.stats.max_us,
            late_samples: self.stats.late_count,
            sdk_errors: self.sdk_errors,
            analog_valid_samples: self.analog_valid_samples,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use csi::csi::{BIT_W, FLAG_ANALOG_VALID, MAGIC};

    fn tempdir() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    fn test_header() -> Header {
        Header {
            magic: *MAGIC,
            format_version: csi::csi::FORMAT_VERSION,
            header_size: csi::csi::HEADER_SIZE,
            sample_rate_hz: 1000,
            reserved0: 0,
            qpc_frequency: 10_000_000,
            session_start_qpc: 42,
            utc_start_unix_ms: 0,
            cs2_pid: 0,
            wooting_device_id: 0,
            enabled_key_mask: 0x1FF,
            status_config_flags: 0,
            recorder_version: 0,
            reserved: [0; 40],
        }
    }

    #[test]
    fn session_roundtrip_and_finalize() {
        let dir = tempdir();
        {
            let mut s = Session::start(dir.path(), &test_header(), "test").unwrap();
            for i in 0..3000u32 {
                let sample = Sample {
                    dt_us: 1000,
                    w: (i % 65536) as u16,
                    a: 0,
                    s: 0,
                    d: 0,
                    digital_mask: BIT_W,
                    status_flags: FLAG_ANALOG_VALID,
                };
                s.push(&sample).unwrap();
                s.stats.observe(1000, 1000, false);
            }
            let summary = s.finalize().unwrap();
            assert_eq!(summary.samples, 3000);
            assert!(summary.path.exists());
            assert!(!dir.path().join("test.csi.part").exists());
        }
        // read back
        let mut f = std::fs::File::open(dir.path().join("test.csi")).unwrap();
        let h = csi::csi::read_header(&mut f).unwrap();
        assert_eq!(h.sample_rate_hz, 1000);
        let mut count = 0u64;
        let mut buf = [0u8; 16];
        loop {
            use std::io::Read;
            match f.read_exact(&mut buf) {
                Ok(()) => count += 1,
                Err(_) => break,
            }
        }
        assert_eq!(count, 3000);
    }

    #[test]
    fn truncated_file_still_readable_prefix() {
        let dir = tempdir();
        {
            let mut s = Session::start(dir.path(), &test_header(), "trunc").unwrap();
            for _ in 0..10 {
                s.push(&Sample {
                    dt_us: 1000,
                    w: 0,
                    a: 0,
                    s: 0,
                    d: 0,
                    digital_mask: 0,
                    status_flags: 0,
                })
                .unwrap();
            }
            let summary = s.finalize().unwrap();
            assert_eq!(summary.samples, 10);
        }
        // chop off half of the last sample
        let mut data = std::fs::read(dir.path().join("trunc.csi")).unwrap();
        let header_len = csi::csi::HEADER_SIZE as usize;
        let expected_full = header_len + 9 * 16;
        data.truncate(expected_full + 8);
        std::fs::write(dir.path().join("trunc2.csi"), &data).unwrap();
        let mut f = std::fs::File::open(dir.path().join("trunc2.csi")).unwrap();
        csi::csi::read_header(&mut f).unwrap();
        let mut buf = [0u8; 16];
        let mut count = 0;
        loop {
            use std::io::Read;
            match f.read_exact(&mut buf) {
                Ok(()) => count += 1,
                Err(_) => break,
            }
        }
        assert_eq!(count, 9); // 9 full samples readable despite truncation
    }

    #[test]
    fn late_sample_counted_not_faked() {
        let mut stats = IntervalStats::new();
        stats.observe(1000, 1000, false);
        stats.observe(50_000, 1000, true); // a late wake
        stats.observe(1000, 1000, false);
        assert_eq!(stats.count, 3); // one real sample, no catch-up fakes
        assert_eq!(stats.late_count, 1);
        assert_eq!(stats.max_us, 50_000);
        assert!((stats.mean_us().unwrap() - 17_333.33).abs() < 1.0);
    }
}
