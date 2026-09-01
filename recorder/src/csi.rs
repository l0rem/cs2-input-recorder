//! `.csi` binary format: header + fixed-size 16-byte samples.
//!
//! Pure logic, no Win32, fully unit-testable. Encoding is explicit
//! little-endian per field — no serde in the hot path.

pub const MAGIC: &[u8; 8] = b"CS2INP01";
pub const FORMAT_VERSION: u16 = 1;
pub const HEADER_SIZE: u16 = 96;

// Digital mask bits (brief §9)
pub const BIT_W: u16 = 1 << 0;
pub const BIT_A: u16 = 1 << 1;
pub const BIT_S: u16 = 1 << 2;
pub const BIT_D: u16 = 1 << 3;
pub const BIT_SPACE: u16 = 1 << 4;
pub const BIT_CTRL: u16 = 1 << 5;
pub const BIT_SHIFT: u16 = 1 << 6;
pub const BIT_MOUSE1: u16 = 1 << 7;
pub const BIT_MOUSE2: u16 = 1 << 8;

// Status flags (brief §9)
pub const FLAG_ANALOG_VALID: u16 = 1 << 0;
pub const FLAG_TIMER_LATE: u16 = 1 << 1;
pub const FLAG_SDK_ERROR: u16 = 1 << 2;

/// 16 bytes, little-endian on disk.
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Sample {
    /// actual elapsed time since previous sample, microseconds
    pub dt_us: u32,
    /// analog 0..=65535 (65535 == 1.0 fully depressed)
    pub w: u16,
    pub a: u16,
    pub s: u16,
    pub d: u16,
    pub digital_mask: u16,
    pub status_flags: u16,
}

/// 96 bytes on disk.
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Header {
    pub magic: [u8; 8],
    pub format_version: u16,
    pub header_size: u16,
    pub sample_rate_hz: u16,
    pub reserved0: u16,
    pub qpc_frequency: u64,
    pub session_start_qpc: u64,
    pub utc_start_unix_ms: u64,
    pub cs2_pid: u32,
    pub wooting_device_id: u32,
    pub enabled_key_mask: u16,
    pub status_config_flags: u16,
    pub recorder_version: u32, // crate semver packed (major<<16|minor<<8|patch)
    pub reserved: [u8; 40],    // future expansion (total header = 96 bytes on disk)
}

#[derive(Debug)]
pub enum CsiError {
    BadMagic,
    UnsupportedVersion(u16),
    Truncated,
    Io(std::io::Error),
}

impl std::fmt::Display for CsiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CsiError::BadMagic => write!(f, "not a .csi file (bad magic)"),
            CsiError::UnsupportedVersion(v) => {
                write!(
                    f,
                    "unsupported format version {v} (this decoder supports {FORMAT_VERSION})"
                )
            }
            CsiError::Truncated => write!(f, "file truncated (incomplete sample record)"),
            CsiError::Io(e) => write!(f, "io error: {e}"),
        }
    }
}

impl std::error::Error for CsiError {}

impl From<std::io::Error> for CsiError {
    fn from(e: std::io::Error) -> Self {
        CsiError::Io(e)
    }
}

/// Quantize analog 0.0..=1.0 into u16. Clamps out-of-range, NaN -> 0.
pub const fn quantize_analog(f: f32) -> u16 {
    // const-friendly: no clamp() in const fn, use manual branches
    if f.is_nan() || f <= 0.0 {
        0
    } else if f >= 1.0 {
        65535
    } else {
        (f * 65535.0 + 0.5) as u16
    }
}

/// Dequantize back to 0.0..=1.0
pub const fn dequantize_analog(q: u16) -> f32 {
    q as f32 / 65535.0
}

pub fn encode_sample(s: &Sample) -> [u8; 16] {
    let mut b = [0u8; 16];
    b[0..4].copy_from_slice(&s.dt_us.to_le_bytes());
    b[4..6].copy_from_slice(&s.w.to_le_bytes());
    b[6..8].copy_from_slice(&s.a.to_le_bytes());
    b[8..10].copy_from_slice(&s.s.to_le_bytes());
    b[10..12].copy_from_slice(&s.d.to_le_bytes());
    b[12..14].copy_from_slice(&s.digital_mask.to_le_bytes());
    b[14..16].copy_from_slice(&s.status_flags.to_le_bytes());
    b
}

pub fn decode_sample(b: &[u8; 16]) -> Sample {
    Sample {
        dt_us: u32::from_le_bytes(b[0..4].try_into().unwrap()),
        w: u16::from_le_bytes(b[4..6].try_into().unwrap()),
        a: u16::from_le_bytes(b[6..8].try_into().unwrap()),
        s: u16::from_le_bytes(b[8..10].try_into().unwrap()),
        d: u16::from_le_bytes(b[10..12].try_into().unwrap()),
        digital_mask: u16::from_le_bytes(b[12..14].try_into().unwrap()),
        status_flags: u16::from_le_bytes(b[14..16].try_into().unwrap()),
    }
}

pub fn write_header(w: &mut impl std::io::Write, h: &Header) -> std::io::Result<()> {
    let mut b = [0u8; HEADER_SIZE as usize];
    b[0..8].copy_from_slice(&h.magic);
    b[8..10].copy_from_slice(&h.format_version.to_le_bytes());
    b[10..12].copy_from_slice(&h.header_size.to_le_bytes());
    b[12..14].copy_from_slice(&h.sample_rate_hz.to_le_bytes());
    b[14..16].copy_from_slice(&h.reserved0.to_le_bytes());
    b[16..24].copy_from_slice(&h.qpc_frequency.to_le_bytes());
    b[24..32].copy_from_slice(&h.session_start_qpc.to_le_bytes());
    b[32..40].copy_from_slice(&h.utc_start_unix_ms.to_le_bytes());
    b[40..44].copy_from_slice(&h.cs2_pid.to_le_bytes());
    b[44..48].copy_from_slice(&h.wooting_device_id.to_le_bytes());
    b[48..50].copy_from_slice(&h.enabled_key_mask.to_le_bytes());
    b[50..52].copy_from_slice(&h.status_config_flags.to_le_bytes());
    b[52..56].copy_from_slice(&h.recorder_version.to_le_bytes());
    // b[56..96] reserved zeros
    w.write_all(&b)
}

pub fn read_header(r: &mut impl std::io::Read) -> Result<Header, CsiError> {
    let mut b = [0u8; HEADER_SIZE as usize];
    r.read_exact(&mut b).map_err(|e| {
        if e.kind() == std::io::ErrorKind::UnexpectedEof {
            CsiError::Truncated
        } else {
            CsiError::Io(e)
        }
    })?;
    if &b[0..8] != MAGIC {
        return Err(CsiError::BadMagic);
    }
    let version = u16::from_le_bytes(b[8..10].try_into().unwrap());
    if version != FORMAT_VERSION {
        return Err(CsiError::UnsupportedVersion(version));
    }
    Ok(Header {
        magic: b[0..8].try_into().unwrap(),
        format_version: version,
        header_size: u16::from_le_bytes(b[10..12].try_into().unwrap()),
        sample_rate_hz: u16::from_le_bytes(b[12..14].try_into().unwrap()),
        reserved0: u16::from_le_bytes(b[14..16].try_into().unwrap()),
        qpc_frequency: u64::from_le_bytes(b[16..24].try_into().unwrap()),
        session_start_qpc: u64::from_le_bytes(b[24..32].try_into().unwrap()),
        utc_start_unix_ms: u64::from_le_bytes(b[32..40].try_into().unwrap()),
        cs2_pid: u32::from_le_bytes(b[40..44].try_into().unwrap()),
        wooting_device_id: u32::from_le_bytes(b[44..48].try_into().unwrap()),
        enabled_key_mask: u16::from_le_bytes(b[48..50].try_into().unwrap()),
        status_config_flags: u16::from_le_bytes(b[50..52].try_into().unwrap()),
        recorder_version: u32::from_le_bytes(b[52..56].try_into().unwrap()),
        reserved: b[56..96].try_into().unwrap(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sample_size_is_16() {
        assert_eq!(std::mem::size_of::<Sample>(), 16);
    }

    #[test]
    fn header_size_is_96() {
        assert_eq!(std::mem::size_of::<Header>(), 96);
    }

    fn test_header() -> Header {
        Header {
            magic: *MAGIC,
            format_version: FORMAT_VERSION,
            header_size: HEADER_SIZE,
            sample_rate_hz: 1000,
            reserved0: 0,
            qpc_frequency: 10_000_000,
            session_start_qpc: 1_234_567_890,
            utc_start_unix_ms: 1_788_000_000_000,
            cs2_pid: 12345,
            wooting_device_id: 0xDEAD_BEEF,
            enabled_key_mask: 0x1FF,
            status_config_flags: 1,
            recorder_version: (0 << 16) | (1 << 8) | 0,
            reserved: [0; 40],
        }
    }

    #[test]
    fn header_roundtrip() {
        let h = test_header();
        let mut buf = std::io::Cursor::new(Vec::new());
        write_header(&mut buf, &h).unwrap();
        buf.set_position(0);
        let h2 = read_header(&mut buf).unwrap();
        assert_eq!(h, h2);
    }

    #[test]
    fn sample_roundtrip() {
        let s = Sample {
            dt_us: 1004,
            w: 65535,
            a: 0,
            s: 12345,
            d: 32768,
            digital_mask: BIT_W | BIT_D,
            status_flags: FLAG_ANALOG_VALID,
        };
        let b = encode_sample(&s);
        assert_eq!(decode_sample(&b), s);
    }

    #[test]
    fn sample_known_bytes() {
        // locks the on-disk layout forever
        let s = Sample {
            dt_us: 1000,
            w: 0x1234,
            a: 0x5678,
            s: 0x9ABC,
            d: 0xDEF0,
            digital_mask: 0x0F0F,
            status_flags: 0x0007,
        };
        let b = encode_sample(&s);
        let expected: [u8; 16] = [
            0xE8, 0x03, 0x00, 0x00, // dt_us = 1000 LE
            0x34, 0x12, // w   = 0x1234 LE
            0x78, 0x56, // a   = 0x5678 LE
            0xBC, 0x9A, // s   = 0x9ABC LE
            0xF0, 0xDE, // d   = 0xDEF0 LE
            0x0F, 0x0F, // mask
            0x07, 0x00, // flags
        ];
        assert_eq!(b, expected);
    }

    #[test]
    fn version_rejected() {
        let mut h = test_header();
        h.format_version = 999;
        let mut buf = std::io::Cursor::new(Vec::new());
        write_header(&mut buf, &h).unwrap();
        buf.set_position(0);
        match read_header(&mut buf) {
            Err(CsiError::UnsupportedVersion(999)) => {}
            other => panic!("expected UnsupportedVersion(999), got {other:?}"),
        }
    }

    #[test]
    fn bad_magic_rejected() {
        let mut h = test_header();
        h.magic = *b"NOTCSI01";
        let mut buf = std::io::Cursor::new(Vec::new());
        write_header(&mut buf, &h).unwrap();
        buf.set_position(0);
        match read_header(&mut buf) {
            Err(CsiError::BadMagic) => {}
            other => panic!("expected BadMagic, got {other:?}"),
        }
    }

    #[test]
    fn truncated_header() {
        let h = test_header();
        let mut buf = std::io::Cursor::new(Vec::new());
        write_header(&mut buf, &h).unwrap();
        let data = buf.into_inner();
        // only 50 bytes of a 96-byte header
        let mut r = std::io::Cursor::new(data[..50].to_vec());
        match read_header(&mut r) {
            Err(CsiError::Truncated) => {}
            other => panic!("expected Truncated, got {other:?}"),
        }
    }

    #[test]
    fn quantize_edges() {
        assert_eq!(quantize_analog(0.0), 0);
        assert_eq!(quantize_analog(1.0), 65535);
        assert_eq!(quantize_analog(0.5), 32768);
        assert_eq!(quantize_analog(-0.1), 0);
        assert_eq!(quantize_analog(1.5), 65535);
        assert_eq!(quantize_analog(f32::NAN), 0);
    }

    #[test]
    fn quantize_intermediates() {
        assert_eq!(quantize_analog(0.25), 16384);
        assert_eq!(quantize_analog(0.75), 49151);
    }

    #[test]
    fn digital_bits() {
        // individual bits are unique powers of two
        let bits = [
            BIT_W, BIT_A, BIT_S, BIT_D, BIT_SPACE, BIT_CTRL, BIT_SHIFT, BIT_MOUSE1, BIT_MOUSE2,
        ];
        for (i, b) in bits.iter().enumerate() {
            assert_eq!(*b, 1u16 << i, "bit {i} wrong");
            for b2 in &bits[i + 1..] {
                assert_ne!(b, b2);
            }
        }
        assert_eq!(BIT_W | BIT_MOUSE1, 0x81);
        assert_eq!(BIT_W | BIT_A | BIT_S | BIT_D, 0x000F);
        assert_eq!(
            BIT_W
                | BIT_A
                | BIT_S
                | BIT_D
                | BIT_SPACE
                | BIT_CTRL
                | BIT_SHIFT
                | BIT_MOUSE1
                | BIT_MOUSE2,
            0x01FF
        );
    }

    #[test]
    fn sample_decode_from_partial_prefix() {
        // decoder helper: 2.5 samples -> 2 full samples readable, 12 leftover bytes
        let s = Sample {
            dt_us: 10,
            w: 1,
            a: 2,
            s: 3,
            d: 4,
            digital_mask: 5,
            status_flags: 6,
        };
        let mut data = Vec::new();
        for _ in 0..2 {
            data.extend_from_slice(&encode_sample(&s));
        }
        data.extend_from_slice(&encode_sample(&s)[..12]);
        assert_eq!(data.len() % 16, 12);
        // reader API is in csi-decode; here we just assert the math
        assert_eq!(data.len() / 16, 2);
    }
}
