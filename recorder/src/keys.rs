//! Digital key sampling via GetAsyncKeyState — OS-visible state at each tick.
//!
//! Deliberately NOT Raw Input for Phase 1 (brief §7). The Win32 call itself
//! is not unit-tested; the mask packing is.

use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;

// Virtual-key codes for the sampled keys
pub const VK_W: i32 = 0x57;
pub const VK_A: i32 = 0x41;
pub const VK_S: i32 = 0x53;
pub const VK_D: i32 = 0x44;
pub const VK_SPACE: i32 = 0x20;
pub const VK_LCTRL: i32 = 0xA2;
pub const VK_LSHIFT: i32 = 0xA0;
pub const VK_LBUTTON: i32 = 0x01;
pub const VK_RBUTTON: i32 = 0x02;

// Mask bits (same layout as csi::BIT_*)
pub const MASK_W: u16 = 1 << 0;
pub const MASK_A: u16 = 1 << 1;
pub const MASK_S: u16 = 1 << 2;
pub const MASK_D: u16 = 1 << 3;
pub const MASK_SPACE: u16 = 1 << 4;
pub const MASK_CTRL: u16 = 1 << 5;
pub const MASK_SHIFT: u16 = 1 << 6;
pub const MASK_MOUSE1: u16 = 1 << 7;
pub const MASK_MOUSE2: u16 = 1 << 8;

/// Pack 9 booleans into the digital mask. Testable, no Win32.
#[inline]
pub fn pack_mask(
    w: bool,
    a: bool,
    s: bool,
    d: bool,
    space: bool,
    ctrl: bool,
    shift: bool,
    m1: bool,
    m2: bool,
) -> u16 {
    (w as u16) << 0
        | (a as u16) << 1
        | (s as u16) << 2
        | (d as u16) << 3
        | (space as u16) << 4
        | (ctrl as u16) << 5
        | (shift as u16) << 6
        | (m1 as u16) << 7
        | (m2 as u16) << 8
}

/// True iff the key's high-order bit is set (key is down at call time).
#[inline]
fn down(vk: i32) -> bool {
    // SAFETY: GetAsyncKeyState is thread-safe, takes any valid VK code.
    // Returns SHORT; high bit set = key down.
    unsafe { GetAsyncKeyState(vk) as u16 & 0x8000 != 0 }
}

/// Sample the OS-visible digital state of all 9 tracked keys.
#[inline]
pub fn sample_digital() -> u16 {
    pack_mask(
        down(VK_W),
        down(VK_A),
        down(VK_S),
        down(VK_D),
        down(VK_SPACE),
        down(VK_LCTRL),
        down(VK_LSHIFT),
        down(VK_LBUTTON),
        down(VK_RBUTTON),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn individual_keys() {
        for (bit, args) in [
            (
                MASK_W,
                (true, false, false, false, false, false, false, false, false),
            ),
            (
                MASK_A,
                (false, true, false, false, false, false, false, false, false),
            ),
            (
                MASK_S,
                (false, false, true, false, false, false, false, false, false),
            ),
            (
                MASK_D,
                (false, false, false, true, false, false, false, false, false),
            ),
            (
                MASK_SPACE,
                (false, false, false, false, true, false, false, false, false),
            ),
            (
                MASK_CTRL,
                (false, false, false, false, false, true, false, false, false),
            ),
            (
                MASK_SHIFT,
                (false, false, false, false, false, false, true, false, false),
            ),
            (
                MASK_MOUSE1,
                (false, false, false, false, false, false, false, true, false),
            ),
            (
                MASK_MOUSE2,
                (false, false, false, false, false, false, false, false, true),
            ),
        ] {
            let (w, a, s, d, sp, ct, sh, m1, m2) = args;
            assert_eq!(pack_mask(w, a, s, d, sp, ct, sh, m1, m2), bit);
        }
    }

    #[test]
    fn multiple_keys() {
        assert_eq!(
            pack_mask(true, false, false, true, false, false, false, true, false),
            MASK_W | MASK_D | MASK_MOUSE1
        );
        assert_eq!(
            pack_mask(true, true, true, true, true, true, true, true, true),
            0x01FF
        );
        assert_eq!(
            pack_mask(false, false, false, false, false, false, false, false, false),
            0
        );
    }

    #[test]
    fn mask_matches_csi_bits() {
        // the two modules must agree on bit layout
        assert_eq!(MASK_W, crate::csi::BIT_W);
        assert_eq!(MASK_A, crate::csi::BIT_A);
        assert_eq!(MASK_S, crate::csi::BIT_S);
        assert_eq!(MASK_D, crate::csi::BIT_D);
        assert_eq!(MASK_SPACE, crate::csi::BIT_SPACE);
        assert_eq!(MASK_CTRL, crate::csi::BIT_CTRL);
        assert_eq!(MASK_SHIFT, crate::csi::BIT_SHIFT);
        assert_eq!(MASK_MOUSE1, crate::csi::BIT_MOUSE1);
        assert_eq!(MASK_MOUSE2, crate::csi::BIT_MOUSE2);
    }
}
