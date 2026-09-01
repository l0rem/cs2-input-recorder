//! `csi` — shared library for the CS2 input recorder project.
//!
//! Pure / testable modules. Win32-touching code (wooting FFI, session IO)
//! lives in the binary crate only.

pub mod csi;
pub mod keys;
pub mod timer;

// Convenience re-exports so consumers can `use csi::{Header, Sample, ...}`
pub use csi::{
    dequantize_analog, encode_sample, quantize_analog, read_header, write_header, CsiError, Header,
    Sample, BIT_A, BIT_CTRL, BIT_D, BIT_MOUSE1, BIT_MOUSE2, BIT_S, BIT_SHIFT, BIT_SPACE, BIT_W,
    FLAG_ANALOG_VALID, FLAG_SDK_ERROR, FLAG_TIMER_LATE, FORMAT_VERSION, HEADER_SIZE, MAGIC,
};
