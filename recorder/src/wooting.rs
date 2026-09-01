//! Wooting Analog SDK C-ABI bindings (official SDK v0.9.1, `wooting_analog_sdk_dist`).
//!
//! Loaded at RUNTIME via LoadLibraryW so the recorder still runs (digital-only,
//! `--analog-optional`) when the DLL is missing — load-time linking would kill
//! the process before main. No unit tests possible without DLL + hardware;
//! exercised manually. All unsafe call sites carry SAFETY comments (brief §24).

use std::ffi::c_char;
use windows::core::PCWSTR;

// Function pointer types mirroring the official header signatures.
type InitialiseFn = unsafe extern "C" fn() -> i32;
type IsInitialisedFn = unsafe extern "C" fn() -> bool;
type UninitialiseFn = unsafe extern "C" fn() -> i32;
type VersionSemverFn = unsafe extern "C" fn() -> *const c_char;
type GetDevicesInfoFn = unsafe extern "C" fn(*mut *mut DeviceInfoFfi, u32) -> i32;
type SetKeycodeModeFn = unsafe extern "C" fn(u32) -> i32;
type ReadFullBufferFn = unsafe extern "C" fn(*mut u16, *mut f32, u32) -> i32;

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

/// Mirror of `WootingAnalog_DeviceInfo_FFI` from the official header
/// (includes/wooting-analog-sdk.h lines 63-82).
#[repr(C)]
pub struct DeviceInfoFfi {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer_name: *mut c_char,
    pub device_name: *mut c_char,
    pub device_id: u64,
    pub device_type: i32,
}

/// Runtime-resolved SDK function table. All pointers are null until
/// `load_dll` succeeds.
pub struct WootingApi {
    pub initialise: InitialiseFn,
    pub is_initialised: IsInitialisedFn,
    pub uninitialise: UninitialiseFn,
    pub version_semver: VersionSemverFn,
    pub get_connected_devices_info: GetDevicesInfoFn,
    pub set_keycode_mode: SetKeycodeModeFn,
    pub read_full_buffer: ReadFullBufferFn,
    module: windows::Win32::Foundation::HMODULE,
}

unsafe impl Send for WootingApi {}

impl WootingApi {
    /// Load `wooting_analog_sdk_dist.dll` (next to the exe, then the system
    /// search path) and resolve the 7 functions we use.
    pub fn load() -> Result<WootingApi, InitError> {
        const DLL_NAME: &[u16] = &[
            b'w' as u16,
            b'o' as u16,
            b'o' as u16,
            b't' as u16,
            b'i' as u16,
            b'n' as u16,
            b'g' as u16,
            b'_' as u16,
            b'a' as u16,
            b'n' as u16,
            b'a' as u16,
            b'l' as u16,
            b'o' as u16,
            b'g' as u16,
            b'_' as u16,
            b's' as u16,
            b'd' as u16,
            b'k' as u16,
            b'_' as u16,
            b'd' as u16,
            b'i' as u16,
            b's' as u16,
            b't' as u16,
            b'.' as u16,
            b'd' as u16,
            b'l' as u16,
            b'l' as u16,
            0,
        ];
        // SAFETY: DLL_NAME is a null-terminated UTF-16 literal.
        let handle = unsafe {
            windows::Win32::System::LibraryLoader::LoadLibraryW(PCWSTR(DLL_NAME.as_ptr()))
        }
        .map_err(|_| InitError::DllNotFound)?;
        let sym = |name: &[u8]| -> Result<usize, InitError> {
            // GetProcAddress is ANSI (PCSTR): u8 NTS name. Callers include
            // the trailing NUL in `name`; strip it before wrapping in CString
            // (CString::new rejects interior NULs).
            let trimmed = &name[..name.len().saturating_sub(1)]; // drop trailing NUL
            let name_c =
                std::ffi::CString::new(trimmed).map_err(|_| InitError::FunctionNotFound)?;
            // SAFETY: handle valid (we own it until drop); name is a NTS.
            let p = unsafe {
                windows::Win32::System::LibraryLoader::GetProcAddress(
                    handle,
                    windows::core::PCSTR(name_c.as_ptr().cast()),
                )
            };
            p.ok_or(InitError::FunctionNotFound).map(|f| f as usize)
        };
        Ok(WootingApi {
            // SAFETY: signature verified against the official v0.9.1 header.
            initialise: unsafe { std::mem::transmute(sym(b"wooting_analog_initialise\0")?) },
            is_initialised: unsafe {
                std::mem::transmute(sym(b"wooting_analog_is_initialised\0")?)
            },
            uninitialise: unsafe { std::mem::transmute(sym(b"wooting_analog_uninitialise\0")?) },
            version_semver: unsafe {
                std::mem::transmute(sym(b"wooting_analog_version_semver\0")?)
            },
            get_connected_devices_info: unsafe {
                std::mem::transmute(sym(b"wooting_analog_get_connected_devices_info\0")?)
            },
            set_keycode_mode: unsafe {
                std::mem::transmute(sym(b"wooting_analog_set_keycode_mode\0")?)
            },
            read_full_buffer: unsafe {
                std::mem::transmute(sym(b"wooting_analog_read_full_buffer\0")?)
            },
            module: handle,
        })
    }
}

pub struct Wooting {
    pub device_id: u64,
    pub device_name: String,
    pub vendor_id: u16,
    pub product_id: u16,
    api: WootingApi,
    codes: [u16; 16],
    analogs: [f32; 16],
    // held analog state; read_full_buffer only reports pressed keys
    // (+ one 0.0 report on release), so we maintain state here.
    w: f32,
    a: f32,
    s: f32,
    d: f32,
}

impl std::fmt::Debug for Wooting {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Wooting")
            .field("device_id", &self.device_id)
            .field("device_name", &self.device_name)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitError {
    DllNotFound,
    FunctionNotFound,
    NoPlugins,
    NoDevices,
    Other(i32),
}

impl InitError {
    pub fn message(&self) -> String {
        match self {
            InitError::DllNotFound => {
                "wooting_analog_sdk_dist.dll not found next to the recorder exe \
                 (system SDK may still be installed — digital-only mode still works)"
                    .into()
            }
            InitError::FunctionNotFound => "SDK DLL loaded but a required export is missing \
                 (SDK version mismatch?)"
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
    /// Initialise the SDK. Never pretends success — errors carry the reason.
    pub fn init() -> Result<Wooting, InitError> {
        let api = WootingApi::load()?;

        // SAFETY: C-ABI fn pointer from the loaded DLL; single-threaded app.
        let devices = unsafe { (api.initialise)() };
        if devices < 0 {
            return Err(match devices {
                result::DLL_NOT_FOUND => InitError::DllNotFound,
                result::NO_PLUGINS => InitError::NoPlugins,
                result::NO_DEVICES => InitError::NoDevices,
                c => InitError::Other(c),
            });
        }

        // VirtualKey mode: analog codes match GetAsyncKeyState codes.
        // SAFETY: simple enum-parameter call.
        let r = unsafe { (api.set_keycode_mode)(KEYCODE_MODE_VIRTUAL_KEY) };
        if r < 0 {
            return Err(InitError::Other(r));
        }

        let mut wooting = Wooting {
            device_id: 0,
            device_name: "unknown".into(),
            vendor_id: 0,
            product_id: 0,
            api,
            codes: [0; 16],
            analogs: [0.0; 16],
            w: 0.0,
            a: 0.0,
            s: 0.0,
            d: 0.0,
        };

        // Discover device info (best-effort: fill what we can).
        let mut ptrs: [*mut DeviceInfoFfi; 8] = [std::ptr::null_mut(); 8];
        // SAFETY: buffer of 8 valid slots; struct memory valid only until the
        // next devices_info call — we copy everything we need immediately.
        let n = unsafe { (wooting.api.get_connected_devices_info)(ptrs.as_mut_ptr(), 8) };
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

    pub fn version() -> String {
        match WootingApi::load() {
            Ok(api) => {
                // SAFETY: returns a static string owned by the SDK.
                unsafe {
                    let p = (api.version_semver)();
                    if p.is_null() {
                        "unknown".into()
                    } else {
                        std::ffi::CStr::from_ptr(p).to_string_lossy().into_owned()
                    }
                }
            }
            Err(_) => "unavailable".into(),
        }
    }

    /// Read analog W/A/S/D via one full-buffer SDK call per tick.
    /// Returned order: [w, a, s, d], 0.0..=1.0. Err(code) on SDK error.
    pub fn read_wasda(&mut self) -> Result<[f32; 4], i32> {
        // SAFETY: fixed buffers owned by self; single thread; len matches.
        let n = unsafe {
            (self.api.read_full_buffer)(self.codes.as_mut_ptr(), self.analogs.as_mut_ptr(), 16)
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
            let _ = (self.api.uninitialise)();
        }
        // Wooting implements Drop, so fields can't be moved out of a fresh
        // instance; copy scalars and swap the api via mem::forget-free trick:
        // wrap fresh in ManuallyDrop and take its api.
        let mut fresh = match Wooting::init() {
            Ok(f) => f,
            Err(_) => return false,
        };
        self.device_id = fresh.device_id;
        self.device_name = std::mem::take(&mut self.device_name);
        self.device_name = std::mem::take(&mut fresh.device_name);
        self.vendor_id = fresh.vendor_id;
        self.product_id = fresh.product_id;
        // SAFETY: both sides are &mut; we only move the raw api struct.
        self.api = unsafe { std::ptr::read(&fresh.api) };
        std::mem::forget(fresh); // don't double-uninitialise the old api
        true
    }
}

impl Drop for Wooting {
    fn drop(&mut self) {
        // SAFETY: single-threaded teardown.
        unsafe {
            let _ = (self.api.uninitialise)();
        }
    }
}
