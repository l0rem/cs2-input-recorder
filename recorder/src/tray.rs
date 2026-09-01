//! System tray icon — zero hot-path cost.
//!
//! A dedicated thread owns a hidden Win32 message window and the tray icon.
//! The sample loop only touches two atomics: `set_state()` on state CHANGES
//! (twice per session) and `stop_requested()` (one relaxed load per tick,
//! alongside the existing ctrl-c flag). No locks, no syscalls, no allocations
//! in the 1 kHz loop.
//!
//! Note on message flow: Shell_NotifyIcon callback notifications are SENT to
//! the window's wndproc (not queued), so click handling lives in
//! `tray_wndproc`. State changes are posted from `set_state` via
//! `PostMessageW` to the same hwnd.

use std::sync::atomic::{AtomicBool, AtomicIsize, AtomicU8, Ordering};

use windows::core::PCWSTR;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    CreateDIBSection, DeleteObject, GetDC, ReleaseDC, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    DIB_RGB_COLORS,
};
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconIndirect, CreatePopupMenu, CreateWindowExW, DefWindowProcW,
    DestroyIcon, DestroyMenu, DestroyWindow, DispatchMessageW, GetCursorPos, GetMessageW,
    PostMessageW, PostQuitMessage, RegisterClassW, SetForegroundWindow, ShowWindow,
    TrackPopupMenu, TranslateMessage, HICON, HMENU, MENU_ITEM_FLAGS, MSG, SW_HIDE, TPM_BOTTOMALIGN,
    TPM_LEFTALIGN, TPM_RETURNCMD, WINDOW_EX_STYLE, WM_APP, WM_COMMAND, WM_DESTROY, WM_LBUTTONUP,
    WM_RBUTTONUP,
};

pub const STATE_WAITING: u8 = 0; // red: no CS2 detected
pub const STATE_RECORDING: u8 = 1; // green: recording

const WM_TRAYICON: u32 = WM_APP + 1;
const WM_STATECHANGED: u32 = WM_APP + 2;
const ID_STOP: u16 = 1001;

static STATE: AtomicU8 = AtomicU8::new(STATE_WAITING);
static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);
/// HWND of the tray window, set once by the tray thread (0 until ready).
static TRAY_HWND: AtomicIsize = AtomicIsize::new(0);

/// Called by the recorder on state transitions — two atomic stores + one
/// PostMessage per transition. Wakes the tray thread to swap the icon.
pub fn set_state(s: u8) {
    STATE.store(s, Ordering::SeqCst);
    let hwnd = TRAY_HWND.load(Ordering::SeqCst);
    if hwnd != 0 {
        unsafe {
            let _ = PostMessageW(HWND(hwnd as *mut _), WM_STATECHANGED, WPARAM(0), LPARAM(0));
        }
    }
}

/// Polled by the main loop alongside ctrl-c (one relaxed atomic load per tick).
pub fn stop_requested() -> bool {
    STOP_REQUESTED.load(Ordering::Relaxed)
}

#[inline]
fn inside_circle(x: i32, y: i32) -> bool {
    let dx = f64::from(x) - 7.5;
    let dy = f64::from(y) - 7.5;
    dx * dx + dy * dy <= 6.0f64 * 6.0
}

/// 16x16 icon: solid filled dot on transparent background.
/// `rgb` is 0x00RRGGBB.
unsafe fn make_dot_icon(rgb: u32) -> HICON {
    let r = ((rgb >> 16) & 0xFF) as u8;
    let g = ((rgb >> 8) & 0xFF) as u8;
    let b = (rgb & 0xFF) as u8;

    // 32bpp BGRA, top-down (negative height).
    let mut px: Vec<u8> = Vec::with_capacity(16 * 16 * 4);
    for y in 0..16 {
        for x in 0..16 {
            if inside_circle(x, y) {
                px.extend_from_slice(&[b, g, r, 255]);
            } else {
                px.extend_from_slice(&[0, 0, 0, 0]);
            }
        }
    }
    // AND mask: 1 = transparent. 16 px wide = 2 bytes per row, 16 rows.
    let mut and_mask = vec![0xFFu8; 32];
    for y in 0..16usize {
        for x in 0..16usize {
            if inside_circle(x as i32, y as i32) {
                and_mask[y * 2 + x / 8] &= !(0x80 >> (x % 8));
            }
        }
    }

    let hdc = GetDC(None);
    let mut icon_info = windows::Win32::UI::WindowsAndMessaging::ICONINFO {
        fIcon: true.into(),
        xHotspot: 0,
        yHotspot: 0,
        hbmMask: Default::default(),
        hbmColor: Default::default(),
    };
    let mut bmi = BITMAPINFO::default();
    bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
    bmi.bmiHeader.biWidth = 16;
    bmi.bmiHeader.biHeight = -16; // negative = top-down, matches our row order
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB.0;
    let mut bits: *mut std::ffi::c_void = std::ptr::null_mut();
    let color_bmp = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &mut bits, None, 0)
        .expect("CreateDIBSection color");
    std::ptr::copy_nonoverlapping(px.as_ptr(), bits as *mut u8, px.len());

    let mut bmi1 = BITMAPINFO::default();
    bmi1.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
    bmi1.bmiHeader.biWidth = 16;
    bmi1.bmiHeader.biHeight = 16; // bottom-up mask
    bmi1.bmiHeader.biPlanes = 1;
    bmi1.bmiHeader.biBitCount = 1;
    bmi1.bmiHeader.biCompression = BI_RGB.0;
    let mut bits1: *mut std::ffi::c_void = std::ptr::null_mut();
    let mask_bmp = CreateDIBSection(hdc, &bmi1, DIB_RGB_COLORS, &mut bits1, None, 0)
        .expect("CreateDIBSection mask");
    std::ptr::copy_nonoverlapping(and_mask.as_ptr(), bits1 as *mut u8, and_mask.len());

    icon_info.hbmColor = color_bmp;
    icon_info.hbmMask = mask_bmp;
    let icon = CreateIconIndirect(&icon_info).expect("CreateIconIndirect");
    let _ = DeleteObject(color_bmp);
    let _ = DeleteObject(mask_bmp);
    let _ = ReleaseDC(None, hdc);
    icon
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

unsafe fn utf16_padded(s: &str) -> [u16; 128] {
    let mut buf = [0u16; 128];
    for (i, c) in s.encode_utf16().take(127).enumerate() {
        buf[i] = c;
    }
    buf
}

fn tooltip_for(state: u8) -> &'static str {
    if state == STATE_RECORDING {
        "cs2-input-recorder: RECORDING"
    } else {
        "cs2-input-recorder: waiting for cs2.exe"
    }
}

/// Show the context menu. Runs on the tray thread inside the wndproc.
unsafe fn show_context_menu(hwnd: HWND) {
    // Required so the menu dismisses when clicking elsewhere.
    SetForegroundWindow(hwnd);
    let menu: HMENU = windows::Win32::UI::WindowsAndMessaging::CreatePopupMenu().expect("menu");
    let label = wide("Stop recorder");
    AppendMenuW(
        menu,
        MENU_ITEM_FLAGS(0),
        ID_STOP as usize,
        PCWSTR(label.as_ptr()),
    );
    let mut pt = windows::Win32::Foundation::POINT::default();
    let _ = GetCursorPos(&mut pt);
    let cmd = windows::Win32::UI::WindowsAndMessaging::TrackPopupMenu(
        menu,
        TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
        pt.x,
        pt.y,
        0,
        hwnd,
        None,
    );
    let _ = windows::Win32::UI::WindowsAndMessaging::DestroyMenu(menu);
    if cmd.0 as u16 == ID_STOP {
        STOP_REQUESTED.store(true, Ordering::SeqCst);
    }
}

unsafe extern "system" fn tray_wndproc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if msg == WM_TRAYICON {
        // low word of lParam = mouse message
        let mouse = (lparam.0 & 0xFFFF) as u32;
        if mouse == WM_RBUTTONUP || mouse == WM_LBUTTONUP {
            show_context_menu(hwnd);
        }
        return LRESULT(0);
    }
    if msg == WM_DESTROY {
        PostQuitMessage(0);
        return LRESULT(0);
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

fn tray_thread() {
    unsafe {
        let class_name = wide("csi_tray");
        let wc = windows::Win32::UI::WindowsAndMessaging::WNDCLASSW {
            lpfnWndProc: Some(tray_wndproc),
            lpszClassName: PCWSTR(class_name.as_ptr()),
            hInstance: windows::Win32::System::LibraryLoader::GetModuleHandleW(None)
                .unwrap_or_default()
                .into(),
            ..Default::default()
        };
        let atom = RegisterClassW(&wc);
        if atom == 0 {
            eprintln!("tray: RegisterClassW failed");
            return;
        }
        // Hidden top-level window (message-only windows cannot host tray icons).
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            PCWSTR(class_name.as_ptr()),
            PCWSTR(wide("cs2-input-recorder").as_ptr()),
            windows::Win32::UI::WindowsAndMessaging::WINDOW_STYLE(0), // not WS_VISIBLE
            0,
            0,
            0,
            0,
            None,
            None,
            windows::Win32::System::LibraryLoader::GetModuleHandleW(None).unwrap_or_default(),
            None,
        )
        .expect("tray CreateWindowExW");
        TRAY_HWND.store(hwnd.0 as isize, Ordering::SeqCst);

        let icon_red = make_dot_icon(0xC8_00_00); // 0xRRGGBB: red
        let icon_green = make_dot_icon(0x00_C8_00); // green
        if icon_red.is_invalid() || icon_green.is_invalid() {
            eprintln!("tray: icon creation failed");
            return;
        }

        let nid = NOTIFYICONDATAW {
            uID: 1,
            hWnd: hwnd,
            uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage: WM_TRAYICON,
            hIcon: icon_red,
            szTip: utf16_padded(tooltip_for(STATE_WAITING)),
            ..Default::default()
        };
        if Shell_NotifyIconW(NIM_ADD, &nid) == windows::Win32::Foundation::FALSE {
            eprintln!("tray: Shell_NotifyIconW add failed");
            return;
        }
        ShowWindow(hwnd, SW_HIDE);

        let mut current = STATE_WAITING;
        let mut msg = MSG::default();
        loop {
            let r = GetMessageW(&mut msg, None, 0, 0);
            if r.0 == 0 {
                break;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
            // apply pending state change (icon swap) after processing messages
            let new_state = STATE.load(Ordering::SeqCst);
            if new_state != current {
                current = new_state;
                let mut nid = NOTIFYICONDATAW {
                    uID: 1,
                    hWnd: hwnd,
                    uFlags: NIF_ICON | NIF_TIP,
                    hIcon: if current == STATE_RECORDING { icon_green } else { icon_red },
                    szTip: utf16_padded(tooltip_for(current)),
                    ..Default::default()
                };
                let _ = Shell_NotifyIconW(NIM_MODIFY, &nid);
            }
        }
        let _ = Shell_NotifyIconW(
            windows::Win32::UI::Shell::NIM_DELETE,
            &NOTIFYICONDATAW {
                uID: 1,
                hWnd: hwnd,
                ..Default::default()
            },
        );
        let _ = DestroyIcon(icon_red);
        let _ = DestroyIcon(icon_green);
    }
}

/// Spawn the tray thread. Call once at startup; never touches the sample loop.
pub fn spawn() -> Option<std::thread::JoinHandle<()>> {
    std::thread::Builder::new()
        .name("tray".into())
        .stack_size(64 * 1024)
        .spawn(tray_thread)
        .ok()
}
