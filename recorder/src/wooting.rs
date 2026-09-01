//! Wooting Analog SDK C-ABI bindings (official SDK v0.9.1, `wooting_analog_sdk_dist`).
//!
//! No unit tests possible without the SDK DLL + hardware; exercised manually.
//! All Win32/FFI call sites have SAFETY comments (brief §24).

use std::ffi::c_char;

// === Link to the distributable import lib (ships next to our exe) ===
// Exact DLL: wooting_analog_sdk_dist.dll from wooting-analog-sdk v0.9.1
// x86_64-pc-windows-msvc release zip.
#[link(name = "wooting_analog_sdk_dist.dll")]
extern "C" {
    fn wooting_analog_initialise() -> i32;
    fn wooting_analog_is_initialised() -> bool;
    fn wooting_analog_uninitialise() -> i32;
    fn wooting_analog_version_semver() -> *const c_char;
    fn wooting_analog_get_connected_devices_info(buffer: *mut *mut DeviceInfoFfi, len: u32) -> i32;
    fn wooting_analog_set_keycode_mode(mode: u32) -> i32;
    fn wooting_analog_read_full_buffer(
        code_buffer: *mut u16,
        analog_buffer: *mut f32,
        len: u32,
    ) -> i32;
}

/// From the official header `includes/wooting-analog-sdk.h` (v0.9.1).
/// Result codes are negative; Ok is 1.
pub mod result {
    pub const OK: i32 = 1;
    pub const UNINITIALIZED: i32 = -2000;
    pub const NO_DEVICES: i32 = -1999;
    pub const DEVICE_DISCONNECTED: i32 = -1998;
    pub const FAILURE: i32 = -1997;
    pub const INVALID_ARGUMENT: i32 = -1996;
    pub const NO_PLUGINS: i32 = -1995;
    pub const FUNCTION_NOT_FOUND: i32 = -1994;
    pub const NO_MAPPING: i32 = -1993;
    pub const NOT_AVAILABLE: i32 = -1992;
    pub const INCOMPATIBLE_VERSION: i32 = -1991;
    pub const DLL_NOT_FOUND: i32 = -1990;

    pub fn name(code: i32) -> &'static str {
        match code {
            OK => "Ok",
            UNINITIALIZED => "UnInitialized",
            NO_DEVICES => "NoDevices",
            DEVICE_DISCONNECTED => "DeviceDisconnected",
            FAILURE => "Failure",
            INVALID_ARGUMENT => "InvalidArgument",
            NO_PLUGINS => "NoPlugins",
            FUNCTION_NOT_FOUND => "FunctionNotFound",
            NO_MAPPING => "NoMapping",
            NOT_AVAILABLE => "NotAvailable",
            INCOMPATIBLE_VERSION => "IncompatibleVersion",
            DLL_NOT_FOUND => "DllNotFound",
            _ => "Unknown",
        }
    }
}

pub const KEYCODE_MODE_VIRTUAL_KEY: u32 = 2;

/// Mirror of `WootingAnalog_DeviceInfo_FFI` from the official header.
/// Layout verified against includes/wooting-analog-sdk.h lines 63-82.
#[repr(C)]
pub struct DeviceInfoFfi {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer_name: *mut c_char,
    pub device_name: *mut c_char,
    pub device_id: u64,
    pub device_type: i32,
}

#[derive(Debug)]
pub struct Wooting {
    /// device id (0 = unknown)
    pub device_id: u64,
    pub device_name: String,
    pub vendor_id: u16,
    pub product_id: u16,
    codes: [u16; 16],
    analogs: [f32; 16],
    // held analog state; read_full_buffer only reports pressed keys
    // (+ one 0.0 report on release), so we maintain state here.
    w: f32,
    a: f32,
    s: f32,
    d: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitError {
    DllNotFound,
    NoPlugins,
    NoDevices,
    Other(i32),
}

impl InitError {
    pub fn message(&self) -> String {
        match self {
            InitError::DllNotFound => "wooting_analog_sdk_dist.dll not found (place it next to \
                the recorder exe, and the Wooting Analog SDK installed system-wide)"
                .into(),
            InitError::NoPlugins => "SDK loaded but no plugins initialised (is the Wooting \
                analog plugin installed via the Wooting dashboard?)"
                .into(),
            InitError::NoDevices => "SDK initialised but no analog devices found (is the \
                keyboard connected?)"
                .into(),
            InitError::Other(c) => format!("SDK init failed: {} ({c})", result::name(*c)),
        }
    }
}

impl Wooting {
    /// Initialise the SDK. Prints nothing; caller owns console output.
    /// Never pretends success — errors carry the reason (brief §6).
    pub fn init() -> Result<Wooting, InitError> {
        // SAFETY: C-ABI call; no pointers passed; must not be called
        // concurrently from multiple threads (single-threaded app).
        let devices = unsafe { wooting_analog_initialise() };
        if devices < 0 {
            let e = match devices {
                result::DLL_NOT_FOUND => InitError::DllNotFound,
                result::NO_PLUGINS => InitError::NoPlugins,
                result::NO_DEVICES => InitError::NoDevices,
                c => InitError::Other(c),
            };
            return Err(e);
        }

        // VirtualKey mode: analog codes match GetAsyncKeyState codes.
        // SAFETY: simple enum-parameter call.
        let r = unsafe { wooting_analog_set_keycode_mode(KEYCODE_MODE_VIRTUAL_KEY) };
        if r < 0 {
            return Err(InitError::Other(r));
        }

        let mut wooting = Wooting {
            device_id: 0,
            device_name: "unknown".into(),
            vendor_id: 0,
            product_id: 0,
            codes: [0; 16],
            analogs: [0.0; 16],
            w: 0.0,
            a: 0.0,
            s: 0.0,
            d: 0.0,
        };

        // Discover device info (best-effort: fill what we can).
        let mut ptrs: [*mut DeviceInfoFfi; 8] = [std::ptr::null_mut(); 8];
        // SAFETY: buffer of 8 valid pointers; SDK fills up to 8; memory of
        // the structs is valid only until next call — we copy what we need
        // immediately (device name String, ids).
        let n = unsafe { wooting_analog_get_connected_devices_info(ptrs.as_mut_ptr(), 8) };
        if n > 0 {
            let info = unsafe { &*ptrs[0] };
            wooting.device_id = info.device_id;
            wooting.vendor_id = info.vendor_id;
            wooting.product_id = info.product_id;
            // SAFETY: SDK-owned NTS valid until next devices_info call.
            unsafe {
                if !info.device_name.is_null() {
                    wooting.device_name = std::ffi::CStr::from_ptr(info.device_name)
                        .to_string_lossy()
                        .into_owned();
                }
            }
        }

        Ok(wooting)
    }

    pub fn is_initialised() -> bool {
        // SAFETY: simple query.
        unsafe { wooting_analog_is_initialised() }
    }

    pub fn version() -> String {
        // SAFETY: returns a static string owned by the SDK.
        unsafe {
            let p = wooting_analog_version_semver();
            if p.is_null() {
                "unknown".into()
            } else {
                std::ffi::CStr::from_ptr(p).to_string_lossy().into_owned()
            }
        }
    }

    /// Read analog W/A/S/D via one full-buffer SDK call per tick.
    /// Returned order: [w, a, s, d], 0.0..=1.0.
    /// Released keys are reported as 0.0 once by the SDK; we maintain held
    /// state so a released key reads 0.0 until then and after.
    /// Err(code) on SDK error (<0 return).
    pub fn read_wasda(&mut self) -> Result<[f32; 4], i32> {
        // SAFETY: fixed-size buffers owned by self; len matches; single thread.
        let n = unsafe {
            wooting_analog_read_full_buffer(self.codes.as_mut_ptr(), self.analogs.as_mut_ptr(), 16)
        };
        if n < 0 {
            return Err(n);
        }
        for i in 0..n as usize {
            match self.codes[i] {
                0x57 /* W */ => self.w = self.analogs[i],
                0x41 /* A */ => self.a = self.analogs[i],
                0x53 /* S */ => self.s = self.analogs[i],
                0x44 /* D */ => self.d = self.analogs[i],
                _ => {}
            }
        }
        Ok([self.w, self.a, self.s, self.d])
    }

    /// Attempt recovery after a mid-session dropout (brief §6).
    /// Returns true if the SDK re-initialised with a device.
    pub fn recover(&mut self) -> bool {
        // SAFETY: single-threaded; SDK owns its own state.
        unsafe {
            let _ = wooting_analog_uninitialise();
        }
        match Wooting::init() {
            Ok(fresh) => {
                self.device_id = fresh.device_id;
                self.device_name = std::mem::take(&mut self.device_name);
                self.device_name = fresh.device_name.clone();
                self.vendor_id = fresh.vendor_id;
                self.product_id = fresh.product_id;
                true
            }
            Err(_) => false,
        }
    }
}

impl Drop for Wooting {
    fn drop(&mut self) {
        // SAFETY: single-threaded teardown.
        unsafe {
            let _ = wooting_analog_uninitialise();
        }
    }
}
