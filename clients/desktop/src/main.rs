#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod compat;
mod sha256;
mod trusted;

use compat::{BundleManifest, HandshakeReport, TrustedStatus};
use futures_util::future::join_all;
use if_addrs::{get_if_addrs, IfAddr};
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashSet},
    fs,
    net::{IpAddr, Ipv4Addr},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, RunEvent, State, WebviewWindow, WindowEvent,
};
use tauri_plugin_dialog::DialogExt;

#[derive(Debug, Serialize)]
struct DiscoveryResult {
    name: String,
    url: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct PersistedConfig {
    server_url: String,
}

/// 客户端本机配置（client.json）：只保存客户端自用字段，绝不提交服务器。
#[derive(Debug, Deserialize, Serialize, Default, Clone, PartialEq)]
struct ClientConfig {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    upload_source_dir: Option<String>,
}

/// 会话级白名单：只允许读取本轮通过对话框明确选中的文件。
struct ClientSessionState(Mutex<HashSet<PathBuf>>);

#[derive(Debug, Clone, Serialize)]
struct LaunchResult {
    destination: String,
    auto_login: bool,
    notice: String,
}

#[derive(Default)]
struct DiagnosticsInner {
    handshake: Option<HandshakeReport>,
    trusted: TrustedStatus,
    bundled_backend_version: String,
    manifest_error: String,
    program_data_non_overlapping: Option<bool>,
    notice: String,
    last_error: String,
}

#[derive(Default)]
struct ClientDiagnostics(Mutex<DiagnosticsInner>);

#[derive(Debug, Serialize)]
struct DiagnosticsView {
    desktop_version: String,
    build_mode: String,
    bundled_backend_version: String,
    capability_profile: String,
    capability_digest: String,
    manifest_error: String,
    handshake: Option<HandshakeReport>,
    trusted: TrustedStatus,
    program_data_non_overlapping: Option<bool>,
    notice: String,
    last_error: String,
    backend_phase: String,
    backend_pid: Option<u32>,
    install_dir: String,
    data_location: String,
}

/// 托管的本地后端子进程生命周期阶段。
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum BackendPhase {
    Idle,
    Launching,
    Running,
    Failed,
}

impl BackendPhase {
    fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Launching => "launching",
            Self::Running => "running",
            Self::Failed => "failed",
        }
    }
}

struct BackendStateInner {
    phase: BackendPhase,
    child: Option<std::process::Child>,
    last_error: Option<String>,
}

/// 托管的本地后端子进程句柄状态机（仅当由本桌面端拉起时持有）。
#[derive(Default)]
struct ManagedBackendState(Mutex<BackendStateInner>);

impl Default for BackendStateInner {
    fn default() -> Self {
        Self {
            phase: BackendPhase::Idle,
            child: None,
            last_error: None,
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct InstanceLock {
    port: u16,
    token: String,
    pid: u32,
    #[serde(default)]
    exe_path: String,
    #[serde(default)]
    process_start_time: u64,
}

fn instance_lock_path() -> PathBuf {
    std::env::temp_dir().join("ai.godsworkbench.desktop.instance.json")
}

/// 唤醒状态共享结构：允许在 Tauri 窗口尚未完成初始化前安全捕获唤醒、退出与诊断请求。
struct WakeupState {
    app_handle: Mutex<Option<AppHandle>>,
    pending_wakeups: std::sync::atomic::AtomicUsize,
    pending_quit: std::sync::atomic::AtomicBool,
    lock_path: PathBuf,
}

impl WakeupState {
    fn new(lock_path: PathBuf) -> Self {
        Self {
            app_handle: Mutex::new(None),
            pending_wakeups: std::sync::atomic::AtomicUsize::new(0),
            pending_quit: std::sync::atomic::AtomicBool::new(false),
            lock_path,
        }
    }

    fn trigger_wakeup(&self) {
        if let Ok(guard) = self.app_handle.lock() {
            if let Some(app) = guard.as_ref() {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                    return;
                }
            }
        }
        self.pending_wakeups
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    }

    fn trigger_quit(&self) {
        let app = self.app_handle.lock().ok().and_then(|guard| guard.clone());
        if let Some(app) = app {
            release_own_instance_lock(&self.lock_path);
            let backend = app.state::<ManagedBackendState>();
            stop_managed_backend(&backend);
            app.exit(0);
            return;
        }
        self.pending_quit
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }

    fn trigger_find_nas(&self) {
        if let Ok(guard) = self.app_handle.lock() {
            if let Some(app) = guard.as_ref() {
                if let Some(registry) = app.try_state::<TrayMenuRegistry>() {
                    if let Ok(id_guard) = registry.find_nas_id.lock() {
                        if let Some(menu_id) = id_guard.as_deref() {
                            handle_tray_menu_event(app, menu_id, &self.lock_path);
                        }
                    }
                }
            }
        }
    }

    fn status_json(&self) -> Option<String> {
        let app = self
            .app_handle
            .lock()
            .ok()
            .and_then(|guard| guard.clone())?;
        let diagnostics = app.state::<ClientDiagnostics>();
        let backend = app.state::<ManagedBackendState>();
        let view = diagnostics_view(&app, &diagnostics, &backend).ok()?;
        serde_json::to_string(&view).ok()
    }

    fn attach_app(&self, app: AppHandle) {
        let pending_quit = self
            .pending_quit
            .swap(false, std::sync::atomic::Ordering::SeqCst);
        if pending_quit {
            release_own_instance_lock(&self.lock_path);
            let backend = app.state::<ManagedBackendState>();
            stop_managed_backend(&backend);
            app.exit(0);
            return;
        }

        let pending = self
            .pending_wakeups
            .swap(0, std::sync::atomic::Ordering::SeqCst);
        if pending > 0 {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }
        if let Ok(mut guard) = self.app_handle.lock() {
            *guard = Some(app);
        }
    }
}

enum SingleInstanceDecision {
    Primary {
        listener: std::net::TcpListener,
        token: String,
        wakeup_state: Arc<WakeupState>,
        lock_path: PathBuf,
    },
    SecondaryWoken,
    Error(String),
}

#[cfg(windows)]
fn is_process_alive(pid: u32) -> bool {
    extern "system" {
        fn OpenProcess(
            dwDesiredAccess: u32,
            bInheritHandle: i32,
            dwProcessId: u32,
        ) -> *mut std::ffi::c_void;
        fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
        fn WaitForSingleObject(hHandle: *mut std::ffi::c_void, dwMilliseconds: u32) -> u32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const SYNCHRONIZE: u32 = 0x00100000;
    const WAIT_TIMEOUT: u32 = 258;
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return false;
        }
        // OpenProcess still succeeds while another handle keeps a terminated
        // process object alive; wait on the handle to observe real termination.
        let wait = WaitForSingleObject(handle, 0);
        CloseHandle(handle);
        wait == WAIT_TIMEOUT
    }
}

#[cfg(not(windows))]
fn is_process_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(windows)]
#[repr(C)]
struct FileTime {
    dw_low_date_time: u32,
    dw_high_date_time: u32,
}

#[cfg(windows)]
extern "system" {
    fn GetCurrentProcess() -> *mut std::ffi::c_void;
    fn GetProcessTimes(
        hProcess: *mut std::ffi::c_void,
        lpCreationTime: *mut FileTime,
        lpExitTime: *mut FileTime,
        lpKernelTime: *mut FileTime,
        lpUserTime: *mut FileTime,
    ) -> i32;
    fn QueryFullProcessImageNameW(
        hProcess: *mut std::ffi::c_void,
        dwFlags: u32,
        lpExeName: *mut u16,
        lpdwSize: *mut u32,
    ) -> i32;
}

fn current_process_exe_path() -> String {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default()
}

#[cfg(windows)]
fn current_process_start_time() -> u64 {
    unsafe {
        let mut creation = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut exit = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut kernel = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut user = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        if GetProcessTimes(
            GetCurrentProcess(),
            &mut creation,
            &mut exit,
            &mut kernel,
            &mut user,
        ) != 0
        {
            ((creation.dw_high_date_time as u64) << 32) | (creation.dw_low_date_time as u64)
        } else {
            0
        }
    }
}

#[cfg(windows)]
fn get_process_identity(pid: u32) -> Option<(u64, String)> {
    extern "system" {
        fn OpenProcess(
            dwDesiredAccess: u32,
            bInheritHandle: i32,
            dwProcessId: u32,
        ) -> *mut std::ffi::c_void;
        fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut creation = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut exit = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut kernel = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let mut user = FileTime {
            dw_low_date_time: 0,
            dw_high_date_time: 0,
        };
        let start_time =
            if GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) != 0 {
                ((creation.dw_high_date_time as u64) << 32) | (creation.dw_low_date_time as u64)
            } else {
                0
            };

        let mut buffer = [0u16; 1024];
        let mut size = buffer.len() as u32;
        let exe_path = if QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) != 0
        {
            String::from_utf16_lossy(&buffer[..size as usize])
        } else {
            String::new()
        };

        CloseHandle(handle);
        Some((start_time, exe_path))
    }
}

#[cfg(not(windows))]
fn get_unix_process_start_time(pid: u32) -> u64 {
    // 1. Linux /proc/<pid>/stat 探测（第 22 个字段 starttime）
    if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        if let Some(last_paren) = stat.rfind(')') {
            let rest = &stat[last_paren + 1..];
            let fields: Vec<&str> = rest.split_whitespace().collect();
            // rest[0] 是 state(3)，所以 starttime(22) 位于 22 - 3 = 19
            if fields.len() > 19 {
                if let Ok(val) = fields[19].parse::<u64>() {
                    if val > 0 {
                        return val;
                    }
                }
            }
        }
    }

    // 2. macOS / BSD 等无 /proc 环境：使用 ps -p <pid> -o lstart=
    if let Ok(output) = std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "lstart="])
        .output()
    {
        if output.status.success() {
            let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !text.is_empty() {
                use std::hash::{Hash, Hasher};
                let mut hasher = std::collections::hash_map::DefaultHasher::new();
                text.hash(&mut hasher);
                let h = hasher.finish();
                return if h == 0 { 1 } else { h };
            }
        }
    }

    0
}

#[cfg(not(windows))]
fn current_process_start_time() -> u64 {
    get_unix_process_start_time(std::process::id())
}

#[cfg(not(windows))]
fn get_process_identity(pid: u32) -> Option<(u64, String)> {
    if !is_process_alive(pid) {
        return None;
    }
    let start_time = get_unix_process_start_time(pid);
    let exe_path = std::fs::read_link(format!("/proc/{pid}/exe"))
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| {
            std::process::Command::new("ps")
                .args(["-p", &pid.to_string(), "-o", "comm="])
                .output()
                .ok()
                .and_then(|out| {
                    if out.status.success() {
                        Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
                    } else {
                        None
                    }
                })
                .unwrap_or_default()
        });
    Some((start_time, exe_path))
}

fn is_lock_holder_alive_and_matched(lock: &InstanceLock) -> bool {
    // 1. 若目标 PID 在操作系统中已不存在，明确判定持有者已死亡
    if !is_process_alive(lock.pid) {
        return false;
    }

    // 2. 旧格式锁兼容（缺少身份字段）：
    // 若 lock 中没有记录 exe_path 且 process_start_time 为 0，而该进程确已证实仍在运行，
    // 此时身份不可核验。根据单实例铁律，视为存在存活实例，返回 true（明确报错拒绝双开，不删锁）！
    let is_legacy_lock = lock.exe_path.trim().is_empty() && lock.process_start_time == 0;
    if is_legacy_lock {
        return true;
    }

    // 3. 新格式锁：查询目标进程的真实运行时身份信息
    let Some((start_time, exe_path)) = get_process_identity(lock.pid) else {
        // PID 存活但无法获取身份信息（如受限系统进程），属于非本桌面端的 PID 复用
        return false;
    };

    // 4. 关键：启动时间精确裁决（具有最高优先级）
    if lock.process_start_time > 0 && start_time > 0 {
        if lock.process_start_time == start_time {
            return true; // 启动时间完全相同，绝对是同一进程实例
        } else {
            // 启动时间不一致：证明原持有者已退出，此 PID 无论是否被同名/同路径程序再次复用，
            // 均绝对属于 PID 复用！必须返回 false，严禁落入文件名或路径比较！
            return false;
        }
    }

    // 5. 若某一方未提供有效启动时间（降级容灾），比对规范化可执行文件路径
    if !lock.exe_path.is_empty() && !exe_path.is_empty() {
        let lock_norm = lock.exe_path.trim().to_lowercase();
        let target_norm = exe_path.trim().to_lowercase();
        if lock_norm == target_norm {
            return true;
        }
        let lock_name = Path::new(&lock_norm)
            .file_name()
            .and_then(|f| f.to_str())
            .unwrap_or("");
        let target_name = Path::new(&target_norm)
            .file_name()
            .and_then(|f| f.to_str())
            .unwrap_or("");
        if !lock_name.is_empty() && lock_name == target_name {
            return true;
        }
    }

    false
}

#[cfg(windows)]
fn report_fatal_error(message: &str) {
    extern "system" {
        fn MessageBoxW(
            hwnd: *mut std::ffi::c_void,
            text: *const u16,
            caption: *const u16,
            utype: u32,
        ) -> i32;
    }
    use std::os::windows::ffi::OsStrExt;
    let wide_title: Vec<u16> = std::ffi::OsStr::new("神工坊桌面端启动错误")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let wide_msg: Vec<u16> = std::ffi::OsStr::new(message)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            wide_msg.as_ptr(),
            wide_title.as_ptr(),
            0x10, // MB_ICONERROR
        );
    }
}

#[cfg(not(windows))]
fn report_fatal_error(message: &str) {
    eprintln!("神工坊桌面端启动错误: {message}");
}

fn try_instance_command(port: u16, token: &str, command: &str) -> Option<String> {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream =
        std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(1500)));
    use std::io::{BufRead, BufReader, Write};
    writeln!(stream, "{command} {token}").ok()?;
    let _ = stream.flush();
    let mut reader = BufReader::new(&mut stream);
    let mut reply = String::new();
    reader.read_line(&mut reply).ok()?;
    Some(reply.trim().to_string())
}

fn try_wake_existing_instance(port: u16, token: &str) -> bool {
    matches!(
        try_instance_command(port, token, "WAKEUP").as_deref(),
        Some("WAKEN")
    )
}

fn try_request_existing_quit(port: u16, token: &str) -> Result<(), String> {
    match try_instance_command(port, token, "QUIT").as_deref() {
        Some("BYE") => Ok(()),
        Some(other) => Err(format!("桌面端拒绝了退出请求（应答: {other}）")),
        None => Err("桌面端未响应退出请求".into()),
    }
}

fn try_request_existing_status(port: u16, token: &str) -> Result<String, String> {
    match try_instance_command(port, token, "STATUS") {
        Some(reply) if reply.starts_with("STATUS ") => Ok(reply["STATUS ".len()..].to_string()),
        Some(reply) if reply == "STATUS-UNAVAILABLE" => Err("桌面端诊断状态尚未就绪".into()),
        Some(other) => Err(format!("桌面端返回了无法识别的诊断应答: {other}")),
        None => Err("桌面端未响应诊断请求".into()),
    }
}

fn try_request_existing_find_nas(port: u16, token: &str) -> Result<(), String> {
    match try_instance_command(port, token, "FIND_NAS").as_deref() {
        Some("NAVIGATING") => Ok(()),
        Some(other) => Err(format!("桌面端拒绝了 Find NAS 请求（应答: {other}）")),
        None => Err("桌面端未响应 Find NAS 请求".into()),
    }
}

fn initialize_primary_listener(
    file: &mut std::fs::File,
    lock_path: &Path,
) -> Result<SingleInstanceDecision, String> {
    use std::io::Write;
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("Cannot bind ephemeral port for single instance: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("Cannot determine ephemeral listener port: {e}"))?
        .port();
    let token = format!(
        "{:x}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(1)
    );
    let lock_info = InstanceLock {
        port,
        token: token.clone(),
        pid: std::process::id(),
        exe_path: current_process_exe_path(),
        process_start_time: current_process_start_time(),
    };
    let content = serde_json::to_vec_pretty(&lock_info)
        .map_err(|e| format!("Cannot serialize instance lock: {e}"))?;
    file.write_all(&content)
        .and_then(|_| file.flush())
        .map_err(|e| format!("Cannot write instance lock data: {e}"))?;

    let listener_clone = listener
        .try_clone()
        .map_err(|e| format!("Cannot clone listener socket for wakeup thread: {e}"))?;

    let wakeup_state = Arc::new(WakeupState::new(lock_path.to_path_buf()));
    let wakeup_clone = wakeup_state.clone();
    let expected_token = token.clone();

    std::thread::spawn(move || {
        use std::io::{BufRead, BufReader, Write};
        for stream in listener_clone.incoming() {
            if let Ok(mut stream) = stream {
                let _ = stream.set_read_timeout(Some(Duration::from_millis(1000)));
                let _ = stream.set_write_timeout(Some(Duration::from_millis(1000)));
                let mut reader = BufReader::new(&mut stream);
                let mut line = String::new();
                if reader.read_line(&mut line).is_ok() {
                    let parts: Vec<&str> = line.trim().split_whitespace().collect();
                    if parts.len() == 2 && parts[1] == expected_token {
                        match parts[0] {
                            "WAKEUP" => {
                                // 优先写回应答并 flush，让发起方立即完成，不依赖 UI 或主线程状态
                                let _ = writeln!(stream, "WAKEN");
                                let _ = stream.flush();
                                wakeup_clone.trigger_wakeup();
                            }
                            "QUIT" => {
                                let _ = writeln!(stream, "BYE");
                                let _ = stream.flush();
                                wakeup_clone.trigger_quit();
                            }
                            "STATUS" => {
                                let reply = match wakeup_clone.status_json() {
                                    Some(json) => format!("STATUS {json}"),
                                    None => "STATUS-UNAVAILABLE".to_string(),
                                };
                                let _ = writeln!(stream, "{reply}");
                                let _ = stream.flush();
                            }
                            "FIND_NAS" => {
                                let _ = writeln!(stream, "NAVIGATING");
                                let _ = stream.flush();
                                wakeup_clone.trigger_find_nas();
                            }
                            _ => {}
                        }
                    }
                }
            }
        }
    });

    Ok(SingleInstanceDecision::Primary {
        listener,
        token,
        wakeup_state,
        lock_path: lock_path.to_path_buf(),
    })
}

fn arbitrate_single_instance(lock_path: &Path) -> SingleInstanceDecision {
    use std::fs::OpenOptions;

    let recovery_lock_path = lock_path.with_extension("recovering");

    for _round in 0..2 {
        // 第一阶段：原子排他独占创建主锁
        match OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(lock_path)
        {
            Ok(mut file) => {
                return match initialize_primary_listener(&mut file, lock_path) {
                    Ok(decision) => decision,
                    Err(e) => {
                        let _ = fs::remove_file(lock_path);
                        SingleInstanceDecision::Error(e)
                    }
                };
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                // 主锁文件已存在：尝试连接并唤醒（重试最多 15 次，总计 1.5 秒）
                let mut woken = false;
                let mut existing_lock: Option<InstanceLock> = None;
                for attempt in 0..15 {
                    if let Ok(content) = fs::read(lock_path) {
                        if let Ok(lock) = serde_json::from_slice::<InstanceLock>(&content) {
                            existing_lock = Some(lock.clone());
                            if try_wake_existing_instance(lock.port, &lock.token) {
                                woken = true;
                                break;
                            }
                        }
                    }
                    if attempt < 14 {
                        std::thread::sleep(Duration::from_millis(100));
                    }
                }

                if woken {
                    return SingleInstanceDecision::SecondaryWoken;
                }

                // 第二阶段：唤醒无响应。
                // 必须严格核实锁持有者是否确已不存在（或已死/身份不匹配）：
                // 若持有者存活且身份匹配，说明是真正的主实例正处于卡顿或未响应状态。
                // 铁律：绝不删锁，绝不起第二个客户端实例！向用户提示已有实例但无响应并以非零码退出。
                if let Some(ref lock) = existing_lock {
                    if is_lock_holder_alive_and_matched(lock) {
                        return SingleInstanceDecision::Error(format!(
                            "检测到已有一个神工坊桌面端实例正在运行（PID: {}），但未响应唤醒请求。请检查任务管理器或稍后重试。",
                            lock.pid
                        ));
                    }
                }

                // 锁持有者已死亡或为 PID 复用的无关进程，确认为遗留死锁或损坏锁。
                // 核心原子防护：绝不盲目删除主锁！所有并发进程转为原子竞争恢复排他锁
                match OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&recovery_lock_path)
                {
                    Ok(mut rec_file) => {
                        use std::io::Write;
                        let _ = writeln!(rec_file, "{}", std::process::id());
                        let _ = rec_file.flush();

                        // 抢到恢复锁后，做最后一次连通性与存活复核
                        if let Ok(content) = fs::read(lock_path) {
                            if let Ok(lock) = serde_json::from_slice::<InstanceLock>(&content) {
                                if try_wake_existing_instance(lock.port, &lock.token) {
                                    let _ = fs::remove_file(&recovery_lock_path);
                                    return SingleInstanceDecision::SecondaryWoken;
                                }
                                if is_lock_holder_alive_and_matched(&lock) {
                                    let _ = fs::remove_file(&recovery_lock_path);
                                    return SingleInstanceDecision::Error(format!(
                                        "检测到已有一个神工坊桌面端实例正在运行（PID: {}），但未响应唤醒请求。请检查任务管理器或稍后重试。",
                                        lock.pid
                                    ));
                                }
                            }
                        }

                        // 确定旧主锁已死亡，清理旧主锁并重建
                        let _ = fs::remove_file(lock_path);

                        let decision = match OpenOptions::new()
                            .read(true)
                            .write(true)
                            .create_new(true)
                            .open(lock_path)
                        {
                            Ok(mut new_file) => {
                                match initialize_primary_listener(&mut new_file, lock_path) {
                                    Ok(d) => d,
                                    Err(err) => {
                                        let _ = fs::remove_file(lock_path);
                                        SingleInstanceDecision::Error(err)
                                    }
                                }
                            }
                            Err(err) => SingleInstanceDecision::Error(format!(
                                "Failed to recreate primary lock during recovery: {err}"
                            )),
                        };

                        let _ = fs::remove_file(&recovery_lock_path);
                        return decision;
                    }
                    Err(rec_err) if rec_err.kind() == std::io::ErrorKind::AlreadyExists => {
                        // 其他并发进程已抢到恢复锁并在进行恢复！跟随等待恢复者写入新主锁（最多等待 2 秒）
                        for _ in 0..20 {
                            std::thread::sleep(Duration::from_millis(100));
                            if let Ok(content) = fs::read(lock_path) {
                                if let Ok(lock) = serde_json::from_slice::<InstanceLock>(&content) {
                                    if try_wake_existing_instance(lock.port, &lock.token) {
                                        return SingleInstanceDecision::SecondaryWoken;
                                    }
                                }
                            }
                        }
                        // 若 2 秒后恢复者仍未就绪（如恢复者异常崩溃），尝试清理孤儿恢复锁并进入下一轮
                        let _ = fs::remove_file(&recovery_lock_path);
                        continue;
                    }
                    Err(err) => {
                        return SingleInstanceDecision::Error(format!(
                            "Failed to acquire recovery lock: {err}"
                        ));
                    }
                }
            }
            Err(e) => {
                return SingleInstanceDecision::Error(format!(
                    "Failed to open instance lock file {:?}: {e}",
                    lock_path
                ));
            }
        }
    }

    SingleInstanceDecision::Error("Failed to arbitrate single instance after recovery.".into())
}

fn find_python_executable(backend_root: &Path) -> Result<PathBuf, String> {
    if let Ok(path_str) = std::env::var("GW_PYTHON") {
        let p = PathBuf::from(path_str.trim());
        if p.is_file() {
            return Ok(p);
        }
    }

    #[cfg(windows)]
    let venv_candidates = [
        backend_root.join("python").join("python.exe"),
        backend_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe"),
        backend_root.join("venv").join("Scripts").join("python.exe"),
    ];
    #[cfg(not(windows))]
    let venv_candidates = [
        backend_root.join("python").join("bin").join("python3"),
        backend_root.join(".venv").join("bin").join("python"),
        backend_root.join("venv").join("bin").join("python"),
    ];

    for candidate in &venv_candidates {
        if candidate.is_file() {
            return Ok(candidate.clone());
        }
    }

    #[cfg(windows)]
    let system_candidates = ["python.exe", "python"];
    #[cfg(not(windows))]
    let system_candidates = ["python3", "python"];

    for candidate in &system_candidates {
        if let Ok(output) = std::process::Command::new(candidate)
            .arg("--version")
            .output()
        {
            if output.status.success() {
                return Ok(PathBuf::from(candidate));
            }
        }
    }

    Err(
        "未找到 Python 解释器。请在系统环境变量配置 GW_PYTHON 或在项目目录下建立 .venv 虚拟环境。"
            .into(),
    )
}

fn looks_like_backend_root(path: &Path) -> bool {
    path.join("run.py").is_file() && path.join("gw").is_dir()
}

fn installed_backend_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            candidates.push(exe_dir.join("backend"));
            candidates.push(exe_dir.join("resources").join("backend"));
            candidates.push(
                exe_dir
                    .parent()
                    .unwrap_or(exe_dir)
                    .join("Resources")
                    .join("backend"),
            );
        }
    }
    candidates
}

fn find_backend_root() -> Result<PathBuf, String> {
    for var in ["GW_BACKEND_ROOT", "GW_ROOT_DIR"] {
        if let Ok(dir_str) = std::env::var(var) {
            let p = PathBuf::from(dir_str.trim());
            if looks_like_backend_root(&p) {
                return Ok(p);
            }
        }
    }

    for candidate in installed_backend_candidates() {
        if looks_like_backend_root(&candidate) {
            return Ok(candidate);
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        if looks_like_backend_root(&cwd) {
            return Ok(cwd);
        }
    }

    if let Ok(exe_path) = std::env::current_exe() {
        let mut current = exe_path.parent();
        for _ in 0..6 {
            if let Some(dir) = current {
                if looks_like_backend_root(dir) {
                    return Ok(dir.to_path_buf());
                }
                current = dir.parent();
            } else {
                break;
            }
        }
    }

    Err(
        "未找到神工坊后端根目录（未发现 run.py 与 gw 目录）。请配置 GW_BACKEND_ROOT 环境变量。"
            .into(),
    )
}

fn load_installed_manifest() -> Result<Option<BundleManifest>, String> {
    for candidate in installed_backend_candidates() {
        if compat::manifest_path(&candidate).exists() {
            return compat::load_bundle_manifest(&candidate);
        }
    }
    match find_backend_root() {
        Ok(root) => compat::load_bundle_manifest(&root),
        Err(_) => Ok(None),
    }
}

fn localappdata_root() -> Result<PathBuf, String> {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .ok_or_else(|| "无法确定本机数据目录：LOCALAPPDATA 不可用".to_string())
}

fn bootstrap_data_root_in(base: &Path) -> PathBuf {
    base.join("Gods Workbench")
}

fn bootstrap_data_root() -> Result<PathBuf, String> {
    Ok(bootstrap_data_root_in(&localappdata_root()?))
}

fn legacy_bootstrap_data_root_in(base: &Path) -> PathBuf {
    base.join("GodsWorkbench")
}

fn legacy_installer_data_root_in(base: &Path) -> PathBuf {
    base.join("Gods Workbench data")
}

fn has_existing_production_data_at(root: &Path) -> bool {
    if root.join("auth").join("auth.sqlite3").is_file() {
        return true;
    }
    for sub in ["data", "video"] {
        let dir = root.join(sub);
        if dir.is_dir() {
            if let Ok(mut entries) = fs::read_dir(&dir) {
                if entries.next().is_some() {
                    return true;
                }
            }
        }
    }
    false
}

fn read_pointer_data_location(bootstrap: &Path) -> Result<Option<PathBuf>, String> {
    let pointer_path = bootstrap.join("runtime-root.json");
    match fs::symlink_metadata(&pointer_path) {
        Ok(meta) => {
            if meta.file_type().is_symlink() {
                return Err("数据位置指针不允许符号链接".to_string());
            }
            let raw = fs::read_to_string(&pointer_path)
                .map_err(|_| "数据位置指针无法读取".to_string())?;
            let payload: serde_json::Value = serde_json::from_str(&raw)
                .map_err(|_| "数据位置指针内容不是合法 JSON".to_string())?;
            let schema_version = payload
                .get("schema_version")
                .and_then(|value| value.as_i64())
                .unwrap_or(0);
            if schema_version != 1 {
                return Err("数据位置指针 schema_version 不受支持".to_string());
            }
            let location = payload
                .get("data_location")
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| "数据位置指针缺少 data_location".to_string())?;
            let path = PathBuf::from(location);
            if !path.is_absolute() {
                return Err("数据位置指针路径必须是绝对路径".to_string());
            }
            Ok(Some(path))
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err("数据位置指针无法安全核验".to_string()),
    }
}

fn production_data_root_in(base: &Path) -> Result<PathBuf, String> {
    let new_bootstrap = bootstrap_data_root_in(base);
    let legacy_bootstrap = legacy_bootstrap_data_root_in(base);
    // 先校验新、旧两处指针：任一存在但损坏必须失败关闭，不因新目录存在 data 而跳过。
    let new_pointer = read_pointer_data_location(&new_bootstrap)?;
    let legacy_pointer = read_pointer_data_location(&legacy_bootstrap)?;
    if let Some(path) = new_pointer {
        return Ok(path);
    }
    if let Some(path) = legacy_pointer {
        return Ok(path);
    }
    let default_user_data = new_bootstrap.join("user data");
    if has_existing_production_data_at(&default_user_data) {
        return Ok(default_user_data);
    }
    if has_existing_production_data_at(&new_bootstrap) {
        return Ok(new_bootstrap);
    }
    if has_existing_production_data_at(&legacy_bootstrap) {
        return Ok(legacy_bootstrap);
    }
    let legacy_data = legacy_installer_data_root_in(base);
    if has_existing_production_data_at(&legacy_data) {
        return Ok(legacy_data);
    }
    Ok(default_user_data)
}

fn production_data_root() -> Result<PathBuf, String> {
    production_data_root_in(&localappdata_root()?)
}

fn install_directory() -> Result<PathBuf, String> {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .ok_or_else(|| "无法确定桌面端安装目录".to_string())
}

#[cfg(windows)]
const PATH_SEPARATOR: char = '\\';
#[cfg(not(windows))]
const PATH_SEPARATOR: char = '/';

fn strip_verbatim_prefix(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        let text = path.to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\") {
            if let Some(unc) = rest.strip_prefix(r"UNC\") {
                return PathBuf::from(format!(r"\\{unc}"));
            }
            return PathBuf::from(rest);
        }
    }
    path.to_path_buf()
}

fn canonicalize_with_missing(path: &Path) -> PathBuf {
    let mut missing: Vec<std::ffi::OsString> = Vec::new();
    let mut current = path.to_path_buf();
    loop {
        if let Ok(resolved) = fs::canonicalize(&current) {
            let mut result = strip_verbatim_prefix(&resolved);
            for part in missing.iter().rev() {
                result.push(part);
            }
            return result;
        }
        let Some(name) = current.file_name().map(|name| name.to_os_string()) else {
            return path.to_path_buf();
        };
        missing.push(name);
        if !current.pop() {
            return path.to_path_buf();
        }
    }
}

fn comparison_key(path: &Path) -> String {
    let canonical = strip_verbatim_prefix(path);
    let separator = PATH_SEPARATOR.to_string();
    let normalized = canonical.to_string_lossy().replace(['/', '\\'], &separator);
    let trimmed = normalized.trim_end_matches(PATH_SEPARATOR);
    let value = if trimmed.is_empty() {
        normalized.as_str()
    } else {
        trimmed
    };
    #[cfg(windows)]
    {
        value.to_lowercase()
    }
    #[cfg(not(windows))]
    {
        value.to_string()
    }
}

fn path_is_within(child: &Path, parent: &Path) -> bool {
    let child_key = comparison_key(child);
    let parent_key = comparison_key(parent);
    if child_key == parent_key {
        return true;
    }
    let prefix = if parent_key.ends_with(PATH_SEPARATOR) {
        parent_key
    } else {
        format!("{parent_key}{PATH_SEPARATOR}")
    };
    child_key.starts_with(&prefix)
}

fn paths_non_overlapping(left: &Path, right: &Path) -> bool {
    !path_is_within(left, right) && !path_is_within(right, left)
}

fn program_data_non_overlapping() -> Result<bool, String> {
    let install = canonicalize_with_missing(&install_directory()?);
    let bootstrap = canonicalize_with_missing(&bootstrap_data_root()?);
    let data = canonicalize_with_missing(&production_data_root()?);
    Ok(paths_non_overlapping(&install, &bootstrap) && paths_non_overlapping(&install, &data))
}

fn upgrade_journal_path() -> Option<PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())?;
    Some(
        base.join("ai.godsworkbench.desktop")
            .join("upgrade")
            .join("journal.txt"),
    )
}

fn commit_upgrade_journal_at(journal: &Path) {
    let Ok(state) = fs::read_to_string(journal) else {
        return;
    };
    if matches!(state.trim(), "installed" | "verified") {
        let _ = fs::write(journal, "committed");
    }
}

fn commit_upgrade_journal() {
    if let Some(journal) = upgrade_journal_path() {
        commit_upgrade_journal_at(&journal);
    }
}

fn new_handshake_client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(5))
        .no_proxy()
        .build()
        .map_err(|error| format!("无法创建本机握手客户端: {error}"))
}

fn update_diagnostics(diagnostics: &ClientDiagnostics, update: impl FnOnce(&mut DiagnosticsInner)) {
    if let Ok(mut guard) = diagnostics.0.lock() {
        update(&mut guard);
    }
}

fn record_manifest_result(
    diagnostics: &ClientDiagnostics,
    result: &Result<Option<BundleManifest>, String>,
) {
    update_diagnostics(diagnostics, |inner| match result {
        Ok(Some(manifest)) => {
            inner.bundled_backend_version = manifest.app_version();
            inner.manifest_error.clear();
        }
        Ok(None) => {
            inner.bundled_backend_version = "unknown".into();
            inner.manifest_error.clear();
        }
        Err(error) => {
            inner.bundled_backend_version = "unknown".into();
            inner.manifest_error = error.clone();
        }
    });
}

fn record_handshake(diagnostics: &ClientDiagnostics, report: &HandshakeReport) {
    update_diagnostics(diagnostics, |inner| {
        inner.handshake = Some(report.clone());
        inner.notice.clear();
        inner.last_error.clear();
    });
}

fn record_trusted(diagnostics: &ClientDiagnostics, status: &TrustedStatus) {
    update_diagnostics(diagnostics, |inner| {
        inner.trusted = status.clone();
    });
}

fn record_notice(diagnostics: &ClientDiagnostics, notice: &str) {
    update_diagnostics(diagnostics, |inner| {
        inner.notice = notice.to_string();
    });
}

fn record_error(diagnostics: &ClientDiagnostics, error: &str) {
    update_diagnostics(diagnostics, |inner| {
        inner.last_error = error.to_string();
        inner.notice.clear();
    });
}

struct TrustedAttempt {
    status: TrustedStatus,
    ticket: Option<String>,
}

fn trusted_unavailable(reason: impl Into<String>) -> TrustedAttempt {
    TrustedAttempt {
        status: TrustedStatus {
            available: false,
            auto_login: false,
            reason: reason.into(),
        },
        ticket: None,
    }
}

fn attempt_trusted_entry(
    report: &HandshakeReport,
    manifest: Option<&BundleManifest>,
    non_overlap: bool,
) -> TrustedAttempt {
    if !report.local {
        return trusted_unavailable("仅 loopback 本机后端支持受信登录");
    }
    if manifest.is_none() {
        return trusted_unavailable("开发源构建不签发本机票据");
    }
    if !report.verified {
        let summary = report.failure_summary();
        return trusted_unavailable(if summary.is_empty() {
            "本机后端未通过版本与能力验证".to_string()
        } else {
            format!("本机后端未通过版本与能力验证：{summary}")
        });
    }
    if !report.trusted_bootstrap_available {
        return trusted_unavailable("后端未提供受信本机兑换接口（QIN-630 未部署）");
    }
    if !non_overlap {
        return trusted_unavailable("安装目录与数据根存在包含关系");
    }
    let backend_root = match find_backend_root() {
        Ok(root) => root,
        Err(error) => return trusted_unavailable(error),
    };
    let python = match find_python_executable(&backend_root) {
        Ok(python) => python,
        Err(error) => return trusted_unavailable(error),
    };
    let auth_dir = match production_data_root() {
        Ok(root) => root.join("auth"),
        Err(error) => return trusted_unavailable(error),
    };
    match trusted::verify_acl(&python, &backend_root, &auth_dir) {
        Ok(acl) if acl.ok => {}
        Ok(acl) => return trusted_unavailable(acl.reason),
        Err(error) => return trusted_unavailable(error),
    }
    match trusted::issue_ticket(&python, &backend_root, "127.0.0.1", 2077) {
        Ok(ticket) => TrustedAttempt {
            status: TrustedStatus {
                available: true,
                auto_login: true,
                reason: String::new(),
            },
            ticket: Some(ticket),
        },
        Err(error) => trusted_unavailable(error),
    }
}

async fn trusted_attempt_for(
    report: &HandshakeReport,
    manifest: Option<BundleManifest>,
    non_overlap: bool,
) -> Result<TrustedAttempt, String> {
    let report = report.clone();
    tauri::async_runtime::spawn_blocking(move || {
        attempt_trusted_entry(&report, manifest.as_ref(), non_overlap)
    })
    .await
    .map_err(|error| format!("受信本机预检任务失败: {error}"))
}

fn local_notice_for(report: &HandshakeReport, manifest_present: bool) -> String {
    if report.verified {
        return String::new();
    }
    let summary = report.failure_summary();
    let prefix = if manifest_present {
        "本机后端为未验证实例，安装版不以兼容名义放行"
    } else {
        "开发源构建，仅用于本机开发"
    };
    if summary.is_empty() {
        format!("{prefix}，请使用密码登录。")
    } else {
        format!("{prefix}：{summary}。请使用密码登录或修复安装。")
    }
}

fn remote_failure_message(report: &HandshakeReport) -> String {
    let summary = report.failure_summary();
    if summary.is_empty() {
        "无法验证服务器兼容性。请升级服务器或改用匹配的桌面安装版。".to_string()
    } else {
        format!("无法验证服务器兼容性：{summary}。请升级服务器或改用匹配的桌面安装版。")
    }
}

#[derive(Debug, PartialEq, Eq)]
enum LocalPortStatus {
    HealthyInstance,
    OccupiedForeign,
    Available,
}

async fn check_local_port_2077(client: &Client) -> LocalPortStatus {
    let local_base = match Url::parse("http://127.0.0.1:2077/") {
        Ok(u) => u,
        Err(_) => return LocalPortStatus::Available,
    };
    if check_health(client, &local_base).await.is_ok() {
        return LocalPortStatus::HealthyInstance;
    }
    let is_occupied = tauri::async_runtime::spawn_blocking(|| {
        let addr = std::net::SocketAddr::from(([127, 0, 0, 1], 2077));
        std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
    })
    .await
    .unwrap_or(false);

    if is_occupied {
        LocalPortStatus::OccupiedForeign
    } else {
        LocalPortStatus::Available
    }
}

fn spawn_backend_process(
    python: &Path,
    backend_root: &Path,
) -> Result<std::process::Child, String> {
    let install_dir_value = install_directory()
        .unwrap_or_else(|_| backend_root.to_path_buf())
        .to_string_lossy()
        .to_string();
    let mut cmd = std::process::Command::new(python);
    cmd.current_dir(backend_root)
        .arg("run.py")
        .arg("--prod")
        .env("GW_HOST", "127.0.0.1")
        .env("GW_PORT", "2077")
        .env("GW_INSTALL_DIR", &install_dir_value)
        // 安装目录只读使用：避免运行期把 __pycache__ 写进程序目录并导致卸载残留。
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    cmd.spawn()
        .map_err(|error| format!("无法启动后端进程: {error}。请确认 Python 环境是否完备。"))
}

#[derive(Clone)]
struct ProcessOutputTracker {
    recent_stderr: Arc<Mutex<Vec<String>>>,
}

fn drain_process_output(child: &mut std::process::Child) -> ProcessOutputTracker {
    let recent_stderr = Arc::new(Mutex::new(Vec::new()));

    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            use std::io::{BufRead, BufReader};
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            while let Ok(n) = reader.read_line(&mut line) {
                if n == 0 {
                    break;
                }
                line.clear();
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let tracker = recent_stderr.clone();
        std::thread::spawn(move || {
            use std::io::{BufRead, BufReader};
            let mut reader = BufReader::new(stderr);
            let mut line = String::new();
            while let Ok(n) = reader.read_line(&mut line) {
                if n == 0 {
                    break;
                }
                let trimmed = line.trim_end().to_string();
                if let Ok(mut logs) = tracker.lock() {
                    if logs.len() >= 50 {
                        logs.remove(0);
                    }
                    logs.push(trimmed);
                }
                line.clear();
            }
        });
    }

    ProcessOutputTracker { recent_stderr }
}

#[cfg(test)]
async fn wait_for_backend_ready(
    client: &Client,
    child: &mut std::process::Child,
    tracker: &ProcessOutputTracker,
    base: &Url,
    timeout: Duration,
) -> Result<(), String> {
    let start = std::time::Instant::now();

    while start.elapsed() < timeout {
        if let Ok(Some(status)) = child.try_wait() {
            let mut err_msg = format!("后端进程提前退出（状态码: {status}）。");
            if let Ok(logs) = tracker.recent_stderr.lock() {
                if !logs.is_empty() {
                    err_msg.push_str(&format!(" 错误详情:\n{}", logs.join("\n")));
                }
            }
            return Err(err_msg);
        }

        if check_health(client, base).await.is_ok() {
            return Ok(());
        }

        tauri::async_runtime::spawn_blocking(|| {
            std::thread::sleep(Duration::from_millis(250));
        })
        .await
        .map_err(|e| e.to_string())?;
    }

    // 超时强杀孤儿子进程，防止进程残留常驻
    let _ = child.kill();
    let _ = child.wait();

    let mut timeout_msg =
        "等待本机后端健康检查超时（30秒内未就绪）。建议备用方式：可双击 start.bat 尝试启动并查看控制台日志。".to_string();
    if let Ok(logs) = tracker.recent_stderr.lock() {
        if !logs.is_empty() {
            timeout_msg.push_str(&format!(" 错误日志摘要:\n{}", logs.join("\n")));
        }
    }

    Err(timeout_msg)
}

fn stop_managed_backend(state: &ManagedBackendState) {
    if let Ok(mut guard) = state.0.lock() {
        guard.phase = BackendPhase::Idle;
        if let Some(mut child) = guard.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(Debug, Deserialize)]
struct Healthz {
    status: String,
}

fn ensure_local_ui(window: &WebviewWindow) -> Result<(), String> {
    let current = window
        .url()
        .map_err(|error| format!("Cannot read window URL: {error}"))?;
    let host = current.host_str().unwrap_or_default().to_ascii_lowercase();
    let allowed = host == "tauri.localhost"
        || (host == "localhost" && matches!(current.scheme(), "tauri" | "asset"));
    if allowed {
        Ok(())
    } else {
        Err("Desktop discovery commands are only available from the local client screen.".into())
    }
}

fn origin_of(url: &Url) -> String {
    format!(
        "{}://{}",
        url.scheme(),
        url.host_str().unwrap_or_default().to_ascii_lowercase()
    ) + &url
        .port()
        .map(|port| format!(":{port}"))
        .unwrap_or_default()
}

fn origins_match(current: &str, saved: &str) -> bool {
    match (Url::parse(current), Url::parse(saved)) {
        (Ok(current_url), Ok(saved_url)) => origin_of(&current_url) == origin_of(&saved_url),
        _ => false,
    }
}

/// 客户端桥接命令只对本地发现页、本地 2077 服务端以及当前已连接的服务器页面开放（QIN-432 / QIN-626 AC10）。
fn ensure_client_bridge_window(app: &AppHandle, window: &WebviewWindow) -> Result<(), String> {
    if ensure_local_ui(window).is_ok() {
        return Ok(());
    }
    let current = window
        .url()
        .map_err(|error| format!("Cannot read window URL: {error}"))?;
    let host = current.host_str().unwrap_or_default().to_ascii_lowercase();
    let port = current.port().unwrap_or(80);
    if (host == "127.0.0.1" || host == "localhost" || host == "::1") && port == 2077 {
        return Ok(());
    }
    let saved = last_saved_server_url(app)?;
    let saved = saved.ok_or_else(|| {
        "Client bridge commands are only available for the connected server page.".to_string()
    })?;
    if origins_match(current.as_str(), &saved) {
        Ok(())
    } else {
        Err("Client bridge commands are only available for the connected server page.".into())
    }
}

fn last_saved_server_url(app: &AppHandle) -> Result<Option<String>, String> {
    let path = config_path(app)?;
    let content = match fs::read(path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Cannot read client settings: {error}")),
    };
    let config: PersistedConfig = serde_json::from_slice(&content)
        .map_err(|error| format!("Saved server settings are invalid: {error}"))?;
    Ok(Some(config.server_url))
}

fn is_allowed_local_host(host: &str) -> bool {
    if let Ok(address) = host.parse::<IpAddr>() {
        return match address {
            IpAddr::V4(ip) => ip.is_private() || ip.is_loopback() || ip.is_link_local(),
            IpAddr::V6(ip) => ip.is_loopback() || ip.is_unique_local(),
        };
    }
    let normalized = host.trim_end_matches(".").to_ascii_lowercase();
    normalized == "localhost"
        || normalized.ends_with(".local")
        || (!normalized.contains(".") && !normalized.contains(":"))
}

fn normalize_server_url(input: &str) -> Result<Url, String> {
    let mut url = Url::parse(input.trim()).map_err(|_| "Enter a valid server URL.".to_string())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("Only HTTP and HTTPS server URLs are supported.".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(
            "Server URLs must not include credentials, query strings, or fragments.".into(),
        );
    }
    if url.path() != "/" {
        return Err("Enter the server root URL without a path.".into());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "The server URL must include a host.".to_string())?;
    if !is_allowed_local_host(host) {
        return Err("Enter a private LAN address, localhost, or a local NAS hostname.".into());
    }
    if url.port().is_none() && url.scheme() == "http" {
        url.set_port(Some(2077))
            .map_err(|_| "Cannot set the default NAS port.".to_string())?;
    }
    url.set_path("/");
    Ok(url)
}

fn new_http_client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_millis(350))
        .timeout(Duration::from_millis(650))
        .no_proxy()
        .build()
        .map_err(|error| format!("Cannot create LAN HTTP client: {error}"))
}

fn bounded_subnet_hosts(ip: Ipv4Addr, netmask: Ipv4Addr) -> Vec<Ipv4Addr> {
    let mask = u32::from(netmask);
    let prefix = mask.count_ones();
    let expected_mask = if prefix == 0 {
        0
    } else {
        u32::MAX << (32 - prefix)
    };
    if mask != expected_mask {
        return Vec::new();
    }
    let scan_prefix = prefix.max(24);
    let scan_mask = u32::MAX << (32 - scan_prefix);
    let network = u32::from(ip) & scan_mask;
    let block_size = 1u64 << (32 - scan_prefix);
    (1..block_size.saturating_sub(1))
        .map(|offset| Ipv4Addr::from(network + offset as u32))
        .filter(|candidate| *candidate != ip)
        .collect()
}

fn scan_targets() -> Vec<Ipv4Addr> {
    let mut targets = BTreeSet::new();
    targets.insert(Ipv4Addr::LOCALHOST);
    if let Ok(interfaces) = get_if_addrs() {
        for interface in interfaces {
            if let IfAddr::V4(address) = interface.addr {
                if address.ip.is_private() {
                    targets.extend(bounded_subnet_hosts(address.ip, address.netmask));
                }
            }
        }
    }
    targets.into_iter().collect()
}

async fn probe_ip(client: Client, ip: Ipv4Addr) -> Option<DiscoveryResult> {
    let base = Url::parse(&format!("http://{ip}:2077/")).ok()?;
    let mut health_url = base.clone();
    health_url.set_path("/healthz");
    let response = client.get(health_url).send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let health = response.json::<Healthz>().await.ok()?;
    if health.status != "ok" {
        return None;
    }
    Some(DiscoveryResult {
        name: format!("Gods Workbench NAS ({ip})"),
        url: base.as_str().trim_end_matches("/").to_string(),
    })
}

async fn check_health(client: &Client, base: &Url) -> Result<(), String> {
    let mut health_url = base.clone();
    health_url.set_path("/healthz");
    let response = client
        .get(health_url)
        .send()
        .await
        .map_err(|_| "NAS did not respond to /healthz on port 2077.".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "NAS health check returned HTTP {}.",
            response.status()
        ));
    }
    let health = response
        .json::<Healthz>()
        .await
        .map_err(|_| "NAS health response was not valid JSON.".to_string())?;
    if health.status != "ok" {
        return Err("NAS health status is not ok.".into());
    }
    Ok(())
}

fn client_settings_dir_from(
    appdata_override: Option<std::ffi::OsString>,
    identifier: &str,
    fallback: Result<PathBuf, tauri::Error>,
) -> Result<PathBuf, String> {
    // 优先尊重 APPDATA 环境变量，确保自动化验收隔离模式下客户端配置落入沙箱而不触碰真实用户漫游目录
    if let Some(appdata) = appdata_override.filter(|v| !v.is_empty()) {
        return Ok(PathBuf::from(appdata).join(identifier));
    }
    fallback.map_err(|error| format!("Cannot locate the client settings directory: {error}"))
}

fn client_settings_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = client_settings_dir_from(
        std::env::var_os("APPDATA"),
        app.config().identifier.as_str(),
        app.path().app_config_dir(),
    )?;
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Cannot create the client settings directory: {error}"))?;
    Ok(directory)
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = client_settings_dir(app)?;
    Ok(directory.join("server.json"))
}

fn save_server_url(app: &AppHandle, server_url: &str) -> Result<(), String> {
    let path = config_path(app)?;
    let config = PersistedConfig {
        server_url: server_url.trim_end_matches("/").to_string(),
    };
    let content = serde_json::to_vec_pretty(&config)
        .map_err(|error| format!("Cannot encode client settings: {error}"))?;
    fs::write(path, content).map_err(|error| format!("Cannot save client settings: {error}"))
}

const ALLOWED_CLIENT_SETTING_KEYS: [&str; 1] = ["upload_source_dir"];

fn client_setting_key_is_allowed(key: &str) -> bool {
    ALLOWED_CLIENT_SETTING_KEYS.contains(&key)
}

fn client_config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = client_settings_dir(app)?;
    Ok(directory.join("client.json"))
}

fn load_client_config_from(path: &Path) -> ClientConfig {
    match fs::read(path) {
        Ok(content) => serde_json::from_slice(&content).unwrap_or_default(),
        Err(_) => ClientConfig::default(),
    }
}

fn save_client_config_to(path: &Path, config: &ClientConfig) -> Result<(), String> {
    let content = serde_json::to_vec_pretty(config)
        .map_err(|error| format!("Cannot encode client settings: {error}"))?;
    fs::write(path, content).map_err(|error| format!("Cannot save client settings: {error}"))
}

#[tauri::command]
async fn pick_client_folder(
    app: AppHandle,
    window: WebviewWindow,
    title: Option<String>,
    initial_dir: Option<String>,
) -> Result<Option<String>, String> {
    ensure_client_bridge_window(&app, &window)?;
    let mut builder = app.dialog().file();
    builder = builder.set_title(title.unwrap_or_else(|| "选择文件夹".to_string()));
    if let Some(dir) = initial_dir
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        builder = builder.set_directory(dir);
    }
    let picked = tauri::async_runtime::spawn_blocking(move || builder.blocking_pick_folder())
        .await
        .map_err(|error| format!("Folder dialog task failed: {error}"))?;
    Ok(picked.map(|path| path.to_string()))
}

#[tauri::command]
async fn pick_client_upload_files(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, ClientSessionState>,
    default_dir: Option<String>,
) -> Result<Vec<String>, String> {
    ensure_client_bridge_window(&app, &window)?;
    let mut builder = app.dialog().file();
    builder = builder.set_title("选择要上传的素材");
    if let Some(dir) = default_dir
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        builder = builder.set_directory(dir);
    }
    builder = builder.add_filter(
        "媒体文件",
        &[
            "png", "jpg", "jpeg", "webp", "gif", "bmp", "mp4", "mov", "webm", "mkv", "mp3", "wav",
            "flac", "m4a",
        ],
    );
    let picked = tauri::async_runtime::spawn_blocking(move || builder.blocking_pick_files())
        .await
        .map_err(|error| format!("File dialog task failed: {error}"))?;
    let paths: Vec<String> = picked
        .map(|files| files.iter().map(|path| path.to_string()).collect())
        .unwrap_or_default();
    let mut allowed = state
        .0
        .lock()
        .map_err(|error| format!("Client session state is unavailable: {error}"))?;
    *allowed = paths.iter().map(PathBuf::from).collect();
    Ok(paths)
}

#[tauri::command]
fn read_client_file(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, ClientSessionState>,
    path: String,
) -> Result<tauri::ipc::Response, String> {
    ensure_client_bridge_window(&app, &window)?;
    let candidate = PathBuf::from(&path);
    let allowed = state
        .0
        .lock()
        .map_err(|error| format!("Client session state is unavailable: {error}"))?;
    if !allowed.contains(&candidate) {
        return Err("Only files picked in this session can be read.".into());
    }
    drop(allowed);
    let bytes =
        fs::read(&candidate).map_err(|error| format!("Cannot read picked file: {error}"))?;
    Ok(tauri::ipc::Response::new(bytes))
}

#[tauri::command]
fn get_client_setting(
    app: AppHandle,
    window: WebviewWindow,
    key: String,
) -> Result<Option<String>, String> {
    ensure_client_bridge_window(&app, &window)?;
    if !client_setting_key_is_allowed(&key) {
        return Err(format!("Unknown client setting key: {key}"));
    }
    let path = client_config_path(&app)?;
    let config = load_client_config_from(&path);
    Ok(match key.as_str() {
        "upload_source_dir" => config
            .upload_source_dir
            .filter(|value| !value.trim().is_empty()),
        _ => None,
    })
}

#[tauri::command]
fn set_client_setting(
    app: AppHandle,
    window: WebviewWindow,
    key: String,
    value: Option<String>,
) -> Result<(), String> {
    ensure_client_bridge_window(&app, &window)?;
    if !client_setting_key_is_allowed(&key) {
        return Err(format!("Unknown client setting key: {key}"));
    }
    let path = client_config_path(&app)?;
    let mut config = load_client_config_from(&path);
    match key.as_str() {
        "upload_source_dir" => {
            config.upload_source_dir = value
                .map(|text| text.trim().to_string())
                .filter(|text| !text.is_empty());
        }
        _ => {}
    }
    save_client_config_to(&path, &config)
}

#[tauri::command]
async fn discover_lan(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Vec<DiscoveryResult>, String> {
    ensure_client_bridge_window(&app, &window)?;
    let client = new_http_client()?;
    let probes = scan_targets()
        .into_iter()
        .map(|ip| probe_ip(client.clone(), ip));
    let mut servers = join_all(probes)
        .await
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    servers.sort_by(|left, right| left.url.cmp(&right.url));
    Ok(servers)
}

#[tauri::command]
fn last_server(app: AppHandle, window: WebviewWindow) -> Result<Option<String>, String> {
    ensure_client_bridge_window(&app, &window)?;
    let path = config_path(&app)?;
    let content = match fs::read(path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Cannot read client settings: {error}")),
    };
    let config: PersistedConfig = serde_json::from_slice(&content)
        .map_err(|error| format!("Saved server settings are invalid: {error}"))?;
    let url = normalize_server_url(&config.server_url)?;
    Ok(Some(url.as_str().trim_end_matches("/").to_string()))
}

#[tauri::command]
async fn connect_to_server(
    app: AppHandle,
    window: WebviewWindow,
    diagnostics: State<'_, ClientDiagnostics>,
    server_url: String,
) -> Result<LaunchResult, String> {
    let result = connect_to_server_inner(&app, &window, &diagnostics, &server_url).await;
    if let Err(error) = &result {
        record_error(&diagnostics, error);
    }
    result
}

async fn connect_to_server_inner(
    app: &AppHandle,
    window: &WebviewWindow,
    diagnostics: &ClientDiagnostics,
    server_url: &str,
) -> Result<LaunchResult, String> {
    ensure_client_bridge_window(app, window)?;
    let base = normalize_server_url(server_url)?;
    let manifest_result = load_installed_manifest();
    record_manifest_result(diagnostics, &manifest_result);
    let manifest = manifest_result?;
    let non_overlap = match manifest.as_ref() {
        Some(_) => {
            let ok = program_data_non_overlapping()?;
            update_diagnostics(diagnostics, |inner| {
                inner.program_data_non_overlapping = Some(ok);
            });
            if !ok {
                return Err(
                    "安装目录与数据根存在包含关系，已拒绝连接。请卸载后重新安装到独立目录。".into(),
                );
            }
            true
        }
        None => false,
    };

    let client = new_handshake_client()?;
    let report = compat::evaluate(&client, &base, manifest.as_ref()).await?;
    record_handshake(diagnostics, &report);

    if !report.verified {
        if !report.local {
            return Err(remote_failure_message(&report));
        }
        let notice = local_notice_for(&report, manifest.is_some());
        record_trusted(
            diagnostics,
            &TrustedStatus {
                available: false,
                auto_login: false,
                reason: "本机后端未验证，不签发受信票据".into(),
            },
        );
        record_notice(diagnostics, &notice);
        save_server_url(app, base.as_str())?;
        return Ok(LaunchResult {
            destination: trusted::destination_without_ticket(&base),
            auto_login: false,
            notice,
        });
    }

    if report.local {
        commit_upgrade_journal();
    }

    save_server_url(app, base.as_str())?;

    if report.local && manifest.is_some() && report.trusted_bootstrap_available {
        let attempt = trusted_attempt_for(&report, manifest.clone(), non_overlap).await?;
        record_trusted(diagnostics, &attempt.status);
        if let Some(ticket) = attempt.ticket {
            let destination = trusted::destination_with_ticket(&base, &ticket)?;
            let notice = "已通过受信本机一次性票据自动进入。".to_string();
            record_notice(diagnostics, &notice);
            return Ok(LaunchResult {
                destination,
                auto_login: true,
                notice,
            });
        }
        let notice = format!(
            "自动进入不可用：{}。请使用密码登录。",
            attempt.status.reason
        );
        record_notice(diagnostics, &notice);
        return Ok(LaunchResult {
            destination: trusted::destination_without_ticket(&base),
            auto_login: false,
            notice,
        });
    }

    let reason = if !report.local {
        "远程/NAS 连接永不注入本机票据"
    } else if manifest.is_none() {
        "开发源构建不签发本机票据"
    } else {
        "后端未提供受信本机兑换接口"
    };
    record_trusted(
        diagnostics,
        &TrustedStatus {
            available: false,
            auto_login: false,
            reason: reason.into(),
        },
    );
    let notice = if report.local {
        format!("{reason}，请使用密码登录。")
    } else {
        String::new()
    };
    record_notice(diagnostics, &notice);
    Ok(LaunchResult {
        destination: trusted::destination_without_ticket(&base),
        auto_login: false,
        notice,
    })
}

#[tauri::command]
async fn launch_local_backend(
    app: AppHandle,
    window: WebviewWindow,
    diagnostics: State<'_, ClientDiagnostics>,
    backend_state: State<'_, ManagedBackendState>,
) -> Result<LaunchResult, String> {
    let result = launch_local_backend_inner(&app, &window, &diagnostics, &backend_state).await;
    if let Err(error) = &result {
        record_error(&diagnostics, error);
    }
    result
}

async fn launch_local_backend_inner(
    app: &AppHandle,
    window: &WebviewWindow,
    diagnostics: &ClientDiagnostics,
    backend_state: &ManagedBackendState,
) -> Result<LaunchResult, String> {
    ensure_client_bridge_window(app, window)?;
    let manifest_result = load_installed_manifest();
    record_manifest_result(diagnostics, &manifest_result);
    let manifest = manifest_result?;
    let non_overlap = match manifest.as_ref() {
        Some(_) => {
            let ok = program_data_non_overlapping()?;
            update_diagnostics(diagnostics, |inner| {
                inner.program_data_non_overlapping = Some(ok);
            });
            if !ok {
                return Err(
                    "安装目录与数据根存在包含关系，已拒绝启动。请卸载后重新安装到独立目录。".into(),
                );
            }
            true
        }
        None => false,
    };

    let client = new_http_client()?;

    match check_local_port_2077(&client).await {
        LocalPortStatus::HealthyInstance => {
            return finalize_local_connection(app, diagnostics, manifest.as_ref(), non_overlap)
                .await;
        }
        LocalPortStatus::OccupiedForeign => {
            return Err("端口 2077 已被其他程序占用，无法启动本机后端。请先检查并关闭占用 2077 端口的程序。".into());
        }
        LocalPortStatus::Available => {}
    }

    enum LaunchAction {
        PerformSpawn,
        WaitForExisting,
    }

    let action = {
        let mut guard = backend_state
            .0
            .lock()
            .map_err(|e| format!("Managed backend state unavailable: {e}"))?;

        if let Some(ref mut child) = guard.child {
            match child.try_wait() {
                Ok(Some(_)) => {
                    guard.child = None;
                    guard.phase = BackendPhase::Idle;
                }
                Ok(None) => {}
                Err(_) => {
                    guard.child = None;
                    guard.phase = BackendPhase::Idle;
                }
            }
        }

        match guard.phase {
            BackendPhase::Idle | BackendPhase::Failed => {
                guard.phase = BackendPhase::Launching;
                guard.last_error = None;
                LaunchAction::PerformSpawn
            }
            BackendPhase::Launching => LaunchAction::WaitForExisting,
            BackendPhase::Running => {
                if guard.child.is_some() {
                    LaunchAction::WaitForExisting
                } else {
                    guard.phase = BackendPhase::Launching;
                    guard.last_error = None;
                    LaunchAction::PerformSpawn
                }
            }
        }
    };

    match action {
        LaunchAction::PerformSpawn => {
            let backend_root = match find_backend_root() {
                Ok(r) => r,
                Err(e) => {
                    if let Ok(mut g) = backend_state.0.lock() {
                        g.phase = BackendPhase::Failed;
                        g.last_error = Some(e.clone());
                    }
                    return Err(e);
                }
            };
            let python = match find_python_executable(&backend_root) {
                Ok(p) => p,
                Err(e) => {
                    if let Ok(mut g) = backend_state.0.lock() {
                        g.phase = BackendPhase::Failed;
                        g.last_error = Some(e.clone());
                    }
                    return Err(e);
                }
            };

            let mut child = match spawn_backend_process(&python, &backend_root) {
                Ok(c) => c,
                Err(e) => {
                    if let Ok(mut g) = backend_state.0.lock() {
                        g.phase = BackendPhase::Failed;
                        g.last_error = Some(e.clone());
                    }
                    return Err(e);
                }
            };

            let tracker = drain_process_output(&mut child);

            // 关键：在 spawn 成功的一瞬间，立即将子进程句柄登记到托管列表，防止启动中退出导致漏杀
            {
                let mut g = backend_state
                    .0
                    .lock()
                    .map_err(|e| format!("Managed backend state unavailable: {e}"))?;
                g.phase = BackendPhase::Launching;
                g.child = Some(child);
            }

            let local_base = Url::parse("http://127.0.0.1:2077/").unwrap();
            let start = std::time::Instant::now();
            let timeout = Duration::from_secs(30);

            loop {
                if start.elapsed() >= timeout {
                    let mut g = backend_state.0.lock().map_err(|e| e.to_string())?;
                    g.phase = BackendPhase::Failed;
                    if let Some(mut c) = g.child.take() {
                        let _ = c.kill();
                        let _ = c.wait();
                    }
                    let mut timeout_msg =
                        "等待本机后端健康检查超时（30秒内未就绪）。建议备用方式：可双击 start.bat 尝试启动并查看控制台日志。".to_string();
                    if let Ok(logs) = tracker.recent_stderr.lock() {
                        if !logs.is_empty() {
                            timeout_msg.push_str(&format!(" 错误日志摘要:\n{}", logs.join("\n")));
                        }
                    }
                    g.last_error = Some(timeout_msg.clone());
                    return Err(timeout_msg);
                }

                // 随时检测子进程存活（若被托盘退出取走则立即中止）
                {
                    let mut g = backend_state.0.lock().map_err(|e| e.to_string())?;
                    match g.child.as_mut() {
                        Some(c) => {
                            if let Ok(Some(status)) = c.try_wait() {
                                let mut err_msg = format!("后端进程提前退出（状态码: {status}）。");
                                if let Ok(logs) = tracker.recent_stderr.lock() {
                                    if !logs.is_empty() {
                                        err_msg
                                            .push_str(&format!(" 错误详情:\n{}", logs.join("\n")));
                                    }
                                }
                                g.child = None;
                                g.phase = BackendPhase::Failed;
                                g.last_error = Some(err_msg.clone());
                                return Err(err_msg);
                            }
                        }
                        None => {
                            return Err("桌面客户端已请求退出，后端启动已中止。".into());
                        }
                    }
                }

                if check_health(&client, &local_base).await.is_ok() {
                    if let Ok(mut g) = backend_state.0.lock() {
                        g.phase = BackendPhase::Running;
                    }
                    break;
                }

                tauri::async_runtime::spawn_blocking(|| {
                    std::thread::sleep(Duration::from_millis(250));
                })
                .await
                .map_err(|e| e.to_string())?;
            }
        }
        LaunchAction::WaitForExisting => {
            let local_base = Url::parse("http://127.0.0.1:2077/").unwrap();
            let start = std::time::Instant::now();
            let timeout = Duration::from_secs(30);
            let mut ready = false;

            while start.elapsed() < timeout {
                // 若拉起方已失败或取消，立即中止等待返回错误，绝不空等 30 秒
                if let Ok(g) = backend_state.0.lock() {
                    if g.phase == BackendPhase::Failed {
                        let err = g
                            .last_error
                            .clone()
                            .unwrap_or_else(|| "本机后端启动失败。".into());
                        return Err(err);
                    }
                    if g.phase == BackendPhase::Idle {
                        return Err("本机后端启动已被取消。".into());
                    }
                }

                if check_health(&client, &local_base).await.is_ok() {
                    ready = true;
                    break;
                }

                tauri::async_runtime::spawn_blocking(|| {
                    std::thread::sleep(Duration::from_millis(250));
                })
                .await
                .map_err(|e| e.to_string())?;
            }

            if !ready {
                return Err("等待本机后端就绪超时。请稍后重试。".into());
            }
        }
    }

    finalize_local_connection(app, diagnostics, manifest.as_ref(), non_overlap).await
}

async fn finalize_local_connection(
    app: &AppHandle,
    diagnostics: &ClientDiagnostics,
    manifest: Option<&BundleManifest>,
    non_overlap: bool,
) -> Result<LaunchResult, String> {
    let base = Url::parse("http://127.0.0.1:2077/").map_err(|_| "本机后端地址无效".to_string())?;
    let client = new_handshake_client()?;
    let report = compat::evaluate(&client, &base, manifest).await?;
    record_handshake(diagnostics, &report);
    save_server_url(app, "http://127.0.0.1:2077")?;

    if !report.verified {
        let notice = local_notice_for(&report, manifest.is_some());
        record_trusted(
            diagnostics,
            &TrustedStatus {
                available: false,
                auto_login: false,
                reason: "本机后端未验证，不签发受信票据".into(),
            },
        );
        record_notice(diagnostics, &notice);
        return Ok(LaunchResult {
            destination: trusted::destination_without_ticket(&base),
            auto_login: false,
            notice,
        });
    }

    commit_upgrade_journal();

    if manifest.is_some() && report.trusted_bootstrap_available {
        let attempt = trusted_attempt_for(&report, manifest.cloned(), non_overlap).await?;
        record_trusted(diagnostics, &attempt.status);
        if let Some(ticket) = attempt.ticket {
            let destination = trusted::destination_with_ticket(&base, &ticket)?;
            let notice = "已通过受信本机一次性票据自动进入。".to_string();
            record_notice(diagnostics, &notice);
            return Ok(LaunchResult {
                destination,
                auto_login: true,
                notice,
            });
        }
        let notice = format!(
            "自动进入不可用：{}。请使用密码登录。",
            attempt.status.reason
        );
        record_notice(diagnostics, &notice);
        return Ok(LaunchResult {
            destination: trusted::destination_without_ticket(&base),
            auto_login: false,
            notice,
        });
    }

    let reason = if manifest.is_none() {
        "开发源构建不签发本机票据"
    } else {
        "后端未提供受信本机兑换接口"
    };
    record_trusted(
        diagnostics,
        &TrustedStatus {
            available: false,
            auto_login: false,
            reason: reason.into(),
        },
    );
    let notice = format!("{reason}，请使用密码登录。");
    record_notice(diagnostics, &notice);
    Ok(LaunchResult {
        destination: trusted::destination_without_ticket(&base),
        auto_login: false,
        notice,
    })
}

fn diagnostics_view(
    app: &AppHandle,
    diagnostics: &ClientDiagnostics,
    backend: &ManagedBackendState,
) -> Result<DiagnosticsView, String> {
    let needs_manifest = {
        let guard = diagnostics
            .0
            .lock()
            .map_err(|_| "客户端诊断状态不可用".to_string())?;
        guard.bundled_backend_version.is_empty() && guard.manifest_error.is_empty()
    };
    if needs_manifest {
        let manifest_result = load_installed_manifest();
        record_manifest_result(diagnostics, &manifest_result);
    }
    let guard = diagnostics
        .0
        .lock()
        .map_err(|_| "客户端诊断状态不可用".to_string())?;
    let (backend_phase, backend_pid) = backend
        .0
        .lock()
        .map(|state| {
            (
                state.phase.as_str().to_string(),
                state.child.as_ref().map(|child| child.id()),
            )
        })
        .unwrap_or_else(|_| ("unknown".into(), None));
    Ok(DiagnosticsView {
        desktop_version: app.package_info().version.to_string(),
        build_mode: if cfg!(debug_assertions) {
            "debug".into()
        } else {
            "release".into()
        },
        bundled_backend_version: if guard.bundled_backend_version.is_empty() {
            "unknown".into()
        } else {
            guard.bundled_backend_version.clone()
        },
        capability_profile: compat::CAPABILITY_PROFILE.into(),
        capability_digest: compat::capability_digest(),
        manifest_error: guard.manifest_error.clone(),
        handshake: guard.handshake.clone(),
        trusted: guard.trusted.clone(),
        program_data_non_overlapping: guard.program_data_non_overlapping,
        notice: guard.notice.clone(),
        last_error: guard.last_error.clone(),
        backend_phase,
        backend_pid,
        install_dir: install_directory()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default(),
        data_location: production_data_root()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default(),
    })
}

#[tauri::command]
fn get_diagnostics(
    app: AppHandle,
    window: WebviewWindow,
    diagnostics: State<'_, ClientDiagnostics>,
    backend: State<'_, ManagedBackendState>,
) -> Result<DiagnosticsView, String> {
    ensure_client_bridge_window(&app, &window)?;
    diagnostics_view(&app, &diagnostics, &backend)
}

enum CliCommand {
    Run,
    Quit,
    FindNas,
    Diagnostics(PathBuf),
    Invalid(String),
}

fn parse_cli_command<I>(args: I) -> CliCommand
where
    I: IntoIterator<Item = String>,
{
    let mut args = args.into_iter();
    let Some(first) = args.next() else {
        return CliCommand::Run;
    };
    match first.as_str() {
        "--quit" => {
            if args.next().is_some() {
                CliCommand::Invalid("--quit 不接受额外参数".into())
            } else {
                CliCommand::Quit
            }
        }
        "--find-nas" => {
            if args.next().is_some() {
                CliCommand::Invalid("--find-nas 不接受额外参数".into())
            } else {
                CliCommand::FindNas
            }
        }
        "--diagnostics" => match args.next() {
            Some(path) if !path.trim().is_empty() => {
                if args.next().is_some() {
                    CliCommand::Invalid("--diagnostics 只接受一个输出路径".into())
                } else {
                    CliCommand::Diagnostics(PathBuf::from(path))
                }
            }
            _ => CliCommand::Invalid("--diagnostics 需要输出路径".into()),
        },
        other => CliCommand::Invalid(format!("未知的命令行参数: {other}")),
    }
}

fn write_control_json(path: &Path, value: serde_json::Value) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(&value)
        .map_err(|error| format!("无法序列化诊断结果: {error}"))?;
    fs::write(path, bytes).map_err(|error| format!("无法写入诊断结果: {error}"))
}

fn run_quit_command() -> i32 {
    let lock_path = instance_lock_path();
    let content = match fs::read(&lock_path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return 0,
        Err(error) => {
            eprintln!("无法读取桌面端实例锁: {error}");
            return 1;
        }
    };
    let lock: InstanceLock = match serde_json::from_slice(&content) {
        Ok(lock) => lock,
        Err(error) => {
            eprintln!("桌面端实例锁无法解析: {error}");
            return 1;
        }
    };
    if !is_process_alive(lock.pid) {
        return 0;
    }
    if let Err(error) = try_request_existing_quit(lock.port, &lock.token) {
        eprintln!("{error}");
        return 1;
    }
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    while std::time::Instant::now() < deadline {
        if !is_process_alive(lock.pid) && !lock_path.exists() {
            return 0;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    eprintln!("桌面端在退出请求后仍未完成清理");
    1
}

fn run_find_nas_command() -> i32 {
    let lock_path = instance_lock_path();
    let content = match fs::read(&lock_path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            eprintln!("桌面端未运行，无法触发 Find NAS");
            return 1;
        }
        Err(error) => {
            eprintln!("无法读取桌面端实例锁: {error}");
            return 1;
        }
    };
    let lock: InstanceLock = match serde_json::from_slice(&content) {
        Ok(lock) => lock,
        Err(error) => {
            eprintln!("桌面端实例锁无法解析: {error}");
            return 1;
        }
    };
    if !is_process_alive(lock.pid) {
        eprintln!("桌面端进程已失效");
        return 1;
    }
    if let Err(error) = try_request_existing_find_nas(lock.port, &lock.token) {
        eprintln!("{error}");
        return 1;
    }
    0
}

fn run_diagnostics_command(path: &Path) -> i32 {
    let lock_path = instance_lock_path();
    let content = match fs::read(&lock_path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let payload = serde_json::json!({ "running": false });
            return match write_control_json(path, payload) {
                Ok(()) => 0,
                Err(error) => {
                    eprintln!("{error}");
                    1
                }
            };
        }
        Err(error) => {
            eprintln!("无法读取桌面端实例锁: {error}");
            return 1;
        }
    };
    let lock: InstanceLock = match serde_json::from_slice(&content) {
        Ok(lock) => lock,
        Err(error) => {
            let payload = serde_json::json!({
                "running": false,
                "invalid_lock": true,
                "error": error.to_string(),
            });
            let _ = write_control_json(path, payload);
            return 1;
        }
    };
    if !is_process_alive(lock.pid) {
        let payload = serde_json::json!({ "running": false, "stale_lock": true });
        return match write_control_json(path, payload) {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("{error}");
                1
            }
        };
    }
    let status = match try_request_existing_status(lock.port, &lock.token) {
        Ok(status) => status,
        Err(error) => {
            let payload = serde_json::json!({ "running": true, "error": error });
            let _ = write_control_json(path, payload);
            return 1;
        }
    };
    let diagnostics: serde_json::Value =
        serde_json::from_str(&status).unwrap_or(serde_json::Value::Null);
    let payload = serde_json::json!({ "running": true, "diagnostics": diagnostics });
    match write_control_json(path, payload) {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

pub const TRAY_MENU_ID_FIND_NAS: &str = "scan";

struct TrayMenuRegistry {
    find_nas_id: Mutex<Option<String>>,
}

pub const TRAY_FIND_NAS_SETTINGS_URL: &str =
    "http://127.0.0.1:2077/static/pages/settings.html?section=general&action=scan-nas";

fn handle_tray_menu_event(app: &AppHandle, menu_id: &str, active_lock_path: &Path) -> bool {
    match menu_id {
        "open" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
            true
        }
        TRAY_MENU_ID_FIND_NAS => {
            if let Some(window) = app.get_webview_window("main") {
                handle_find_nas_navigation(&window);
            }
            true
        }
        "quit" => {
            release_own_instance_lock(active_lock_path);
            let state = app.state::<ManagedBackendState>();
            stop_managed_backend(&state);
            app.exit(0);
            true
        }
        _ => false,
    }
}

fn handle_find_nas_navigation(window: &WebviewWindow) {
    let is_local_workbench = if let Ok(u) = window.url() {
        let host = u.host_str().unwrap_or_default().to_ascii_lowercase();
        let port = u.port().unwrap_or(80);
        (host == "127.0.0.1" || host == "localhost" || host == "::1") && port == 2077
    } else {
        false
    };

    if is_local_workbench {
        if let Ok(settings_url) = Url::parse(TRAY_FIND_NAS_SETTINGS_URL) {
            let _ = window.navigate(settings_url);
        }
    } else if let Ok(mut scan_url) = window.url() {
        scan_url.set_query(Some("rescan=1"));
        let _ = window.navigate(scan_url);
    }
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}

fn make_tray_icon() -> Image<'static> {
    const SIZE: u32 = 32;
    const DARK: [u8; 4] = [9, 10, 13, 255];
    const GOLD: [u8; 4] = [223, 195, 132, 255];
    const ORANGE: [u8; 4] = [251, 90, 21, 255];
    const CLEAR: [u8; 4] = [0, 0, 0, 0];
    let mut pixels = vec![0; (SIZE * SIZE * 4) as usize];
    for y in 0..SIZE {
        for x in 0..SIZE {
            let border = x < 2 || x >= 30 || y < 2 || y >= 30;
            let letter_g = ((y >= 7 && y < 10 && x >= 7 && x < 21)
                || (x >= 6 && x < 9 && y >= 8 && y < 24)
                || (y >= 22 && y < 25 && x >= 7 && x < 21)
                || (x >= 18 && x < 21 && y >= 16 && y < 23)
                || (y >= 15 && y < 18 && x >= 13 && x < 21))
                && !(y == 7 && x == 7)
                && !(y == 24 && x == 7);
            let apostrophe = (y >= 7 && y <= 8 && x >= 25 && x <= 26)
                || (y >= 9 && y <= 10 && x >= 24 && x <= 25)
                || (y >= 11 && y <= 12 && x >= 23 && x <= 24);
            let color = if apostrophe {
                ORANGE
            } else if border || letter_g {
                GOLD
            } else if x >= 2 && x < 30 && y >= 2 && y < 30 {
                DARK
            } else {
                CLEAR
            };
            let offset = ((y * SIZE + x) * 4) as usize;
            pixels[offset..offset + 4].copy_from_slice(&color);
        }
    }
    Image::new_owned(pixels, SIZE, SIZE)
}

fn main() {
    match parse_cli_command(std::env::args().skip(1)) {
        CliCommand::Quit => std::process::exit(run_quit_command()),
        CliCommand::FindNas => std::process::exit(run_find_nas_command()),
        CliCommand::Diagnostics(path) => std::process::exit(run_diagnostics_command(&path)),
        CliCommand::Invalid(message) => {
            eprintln!("{message}");
            std::process::exit(2);
        }
        CliCommand::Run => {}
    }

    let lock_path = instance_lock_path();
    let (_listener, _token, wakeup_state, active_lock_path) =
        match arbitrate_single_instance(&lock_path) {
            SingleInstanceDecision::SecondaryWoken => {
                std::process::exit(0);
            }
            SingleInstanceDecision::Primary {
                listener,
                token,
                wakeup_state,
                lock_path,
            } => (listener, token, wakeup_state, lock_path),
            SingleInstanceDecision::Error(err) => {
                report_fatal_error(&err);
                std::process::exit(1);
            }
        };

    let run_lock_path = active_lock_path.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(ClientSessionState(Mutex::new(HashSet::new())))
        .manage(ManagedBackendState::default())
        .manage(ClientDiagnostics::default())
        .invoke_handler(tauri::generate_handler![
            discover_lan,
            last_server,
            connect_to_server,
            launch_local_backend,
            get_diagnostics,
            pick_client_folder,
            pick_client_upload_files,
            read_client_file,
            get_client_setting,
            set_client_setting
        ])
        .setup(move |app| {
            wakeup_state.attach_app(app.handle().clone());

            let window = app
                .get_webview_window("main")
                .ok_or_else(|| std::io::Error::other("Missing main window"))?;
            let hide_window = window.clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = hide_window.hide();
                }
            });

            let open = MenuItem::with_id(app, "open", "Open Workbench", true, None::<&str>)?;
            let scan =
                MenuItem::with_id(app, TRAY_MENU_ID_FIND_NAS, "Find NAS", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            app.manage(TrayMenuRegistry {
                find_nas_id: Mutex::new(Some(scan.id().as_ref().to_string())),
            });
            let menu = Menu::with_items(app, &[&open, &scan, &quit])?;
            TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(true)
                .tooltip("Gods Workbench")
                .icon(make_tray_icon())
                .on_menu_event(move |app, event| {
                    let _ = handle_tray_menu_event(app, event.id().as_ref(), &active_lock_path);
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Failed to launch Gods Workbench desktop client")
        .run(move |app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                release_own_instance_lock(&run_lock_path);
                let state = app.state::<ManagedBackendState>();
                stop_managed_backend(&state);
            }
        });
}

fn release_own_instance_lock(path: &Path) {
    if let Ok(content) = fs::read(path) {
        if let Ok(lock) = serde_json::from_slice::<InstanceLock>(&content) {
            if lock.pid == std::process::id() {
                let _ = fs::remove_file(path);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tray_find_nas_settings_url_contract() {
        let parsed =
            Url::parse(TRAY_FIND_NAS_SETTINGS_URL).expect("valid tray find nas settings url");
        assert_eq!(parsed.scheme(), "http");
        assert_eq!(parsed.host_str(), Some("127.0.0.1"));
        assert_eq!(parsed.port(), Some(2077));
        assert_eq!(parsed.path(), "/static/pages/settings.html");
        let query_pairs: std::collections::HashMap<_, _> =
            parsed.query_pairs().into_owned().collect();
        assert_eq!(
            query_pairs.get("section").map(String::as_str),
            Some("general")
        );
        assert_eq!(
            query_pairs.get("action").map(String::as_str),
            Some("scan-nas")
        );
    }

    #[test]
    fn test_make_tray_icon_includes_brand_colors_and_apostrophe() {
        let icon = make_tray_icon();
        assert_eq!(icon.width(), 32);
        assert_eq!(icon.height(), 32);
        let rgba = icon.rgba();
        assert_eq!(rgba.len(), 32 * 32 * 4);

        let pixel_at = |x: usize, y: usize| -> [u8; 4] {
            let idx = (y * 32 + x) * 4;
            [rgba[idx], rgba[idx + 1], rgba[idx + 2], rgba[idx + 3]]
        };

        const ORANGE: [u8; 4] = [251, 90, 21, 255];
        const GOLD: [u8; 4] = [223, 195, 132, 255];
        const DARK: [u8; 4] = [9, 10, 13, 255];

        // 1. 验证外边框与内衬背景
        assert_eq!(pixel_at(0, 0), GOLD, "outer border pixel should be gold");
        assert_eq!(pixel_at(1, 1), GOLD, "outer border pixel should be gold");
        assert_eq!(pixel_at(4, 4), DARK, "inner background should be dark");
        assert_eq!(pixel_at(30, 30), GOLD, "outer border pixel should be gold");

        // 2. 验证橙色撇号倾斜几何位置（右上角倾斜梯形切角特征）
        assert_eq!(pixel_at(25, 7), ORANGE, "apostrophe top-left pixel");
        assert_eq!(pixel_at(26, 8), ORANGE, "apostrophe top-right pixel");
        assert_eq!(pixel_at(24, 9), ORANGE, "apostrophe mid pixel");
        assert_eq!(pixel_at(23, 11), ORANGE, "apostrophe bottom-left pixel");

        // 3. 验证切角 G 的关键特征：中线槽与切角缺口
        assert_eq!(
            pixel_at(7, 7),
            DARK,
            "G corner notch should be cut out (dark background)"
        );
        assert_eq!(pixel_at(14, 16), GOLD, "G inner horizontal bar");
        assert_eq!(pixel_at(7, 16), GOLD, "G left vertical stem");

        // 4. 统计全局像素分布与对比度
        let mut orange_count = 0;
        let mut gold_count = 0;
        let mut dark_count = 0;

        for pixel in rgba.chunks_exact(4) {
            match pixel {
                [251, 90, 21, 255] => orange_count += 1,
                [223, 195, 132, 255] => gold_count += 1,
                [9, 10, 13, 255] => dark_count += 1,
                _ => {}
            }
        }

        assert_eq!(
            orange_count, 12,
            "tray icon must contain exactly 12 orange apostrophe pixels defining the tilted trapezoid"
        );
        assert!(
            gold_count >= 100,
            "tray icon must contain gold G and border"
        );
        assert!(dark_count >= 400, "tray icon must contain dark background");
    }

    #[test]
    fn test_window_icon_binary_format_and_pixel_parity() {
        // 验证生成 Windows ICO 的二进制格式与 DIB 位图规范
        const SIZE: u32 = 32;
        const IMAGE_BYTES: u32 = SIZE * SIZE * 4 + SIZE * 4;
        const GOLD: [u8; 4] = [223, 195, 132, 255];
        const DARK: [u8; 4] = [9, 10, 13, 255];
        const ORANGE: [u8; 4] = [251, 90, 21, 255];

        let mut dib = Vec::with_capacity(40 + IMAGE_BYTES as usize);
        dib.extend_from_slice(&40_u32.to_le_bytes());
        dib.extend_from_slice(&(SIZE as i32).to_le_bytes());
        dib.extend_from_slice(&((SIZE * 2) as i32).to_le_bytes());
        dib.extend_from_slice(&1_u16.to_le_bytes());
        dib.extend_from_slice(&32_u16.to_le_bytes());
        dib.extend_from_slice(&0_u32.to_le_bytes());
        dib.extend_from_slice(&IMAGE_BYTES.to_le_bytes());
        dib.extend_from_slice(&0_i32.to_le_bytes());
        dib.extend_from_slice(&0_i32.to_le_bytes());
        dib.extend_from_slice(&0_u32.to_le_bytes());
        dib.extend_from_slice(&0_u32.to_le_bytes());

        for y in (0..SIZE).rev() {
            for x in 0..SIZE {
                let border = x < 2 || x >= 30 || y < 2 || y >= 30;
                let letter_g = ((y >= 7 && y < 10 && x >= 7 && x < 21)
                    || (x >= 6 && x < 9 && y >= 8 && y < 24)
                    || (y >= 22 && y < 25 && x >= 7 && x < 21)
                    || (x >= 18 && x < 21 && y >= 16 && y < 23)
                    || (y >= 15 && y < 18 && x >= 13 && x < 21))
                    && !(y == 7 && x == 7)
                    && !(y == 24 && x == 7);
                let apostrophe = (y >= 7 && y <= 8 && x >= 25 && x <= 26)
                    || (y >= 9 && y <= 10 && x >= 24 && x <= 25)
                    || (y >= 11 && y <= 12 && x >= 23 && x <= 24);
                let [red, green, blue, alpha] = if apostrophe {
                    ORANGE
                } else if border || letter_g {
                    GOLD
                } else {
                    DARK
                };
                dib.extend_from_slice(&[blue, green, red, alpha]);
            }
        }
        dib.resize(dib.len() + (SIZE * 4) as usize, 0);

        let mut icon = Vec::with_capacity(22 + dib.len());
        icon.extend_from_slice(&0_u16.to_le_bytes());
        icon.extend_from_slice(&1_u16.to_le_bytes());
        icon.extend_from_slice(&1_u16.to_le_bytes());
        icon.extend_from_slice(&[SIZE as u8, SIZE as u8, 0, 0]);
        icon.extend_from_slice(&1_u16.to_le_bytes());
        icon.extend_from_slice(&32_u16.to_le_bytes());
        icon.extend_from_slice(&(dib.len() as u32).to_le_bytes());
        icon.extend_from_slice(&22_u32.to_le_bytes());
        icon.extend_from_slice(&dib);

        assert_eq!(
            &icon[0..4],
            &[0, 0, 1, 0],
            "ICO header magic must be 0x0000, 0x0001"
        );
        assert_eq!(&icon[4..6], &[1, 0], "ICO image count must be 1");
        assert_eq!(icon[6], 32, "ICO entry width must be 32");
        assert_eq!(icon[7], 32, "ICO entry height must be 32");
        assert_eq!(&icon[18..22], &[22, 0, 0, 0], "DIB data offset must be 22");

        let orange_bgra = [21, 90, 251, 255];
        let mut found_orange = 0;
        for chunk in dib[40..40 + 32 * 32 * 4].chunks_exact(4) {
            if chunk == orange_bgra {
                found_orange += 1;
            }
        }
        assert_eq!(
            found_orange, 12,
            "ICO DIB must contain exactly 12 orange BGRA pixels matching tray icon"
        );
    }

    #[test]
    fn broad_lan_subnet_is_limited_to_local_slash_24() {
        let hosts = bounded_subnet_hosts(
            Ipv4Addr::new(192, 168, 4, 12),
            Ipv4Addr::new(255, 255, 0, 0),
        );
        assert_eq!(hosts.len(), 253);
        assert!(hosts.contains(&Ipv4Addr::new(192, 168, 4, 1)));
        assert!(hosts.contains(&Ipv4Addr::new(192, 168, 4, 254)));
        assert!(!hosts.contains(&Ipv4Addr::new(192, 168, 4, 12)));
        assert!(!hosts.contains(&Ipv4Addr::new(192, 168, 5, 1)));
    }

    #[test]
    fn narrow_lan_subnet_excludes_network_broadcast_and_local_host() {
        let hosts = bounded_subnet_hosts(
            Ipv4Addr::new(192, 168, 4, 200),
            Ipv4Addr::new(255, 255, 255, 128),
        );
        assert_eq!(hosts.len(), 125);
        assert!(hosts.contains(&Ipv4Addr::new(192, 168, 4, 129)));
        assert!(!hosts.contains(&Ipv4Addr::new(192, 168, 4, 128)));
        assert!(!hosts.contains(&Ipv4Addr::new(192, 168, 4, 255)));
        assert!(!hosts.contains(&Ipv4Addr::new(192, 168, 4, 200)));
    }

    #[test]
    fn non_contiguous_subnet_masks_are_ignored() {
        let hosts = bounded_subnet_hosts(
            Ipv4Addr::new(192, 168, 4, 12),
            Ipv4Addr::new(255, 0, 255, 0),
        );
        assert!(hosts.is_empty());
    }

    #[test]
    fn server_urls_default_to_nas_port_and_reject_non_local_hosts() {
        let url = normalize_server_url("http://gw-nas.local").unwrap();
        assert_eq!(url.as_str(), "http://gw-nas.local:2077/");
        let secure_url = normalize_server_url("https://nas.local/").unwrap();
        assert_eq!(secure_url.as_str(), "https://nas.local/");
        assert!(normalize_server_url("https://nas.local:8443/").is_ok());
        assert!(normalize_server_url("http://localhost:2077/").is_ok());
        assert!(normalize_server_url("http://example.com").is_err());
    }

    #[test]
    fn server_urls_never_accept_credentials_paths_or_queries() {
        assert!(normalize_server_url("http://user:secret@nas.local:2077/").is_err());
        assert!(normalize_server_url("http://nas.local:2077/api/").is_err());
        assert!(normalize_server_url("http://nas.local:2077/?token=secret").is_err());
    }

    #[test]
    fn client_setting_keys_are_whitelisted() {
        assert!(client_setting_key_is_allowed("upload_source_dir"));
        assert!(!client_setting_key_is_allowed("server_url"));
        assert!(!client_setting_key_is_allowed(""));
    }

    #[test]
    fn client_config_roundtrips_upload_source_dir() {
        let directory = std::env::temp_dir().join(format!(
            "gw-desktop-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|value| value.as_nanos())
                .unwrap_or_default()
        ));
        fs::create_dir_all(&directory).expect("create temp dir");
        let path = directory.join("client.json");
        // 缺失文件按默认配置处理，不报错。
        assert_eq!(load_client_config_from(&path), ClientConfig::default());
        let mut config = ClientConfig::default();
        config.upload_source_dir = Some("<客户端来源目录>".to_string());
        save_client_config_to(&path, &config).expect("save client config");
        assert_eq!(
            load_client_config_from(&path).upload_source_dir,
            Some("<客户端来源目录>".to_string())
        );
        // 损坏文件按默认配置处理，重启后可恢复。
        fs::write(&path, b"not json").expect("corrupt client config");
        assert_eq!(load_client_config_from(&path), ClientConfig::default());
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn bridge_origins_match_only_same_origin() {
        assert!(origins_match(
            "http://nas.local:2077/static/pages/index.html",
            "http://nas.local:2077/"
        ));
        assert!(!origins_match(
            "http://nas.local:2077/static/pages/index.html",
            "http://evil.example:2077/"
        ));
        assert!(!origins_match(
            "http://nas.local:2077/static/pages/index.html",
            "https://nas.local:2077/"
        ));
        assert!(!origins_match("not a url", "http://nas.local:2077/"));
    }

    #[test]
    fn instance_lock_serializes_and_deserializes() {
        let lock = InstanceLock {
            port: 2077,
            token: "test_token_123".to_string(),
            pid: 42,
            exe_path: "C:\\app\\gw.exe".into(),
            process_start_time: 123456,
        };
        let encoded = serde_json::to_vec(&lock).expect("serialize lock");
        let decoded: InstanceLock = serde_json::from_slice(&encoded).expect("deserialize lock");
        assert_eq!(decoded.port, 2077);
        assert_eq!(decoded.token, "test_token_123");
        assert_eq!(decoded.pid, 42);
        assert_eq!(decoded.exe_path, "C:\\app\\gw.exe");
        assert_eq!(decoded.process_start_time, 123456);
    }

    #[test]
    fn stop_managed_backend_is_noop_when_empty() {
        let state = ManagedBackendState::default();
        stop_managed_backend(&state);
        let guard = state.0.lock().unwrap();
        assert_eq!(guard.phase, BackendPhase::Idle);
        assert!(guard.child.is_none());
    }

    #[test]
    fn test_managed_backend_stopping_during_launching_kills_child() {
        let state = ManagedBackendState::default();
        let child = std::process::Command::new("python")
            .arg("-c")
            .arg("import time; time.sleep(30)")
            .spawn()
            .expect("spawn sleeping process");

        {
            let mut guard = state.0.lock().unwrap();
            guard.phase = BackendPhase::Launching;
            guard.child = Some(child);
        }

        // 模拟用户在拉起阶段点击托盘 Quit，验证处于 Launching 状态的子进程也会被彻底停止
        stop_managed_backend(&state);

        let guard = state.0.lock().unwrap();
        assert_eq!(guard.phase, BackendPhase::Idle);
        assert!(guard.child.is_none());
    }

    #[test]
    fn test_process_liveness_detection() {
        assert!(is_process_alive(std::process::id()));
        assert!(!is_process_alive(99_999_999));
    }

    #[cfg(windows)]
    #[test]
    fn terminated_process_with_open_handle_is_not_alive() {
        let mut child = std::process::Command::new("cmd.exe")
            .args(["/C", "exit", "0"])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn short-lived process");
        let pid = child.id();
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while child.try_wait().expect("query helper process").is_none() {
            assert!(std::time::Instant::now() < deadline, "helper did not exit");
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !is_process_alive(pid),
            "terminated process with an open handle must not count as alive"
        );
        drop(child);
    }

    #[test]
    fn test_single_instance_wakeup_behavior() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind listener");
        let port = listener.local_addr().expect("local addr").port();
        let token = "test_wakeup_token_456".to_string();
        let expected_token = token.clone();

        // 启动后台线程模拟主实例接收唤醒请求
        let handle = std::thread::spawn(move || {
            use std::io::{BufRead, BufReader, Write};
            if let Ok((mut stream, _)) = listener.accept() {
                let mut reader = BufReader::new(&mut stream);
                let mut line = String::new();
                if reader.read_line(&mut line).is_ok() {
                    let parts: Vec<&str> = line.trim().split_whitespace().collect();
                    if parts.len() == 2 && parts[0] == "WAKEUP" && parts[1] == expected_token {
                        let _ = writeln!(stream, "WAKEN");
                        let _ = stream.flush();
                        return true;
                    }
                }
            }
            false
        });

        // 调用客户端唤醒函数
        let woken = try_wake_existing_instance(port, &token);
        assert!(woken, "Instance should be successfully woken");
        let server_verified = handle.join().expect("join server thread");
        assert!(server_verified, "Server thread verified wakeup command");
    }

    #[test]
    fn test_cli_control_commands_are_parsed_strictly() {
        assert!(matches!(
            parse_cli_command(std::iter::empty::<String>()),
            CliCommand::Run
        ));
        assert!(matches!(
            parse_cli_command(["--quit".to_string()]),
            CliCommand::Quit
        ));
        assert!(matches!(
            parse_cli_command(["--find-nas".to_string()]),
            CliCommand::FindNas
        ));
        match parse_cli_command(["--diagnostics".to_string(), "status.json".to_string()]) {
            CliCommand::Diagnostics(path) => assert_eq!(path, PathBuf::from("status.json")),
            _ => panic!("diagnostics command should be recognized"),
        }
        assert!(matches!(
            parse_cli_command(["--diagnostics".to_string()]),
            CliCommand::Invalid(_)
        ));
        assert!(matches!(
            parse_cli_command(["--quit".to_string(), "extra".to_string()]),
            CliCommand::Invalid(_)
        ));
        assert!(matches!(
            parse_cli_command(["--find-nas".to_string(), "extra".to_string()]),
            CliCommand::Invalid(_)
        ));
        assert!(matches!(
            parse_cli_command(["--unknown".to_string()]),
            CliCommand::Invalid(_)
        ));
    }

    #[test]
    fn test_instance_control_quit_and_status_protocol() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind listener");
        let port = listener.local_addr().expect("local addr").port();
        let token = "test_control_token_789".to_string();
        let expected_token = token.clone();

        let handle = std::thread::spawn(move || {
            use std::io::{BufRead, BufReader, Write};
            let mut commands = Vec::new();
            for (index, stream) in listener.incoming().enumerate() {
                let Ok(mut stream) = stream else { break };
                let mut reader = BufReader::new(&mut stream);
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    break;
                }
                commands.push(line.trim().to_string());
                let parts: Vec<&str> = line.trim().split_whitespace().collect();
                if parts.len() != 2 || parts[1] != expected_token {
                    break;
                }
                let reply = match parts[0] {
                    "QUIT" => "BYE",
                    "STATUS" => "STATUS {\"backend_phase\":\"running\"}",
                    "FIND_NAS" => "NAVIGATING",
                    _ => break,
                };
                let _ = writeln!(stream, "{reply}");
                let _ = stream.flush();
                if index == 2 {
                    break;
                }
            }
            commands
        });

        try_request_existing_quit(port, &token).expect("quit request");
        let status = try_request_existing_status(port, &token).expect("status request");
        assert_eq!(status, "{\"backend_phase\":\"running\"}");
        try_request_existing_find_nas(port, &token).expect("find nas request");
        let commands = handle.join().expect("join control server");
        assert_eq!(
            commands,
            vec![
                format!("QUIT {token}"),
                format!("STATUS {token}"),
                format!("FIND_NAS {token}")
            ]
        );
    }

    #[test]
    fn test_concurrent_instance_arbitration_guarantees_single_primary() {
        let test_dir = std::env::temp_dir().join(format!(
            "gw_arb_test_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::create_dir_all(&test_dir);
        let lock_path = test_dir.join("concurrent.lock");

        let num_threads = 12;
        let barrier = Arc::new(std::sync::Barrier::new(num_threads));
        let primary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let secondary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let error_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let mut handles = Vec::new();
        for _ in 0..num_threads {
            let b = barrier.clone();
            let lp = lock_path.clone();
            let p_cnt = primary_count.clone();
            let s_cnt = secondary_count.clone();
            let e_cnt = error_count.clone();

            handles.push(std::thread::spawn(move || {
                b.wait();
                match arbitrate_single_instance(&lp) {
                    SingleInstanceDecision::Primary { .. } => {
                        p_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::SecondaryWoken => {
                        s_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::Error(err) => {
                        eprintln!("Arbitration error: {err}");
                        e_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                }
            }));
        }

        for h in handles {
            h.join().expect("join arbitration thread");
        }

        let p = primary_count.load(std::sync::atomic::Ordering::SeqCst);
        let s = secondary_count.load(std::sync::atomic::Ordering::SeqCst);
        let e = error_count.load(std::sync::atomic::Ordering::SeqCst);

        assert_eq!(
            e, 0,
            "There should be 0 errors during concurrent arbitration"
        );
        assert_eq!(p, 1, "There MUST be exactly 1 primary instance created");
        assert_eq!(
            s, 11,
            "All 11 concurrent loser threads must be secondary woken"
        );

        let _ = fs::remove_file(&lock_path);
        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn test_concurrent_instance_arbitration_recovering_from_stale_dead_lock() {
        let test_dir = std::env::temp_dir().join(format!(
            "gw_arb_stale_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::create_dir_all(&test_dir);
        let lock_path = test_dir.join("stale.lock");

        // 预先写入一份遗留的陈旧死锁（端口不存在，PID 也是早已死去的虚拟 PID）
        let stale_lock = InstanceLock {
            port: 59999,
            token: "dead_stale_token".into(),
            pid: 99_999_999,
            exe_path: String::new(),
            process_start_time: 0,
        };
        fs::write(&lock_path, serde_json::to_vec_pretty(&stale_lock).unwrap()).unwrap();

        let num_threads = 12;
        let barrier = Arc::new(std::sync::Barrier::new(num_threads));
        let primary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let secondary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let error_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let mut handles = Vec::new();
        for _ in 0..num_threads {
            let b = barrier.clone();
            let lp = lock_path.clone();
            let p_cnt = primary_count.clone();
            let s_cnt = secondary_count.clone();
            let e_cnt = error_count.clone();

            handles.push(std::thread::spawn(move || {
                b.wait();
                match arbitrate_single_instance(&lp) {
                    SingleInstanceDecision::Primary { .. } => {
                        p_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::SecondaryWoken => {
                        s_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::Error(err) => {
                        eprintln!("Arbitration error: {err}");
                        e_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                }
            }));
        }

        for h in handles {
            h.join().expect("join arbitration thread");
        }

        let p = primary_count.load(std::sync::atomic::Ordering::SeqCst);
        let s = secondary_count.load(std::sync::atomic::Ordering::SeqCst);
        let e = error_count.load(std::sync::atomic::Ordering::SeqCst);

        assert_eq!(e, 0, "There should be 0 errors during stale lock recovery");
        assert_eq!(
            p, 1,
            "There MUST be exactly 1 primary instance recovered from stale lock"
        );
        assert_eq!(
            s, 11,
            "All 11 concurrent threads must be secondary woken after recovery"
        );

        let _ = fs::remove_file(&lock_path);
        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn test_concurrent_instance_arbitration_recovering_from_empty_corrupted_lock() {
        let test_dir = std::env::temp_dir().join(format!(
            "gw_arb_empty_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::create_dir_all(&test_dir);
        let lock_path = test_dir.join("empty.lock");

        // 预先写入一个 0 字节的空文件，模拟崩溃残留的空锁文件
        fs::write(&lock_path, b"").unwrap();

        let num_threads = 12;
        let barrier = Arc::new(std::sync::Barrier::new(num_threads));
        let primary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let secondary_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let error_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let mut handles = Vec::new();
        for _ in 0..num_threads {
            let b = barrier.clone();
            let lp = lock_path.clone();
            let p_cnt = primary_count.clone();
            let s_cnt = secondary_count.clone();
            let e_cnt = error_count.clone();

            handles.push(std::thread::spawn(move || {
                b.wait();
                match arbitrate_single_instance(&lp) {
                    SingleInstanceDecision::Primary { .. } => {
                        p_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::SecondaryWoken => {
                        s_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                    SingleInstanceDecision::Error(err) => {
                        eprintln!("Arbitration error: {err}");
                        e_cnt.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                }
            }));
        }

        for h in handles {
            h.join().expect("join arbitration thread");
        }

        let p = primary_count.load(std::sync::atomic::Ordering::SeqCst);
        let s = secondary_count.load(std::sync::atomic::Ordering::SeqCst);
        let e = error_count.load(std::sync::atomic::Ordering::SeqCst);

        assert_eq!(e, 0, "There should be 0 errors during empty lock recovery");
        assert_eq!(
            p, 1,
            "There MUST be exactly 1 primary instance recovered from empty lock"
        );
        assert_eq!(
            s, 11,
            "All 11 concurrent threads must be secondary woken after recovery"
        );

        let _ = fs::remove_file(&lock_path);
        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn test_managed_backend_state_transitions() {
        let state = ManagedBackendState::default();

        // 1. 初始为 Idle
        {
            let lock = state.0.lock().unwrap();
            assert_eq!(lock.phase, BackendPhase::Idle);
            assert!(lock.child.is_none());
        }

        // 2. 转换到 Launching
        {
            let mut lock = state.0.lock().unwrap();
            lock.phase = BackendPhase::Launching;
        }

        // 3. 模拟拉起一个已退出的进程测试回收
        let dead_child = std::process::Command::new("python")
            .arg("-c")
            .arg("import sys; sys.exit(0)")
            .spawn()
            .expect("spawn exit child");

        {
            let mut lock = state.0.lock().unwrap();
            lock.phase = BackendPhase::Running;
            lock.child = Some(dead_child);
        }

        // 4. 检查退出状态并回收重置为 Launching
        {
            let mut lock = state.0.lock().unwrap();
            if let Some(ref mut child) = lock.child {
                let _ = child.wait(); // 确保已退出
                assert!(child.try_wait().unwrap().is_some());
            }
            lock.child = None;
            lock.phase = BackendPhase::Launching;
        }

        // 5. 停止
        stop_managed_backend(&state);
        {
            let lock = state.0.lock().unwrap();
            assert_eq!(lock.phase, BackendPhase::Idle);
            assert!(lock.child.is_none());
        }
    }

    #[test]
    fn test_backend_pipe_drain_does_not_block() {
        // 产生超过 64KB 的大量输出，验证管道抽干线程不会导致进程死锁
        let mut child = std::process::Command::new("python")
            .arg("-c")
            .arg("import sys; [sys.stdout.write('A' * 200 + '\\n') for _ in range(500)]; sys.stderr.write('ERR_TEST_LINE\\n'); sys.stdout.flush(); sys.stderr.flush()")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("spawn large output process");

        let tracker = drain_process_output(&mut child);

        let status = child.wait().expect("wait for child process");
        assert!(
            status.success(),
            "Child should exit successfully without pipe deadlock"
        );

        // 等待 stderr 线程写入缓冲
        std::thread::sleep(Duration::from_millis(100));
        let logs = tracker.recent_stderr.lock().unwrap();
        assert!(logs.iter().any(|line| line.contains("ERR_TEST_LINE")));
    }

    #[test]
    fn test_wait_for_backend_ready_timeout_kills_child() {
        // 启动一个长休眠进程
        let mut child = std::process::Command::new("python")
            .arg("-c")
            .arg("import time; time.sleep(30)")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("spawn sleeping process");

        let tracker = drain_process_output(&mut child);
        let client = reqwest::Client::new();

        // 指向本机关闭端口，避免真实 2077 服务干扰超时判定
        let closed = Url::parse("http://127.0.0.1:9/").unwrap();
        let res = tauri::async_runtime::block_on(wait_for_backend_ready(
            &client,
            &mut child,
            &tracker,
            &closed,
            Duration::from_millis(150),
        ));

        assert!(res.is_err(), "Wait should return error on timeout");
        // 验证超时后子进程已被 kill 并且退出
        let exit_status = child.try_wait().expect("try_wait killed child");
        assert!(
            exit_status.is_some(),
            "Child should be killed after timeout"
        );
    }

    #[test]
    fn upgrade_journal_commits_only_after_verified_install() {
        let temp = std::env::temp_dir().join(format!(
            "gw_journal_test_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&temp).unwrap();
        let journal = temp.join("journal.txt");

        for state in ["installed", "verified"] {
            fs::write(&journal, state).unwrap();
            commit_upgrade_journal_at(&journal);
            assert_eq!(fs::read_to_string(&journal).unwrap(), "committed");
        }

        for state in ["prepare", "backed_up", "rolled_back", "committed"] {
            fs::write(&journal, state).unwrap();
            commit_upgrade_journal_at(&journal);
            assert_eq!(fs::read_to_string(&journal).unwrap(), state);
        }

        fs::remove_file(&journal).unwrap();
        commit_upgrade_journal_at(&journal);
        assert!(!journal.exists());

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn find_backend_root_honors_env_variable() {
        let temp_dir = std::env::temp_dir().join(format!("gw_test_root_{}", std::process::id()));
        let _ = fs::create_dir_all(temp_dir.join("gw"));
        let _ = fs::write(temp_dir.join("run.py"), b"# test run.py");
        std::env::set_var("GW_BACKEND_ROOT", &temp_dir);
        let found = find_backend_root().expect("find backend root via env");
        assert_eq!(found, temp_dir);
        std::env::remove_var("GW_BACKEND_ROOT");
        let _ = fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn find_python_executable_honors_env_variable() {
        let temp_dir = std::env::temp_dir().join(format!("gw_test_python_{}", std::process::id()));
        let _ = fs::create_dir_all(&temp_dir);
        let fake_python = temp_dir.join("fake_python.exe");
        let _ = fs::write(&fake_python, b"fake");
        std::env::set_var("GW_PYTHON", &fake_python);
        let found = find_python_executable(&temp_dir).expect("find python via env");
        assert_eq!(found, fake_python);
        std::env::remove_var("GW_PYTHON");
        let _ = fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_process_identity_and_matching() {
        // 当前进程身份必定匹配
        let live_lock = InstanceLock {
            port: 2077,
            token: "tok".into(),
            pid: std::process::id(),
            exe_path: current_process_exe_path(),
            process_start_time: current_process_start_time(),
        };
        assert!(is_lock_holder_alive_and_matched(&live_lock));

        // 不存在的 PID 必定不匹配
        let dead_lock = InstanceLock {
            port: 2077,
            token: "tok".into(),
            pid: 99_999_999,
            exe_path: current_process_exe_path(),
            process_start_time: current_process_start_time(),
        };
        assert!(!is_lock_holder_alive_and_matched(&dead_lock));

        // PID 碰巧存活，但 exe_path 明确属于其他程序（模拟 PID 复用），必定不匹配
        let reused_pid_lock = InstanceLock {
            port: 2077,
            token: "tok".into(),
            pid: std::process::id(),
            exe_path: "C:\\NonExistent\\definitely_foreign_app.exe".into(),
            process_start_time: 1_234_567_890,
        };
        assert!(!is_lock_holder_alive_and_matched(&reused_pid_lock));

        // 同一二进制（exe_path 相同），但启动时间明确不同（模拟同名程序 PID 复用），必定不匹配
        let same_exe_diff_time_lock = InstanceLock {
            port: 2077,
            token: "tok".into(),
            pid: std::process::id(),
            exe_path: current_process_exe_path(),
            process_start_time: current_process_start_time().wrapping_add(888_888),
        };
        assert!(!is_lock_holder_alive_and_matched(&same_exe_diff_time_lock));

        // 旧格式锁（缺少身份字段），但 PID 存活，必须保守判定为不可核验的存活实例（返回 true）
        let legacy_live_lock = InstanceLock {
            port: 2077,
            token: "tok".into(),
            pid: std::process::id(),
            exe_path: String::new(),
            process_start_time: 0,
        };
        assert!(is_lock_holder_alive_and_matched(&legacy_live_lock));
    }

    #[test]
    fn test_unresponsive_live_instance_prevents_second_instance() {
        let temp_dir = std::env::temp_dir();
        let unique_id = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let test_lock_path = temp_dir.join(format!("test_unresponsive_live_{unique_id}.json"));

        // 绑定一个随机端口但绝不 accept（模拟无响应的活实例）
        let fake_listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind listener");
        let port = fake_listener.local_addr().unwrap().port();

        // 构造一个由“当前存活进程”持有的主锁文件
        let lock_info = InstanceLock {
            port,
            token: "unresponsive_token".to_string(),
            pid: std::process::id(),
            exe_path: current_process_exe_path(),
            process_start_time: current_process_start_time(),
        };
        let content = serde_json::to_vec_pretty(&lock_info).unwrap();
        fs::write(&test_lock_path, content).unwrap();

        // 第二次启动执行单实例仲裁
        let decision = arbitrate_single_instance(&test_lock_path);

        // 严格断言：活实例虽然无响应，但绝不能成为第二个 Primary，必须报错并保留原锁！
        match decision {
            SingleInstanceDecision::Error(msg) => {
                assert!(
                    msg.contains("未响应唤醒请求") || msg.contains("正在运行"),
                    "Error message should mention existing unresponsive instance, got: {msg}"
                );
            }
            SingleInstanceDecision::Primary { .. } => {
                panic!(
                    "Unresponsive live instance must NOT be replaced by a second Primary instance!"
                );
            }
            SingleInstanceDecision::SecondaryWoken => {
                panic!("Unresponsive socket should not return SecondaryWoken!");
            }
        }

        // 验证主锁文件依然完好保留，没有被粗暴删除
        assert!(
            test_lock_path.exists(),
            "Lock file must not be removed when holder is still alive!"
        );

        let _ = fs::remove_file(&test_lock_path);
    }

    #[test]
    fn test_stale_lock_with_reused_pid_recovers_successfully() {
        let temp_dir = std::env::temp_dir();
        let unique_id = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let test_lock_path = temp_dir.join(format!("test_reused_pid_{unique_id}.json"));

        // 构造一份遗留锁：端口不可通，PID 碰巧是当前进程 PID，但 exe_path 和启动时间明显不匹配（模拟操作系统 PID 复用）
        let stale_reused_lock = InstanceLock {
            port: 59998,
            token: "foreign_token".to_string(),
            pid: std::process::id(),
            exe_path: "C:\\NonExistent\\reused_foreign_program.exe".to_string(),
            process_start_time: 987_654_321,
        };
        let content = serde_json::to_vec_pretty(&stale_reused_lock).unwrap();
        fs::write(&test_lock_path, content).unwrap();

        // 第二次启动执行仲裁：识别出 PID 复用，安全清理并恢复为主实例
        let decision = arbitrate_single_instance(&test_lock_path);
        match decision {
            SingleInstanceDecision::Primary { lock_path, .. } => {
                assert_eq!(lock_path, test_lock_path);
            }
            SingleInstanceDecision::SecondaryWoken => {
                panic!("Stale lock with foreign identity should not return SecondaryWoken");
            }
            SingleInstanceDecision::Error(e) => {
                panic!(
                    "Stale lock with foreign identity should recover as Primary, got Error: {e}"
                );
            }
        }

        let _ = fs::remove_file(&test_lock_path);
    }

    #[test]
    fn test_stale_lock_with_same_binary_reused_pid_recovers_as_primary() {
        let temp_dir = std::env::temp_dir();
        let unique_id = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let test_lock_path = temp_dir.join(format!("test_same_bin_reused_{unique_id}.json"));

        // 构造一份遗留锁：端口离线，PID 碰巧是当前存活进程，exe_path 也是当前进程（同一二进制），
        // 但 process_start_time 明显陈旧（与当前真实启动时间不符，模拟原实例崩溃后 PID 被同名新进程复用）
        let stale_same_bin_lock = InstanceLock {
            port: 59996,
            token: "same_bin_token".to_string(),
            pid: std::process::id(),
            exe_path: current_process_exe_path(),
            process_start_time: current_process_start_time().wrapping_add(999_999),
        };
        let content = serde_json::to_vec_pretty(&stale_same_bin_lock).unwrap();
        fs::write(&test_lock_path, content).unwrap();

        // 执行入口仲裁：识别出陈旧启动时间判定为 PID 复用，安全恢复为主实例
        let decision = arbitrate_single_instance(&test_lock_path);
        match decision {
            SingleInstanceDecision::Primary { lock_path, .. } => {
                assert_eq!(lock_path, test_lock_path);
            }
            SingleInstanceDecision::SecondaryWoken => {
                panic!("Stale lock with different start time must not return SecondaryWoken");
            }
            SingleInstanceDecision::Error(e) => {
                panic!("Stale lock with different start time should recover as Primary, got Error: {e}");
            }
        }

        let _ = fs::remove_file(&test_lock_path);
    }

    #[test]
    fn test_legacy_lock_with_live_foreign_process_prevents_second_instance() {
        let temp_dir = std::env::temp_dir();
        let unique_id = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let test_lock_path = temp_dir.join(format!("test_legacy_foreign_{unique_id}.json"));

        // 启动一个长期后台子进程（例如 python sleep 30），获取其存活 PID
        let mut foreign_child = std::process::Command::new("python")
            .arg("-c")
            .arg("import time; time.sleep(30)")
            .spawn()
            .expect("spawn live foreign process");
        let foreign_pid = foreign_child.id();

        // 构造一份旧格式锁（缺少 exe_path 与 process_start_time），记录该存活的外部进程 PID，端口无法连通
        let legacy_lock = InstanceLock {
            port: 59995,
            token: "legacy_unresponsive_token".to_string(),
            pid: foreign_pid,
            exe_path: String::new(),
            process_start_time: 0,
        };
        let content = serde_json::to_vec_pretty(&legacy_lock).unwrap();
        fs::write(&test_lock_path, content).unwrap();

        // 第二次启动执行入口仲裁：
        // 发现锁持有者仍存活且为旧格式不可核验，绝不能删锁，绝不起第二个主实例！
        let decision = arbitrate_single_instance(&test_lock_path);
        match decision {
            SingleInstanceDecision::Error(msg) => {
                assert!(
                    msg.contains("未响应唤醒请求") || msg.contains("正在运行"),
                    "Error message should mention existing unresponsive instance, got: {msg}"
                );
            }
            SingleInstanceDecision::Primary { .. } => {
                let _ = foreign_child.kill();
                let _ = foreign_child.wait();
                panic!("Legacy lock held by a live process must NOT be deleted or recovered as Primary!");
            }
            SingleInstanceDecision::SecondaryWoken => {
                let _ = foreign_child.kill();
                let _ = foreign_child.wait();
                panic!("Unresponsive legacy instance should not return SecondaryWoken!");
            }
        }

        // 验证旧格式锁文件依然完好保留，没有被粗暴删除
        assert!(
            test_lock_path.exists(),
            "Legacy lock must be preserved when process is alive!"
        );

        // 清理子进程与测试文件
        let _ = foreign_child.kill();
        let _ = foreign_child.wait();
        let _ = fs::remove_file(&test_lock_path);
    }

    #[test]
    fn test_client_settings_dir_respects_appdata_override() {
        let override_dir = std::ffi::OsString::from(r"C:\sandbox\Roaming");
        let resolved = client_settings_dir_from(
            Some(override_dir),
            "ai.godsworkbench.desktop",
            Ok(PathBuf::from(r"C:\real\Roaming\ai.godsworkbench.desktop")),
        )
        .expect("override should resolve");
        assert_eq!(
            resolved,
            PathBuf::from(r"C:\sandbox\Roaming\ai.godsworkbench.desktop")
        );

        let fallback = client_settings_dir_from(
            None,
            "ai.godsworkbench.desktop",
            Ok(PathBuf::from(r"C:\real\Roaming\ai.godsworkbench.desktop")),
        )
        .expect("fallback should resolve");
        assert_eq!(
            fallback,
            PathBuf::from(r"C:\real\Roaming\ai.godsworkbench.desktop")
        );
    }

    #[test]
    fn test_tray_find_nas_menu_id_constant() {
        assert_eq!(TRAY_MENU_ID_FIND_NAS, "scan");
    }

    #[test]
    fn test_production_data_root_priority_and_fail_closed() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("gw_qin648_rust_{nonce}"));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("create temp root");

        // 1. 全新无指针无数据：默认 Gods Workbench/user data
        assert_eq!(bootstrap_data_root_in(&root), root.join("Gods Workbench"));
        assert_eq!(
            production_data_root_in(&root).expect("fresh default"),
            root.join("Gods Workbench").join("user data")
        );

        // 2. 新引导目录存在陈旧 data，同时存在损坏旧指针 -> 必须 fail-closed
        let new_boot_data = root.join("Gods Workbench").join("data");
        fs::create_dir_all(&new_boot_data).expect("create stale data");
        fs::write(new_boot_data.join("stale.json"), "{}").expect("write stale");
        let legacy_boot = root.join("GodsWorkbench");
        fs::create_dir_all(&legacy_boot).expect("create legacy boot");
        fs::write(legacy_boot.join("runtime-root.json"), "{corrupt").expect("write corrupt");
        assert!(production_data_root_in(&root).is_err());

        // 3. 合法旧指针优先于新引导目录陈旧 data
        let custom_target = root.join("CustomLegacyTarget");
        fs::create_dir_all(&custom_target).expect("create custom target");
        let ptr_json = serde_json::json!({
            "schema_version": 1,
            "data_location": custom_target.to_string_lossy(),
        });
        fs::write(legacy_boot.join("runtime-root.json"), ptr_json.to_string())
            .expect("write valid legacy pointer");
        assert_eq!(
            production_data_root_in(&root).expect("legacy pointer wins"),
            custom_target
        );

        // 4. 新指针优先于旧指针；若新指针损坏即使旧指针合法也必 fail-closed
        fs::write(
            root.join("Gods Workbench").join("runtime-root.json"),
            "{bad",
        )
        .expect("write corrupt new ptr");
        assert!(production_data_root_in(&root).is_err());

        let new_target = root.join("CustomNewTarget");
        let new_ptr_json = serde_json::json!({
            "schema_version": 1,
            "data_location": new_target.to_string_lossy(),
        });
        fs::write(
            root.join("Gods Workbench").join("runtime-root.json"),
            new_ptr_json.to_string(),
        )
        .expect("write valid new ptr");
        assert_eq!(
            production_data_root_in(&root).expect("new pointer wins"),
            new_target
        );

        let _ = fs::remove_dir_all(&root);
    }
}
