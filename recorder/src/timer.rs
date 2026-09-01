//! Timing: QPC clock + high-resolution waitable timer, dt math.
//!
//! The waitable timer itself is Win32 and not unit-tested (brief §13);
//! the math below is.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{HANDLE, WAIT_OBJECT_0};
use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
use windows::Win32::System::Threading::{
    CreateWaitableTimerExW, SetWaitableTimer, WaitForSingleObject,
    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
};

/// QPC clock wrapper.
pub struct QpcClock {
    freq: u64,
}

impl QpcClock {
    pub fn new() -> Self {
        // SAFETY: both calls take out-pointers to local variables; they
        // always succeed on Windows 8+.
        unsafe {
            let mut freq: i64 = 0;
            let _ = QueryPerformanceFrequency(&mut freq);
            let freq = if freq > 0 { freq as u64 } else { 10_000_000 };
            QpcClock { freq }
        }
    }

    pub fn freq(&self) -> u64 {
        self.freq
    }

    pub fn now(&self) -> u64 {
        // SAFETY: out-pointer to local; always succeeds.
        unsafe {
            let mut c: i64 = 0;
            let _ = QueryPerformanceCounter(&mut c);
            c as u64
        }
    }
}

impl Default for QpcClock {
    fn default() -> Self {
        Self::new()
    }
}

/// Elapsed microseconds between two raw QPC readings.
/// wrapping_sub so a counter rollover (never seen in practice) can't panic;
/// wrapping_mul so huge dt (debug overflow check) can't panic either.
#[inline]
pub fn elapsed_us(prev_qpc: u64, now_qpc: u64, freq: u64) -> u64 {
    now_qpc
        .wrapping_sub(prev_qpc)
        .wrapping_mul(1_000_000)
        .wrapping_div(freq.max(1))
}

/// A sample is "late" if it took more than 2x the expected interval.
#[inline]
pub fn is_late(dt_us: u64, expected_us: u64) -> bool {
    dt_us > expected_us.saturating_mul(2)
}

/// High-resolution periodic tick timer with documented fallback.
pub struct TickTimer {
    handle: HANDLE,
    high_res: bool,
}

impl TickTimer {
    /// Create the timer. Falls back to a plain manual-reset waitable timer
    /// if the high-resolution flag is unsupported (pre-1803 Windows).
    pub fn new() -> Self {
        // SAFETY: name is a null PCWSTR (unnamed timer); flags are valid
        // ACCESS_MASK combinations; failure returns INVALID_HANDLE_VALUE
        // which we fall back from.
        unsafe {
            // Automatic-reset (no MANUAL_RESET flag) so each periodic fire
            // releases exactly one wait; HIGH_RESOLUTION for 1 ms accuracy.
            let flags = CREATE_WAITABLE_TIMER_HIGH_RESOLUTION;
            if let Ok(h) = CreateWaitableTimerExW(None, PCWSTR::null(), flags, 0x1F0003) {
                return TickTimer {
                    handle: h,
                    high_res: true,
                };
            }
            // fallback: plain automatic-reset timer (coarser, ~15.6 ms unless
            // global resolution raised); 0 flags = automatic reset
            let h = CreateWaitableTimerExW(None, PCWSTR::null(), 0u32, 0x1F0003)
                .expect("CreateWaitableTimerExW failed even without high-res flag");
            TickTimer {
                handle: h,
                high_res: false,
            }
        }
    }

    pub fn is_high_res(&self) -> bool {
        self.high_res
    }

    /// Arm the periodic timer. Call once before the sample loop; the timer
    /// then fires every `period_ms` milliseconds until cancelled.
    pub fn start_periodic(&self, period_ms: i64) {
        // SAFETY: handle owned; positive period = periodic re-arm by the OS
        // itself (no per-tick SetWaitableTimer call in the hot loop); no
        // completion callback.
        unsafe {
            let due: i64 = -period_ms * 10_000; // negative = relative, 100 ns units
            let _ = SetWaitableTimer(self.handle, &due, period_ms as i32, None, None, false);
        }
    }

    /// Wait for the next timer tick (timer must have been armed with
    /// start_periodic). Returns immediately-with-real-dt on timeout; the
    /// dt math records reality, no fake samples are generated.
    #[inline]
    pub fn wait_tick(&self) {
        // SAFETY: handle owned; timeout slightly above the period.
        unsafe {
            let _ = WaitForSingleObject(self.handle, 32);
        }
    }
}

impl Drop for TickTimer {
    fn drop(&mut self) {
        // SAFETY: handle is owned and not shared.
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dt_math_no_underflow() {
        assert_eq!(elapsed_us(1000, 1999, 1_000_000), 999);
        assert_eq!(elapsed_us(0, 0, 1_000_000), 0);
        // order reversal: wrapping makes it huge, no panic
        let big = elapsed_us(2000, 1000, 1_000_000);
        assert!(big > 1_000_000_000_000);
    }

    #[test]
    fn dt_overflow_safe() {
        // freq close to typical QPC 10 MHz
        assert_eq!(elapsed_us(1_000_000, 1_010_000, 10_000_000), 1_000);
    }

    #[test]
    fn late_wake_flags() {
        assert!(is_late(5001, 1000));
        assert!(!is_late(1004, 1000));
        assert!(!is_late(2000, 1000)); // exactly 2x is not late
        assert!(is_late(2001, 1000));
    }

    #[test]
    fn qpc_clock_monotonic() {
        let c = QpcClock::new();
        assert!(c.freq() > 0);
        let a = c.now();
        let b = c.now();
        assert!(b >= a);
    }
}
