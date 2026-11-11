#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
    AppHandle, Manager, State, WebviewWindow, WindowEvent,
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

/// 托管的本地后端子进程生命周期阶段。
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum BackendPhase {
    Idle,
    Launching,
    Running,
    Failed,
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

/// 唤醒状态共享结构：允许在 Tauri 窗口尚未完成初始化前安全捕获唤醒通知。
struct WakeupState {
    app_handle: Mutex<Option<AppHandle>>,
    pending_wakeups: std::sync::atomic::AtomicUsize,
}

impl WakeupState {
    fn new() -> Self {
        Self {
            app_handle: Mutex::new(None),
            pending_wakeups: std::sync::atomic::AtomicUsize::new(0),
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

    fn attach_app(&self, app: AppHandle) {
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
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const SYNCHRONIZE: u32 = 0x00100000;
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return false;
        }
        CloseHandle(handle);
        true
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
fn current_process_start_time() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(not(windows))]
fn get_process_identity(pid: u32) -> Option<(u64, String)> {
    if !is_process_alive(pid) {
        return None;
    }
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
    Some((0, exe_path))
}

fn is_lock_holder_alive_and_matched(lock: &InstanceLock) -> bool {
    if !is_process_alive(lock.pid) {
        return false;
    }
    let Some((start_time, exe_path)) = get_process_identity(lock.pid) else {
        return false;
    };

    if lock.process_start_time > 0 && start_time > 0 && lock.process_start_time == start_time {
        return true;
    }

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

    if lock.exe_path.is_empty() && lock.process_start_time == 0 && lock.pid == std::process::id() {
        return true;
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

fn try_wake_existing_instance(port: u16, token: &str) -> bool {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
    use std::io::{BufRead, BufReader, Write};
    if writeln!(stream, "WAKEUP {}", token).is_err() {
        return false;
    }
    let _ = stream.flush();
    let mut reader = BufReader::new(&mut stream);
    let mut reply = String::new();
    if reader.read_line(&mut reply).is_ok() && reply.trim() == "WAKEN" {
        return true;
    }
    false
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

    let wakeup_state = Arc::new(WakeupState::new());
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
                    if parts.len() == 2 && parts[0] == "WAKEUP" && parts[1] == expected_token {
                        // 优先写回 WAKEN 并 flush，让发起方立即收到应答完成唤醒退出，不依赖 UI 或主线程状态
                        let _ = writeln!(stream, "WAKEN");
                        let _ = stream.flush();
                        wakeup_clone.trigger_wakeup();
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
        backend_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe"),
        backend_root.join("venv").join("Scripts").join("python.exe"),
    ];
    #[cfg(not(windows))]
    let venv_candidates = [
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

fn find_backend_root() -> Result<PathBuf, String> {
    for var in ["GW_BACKEND_ROOT", "GW_ROOT_DIR"] {
        if let Ok(dir_str) = std::env::var(var) {
            let p = PathBuf::from(dir_str.trim());
            if p.join("run.py").is_file() && p.join("gw").is_dir() {
                return Ok(p);
            }
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        if cwd.join("run.py").is_file() && cwd.join("gw").is_dir() {
            return Ok(cwd);
        }
    }

    if let Ok(exe_path) = std::env::current_exe() {
        let mut current = exe_path.parent();
        for _ in 0..6 {
            if let Some(dir) = current {
                if dir.join("run.py").is_file() && dir.join("gw").is_dir() {
                    return Ok(dir.to_path_buf());
                }
                current = dir.parent();
            } else {
                break;
            }
        }
    }

    Err(
        "未找到神工坊后端源码根目录（未发现 run.py 与 gw 目录）。请配置 GW_BACKEND_ROOT 环境变量。"
            .into(),
    )
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
    let mut cmd = std::process::Command::new(python);
    cmd.current_dir(backend_root)
        .arg("run.py")
        .arg("--prod")
        .env("GW_HOST", "127.0.0.1")
        .env("GW_PORT", "2077")
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

async fn wait_for_backend_ready(
    client: &Client,
    child: &mut std::process::Child,
    tracker: &ProcessOutputTracker,
    timeout: Duration,
) -> Result<(), String> {
    let local_base = Url::parse("http://127.0.0.1:2077/").unwrap();
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

        if check_health(client, &local_base).await.is_ok() {
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

/// 客户端桥接命令只对本地发现页与当前已连接的服务器页面开放（QIN-432 AC10）。
fn ensure_client_bridge_window(app: &AppHandle, window: &WebviewWindow) -> Result<(), String> {
    if ensure_local_ui(window).is_ok() {
        return Ok(());
    }
    let current = window
        .url()
        .map_err(|error| format!("Cannot read window URL: {error}"))?;
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

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("Cannot locate the client settings directory: {error}"))?;
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Cannot create the client settings directory: {error}"))?;
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
    let directory = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("Cannot locate the client settings directory: {error}"))?;
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Cannot create the client settings directory: {error}"))?;
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
async fn discover_lan(window: WebviewWindow) -> Result<Vec<DiscoveryResult>, String> {
    ensure_local_ui(&window)?;
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
    ensure_local_ui(&window)?;
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
    server_url: String,
) -> Result<String, String> {
    ensure_local_ui(&window)?;
    let base = normalize_server_url(&server_url)?;
    let client = new_http_client()?;
    check_health(&client, &base).await?;
    save_server_url(&app, base.as_str())?;
    let mut destination = base;
    destination.set_path("/static/pages/index.html");
    Ok(destination.to_string())
}

#[tauri::command]
async fn launch_local_backend(
    app: AppHandle,
    window: WebviewWindow,
    backend_state: State<'_, ManagedBackendState>,
) -> Result<String, String> {
    ensure_local_ui(&window)?;
    let client = new_http_client()?;

    match check_local_port_2077(&client).await {
        LocalPortStatus::HealthyInstance => {
            save_server_url(&app, "http://127.0.0.1:2077")?;
            return Ok("http://127.0.0.1:2077/static/pages/index.html".to_string());
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

    save_server_url(&app, "http://127.0.0.1:2077")?;
    Ok("http://127.0.0.1:2077/static/pages/index.html".to_string())
}

fn make_tray_icon() -> Image<'static> {
    const SIZE: u32 = 32;
    const DARK: [u8; 4] = [9, 10, 13, 255];
    const GOLD: [u8; 4] = [223, 195, 132, 255];
    const CLEAR: [u8; 4] = [0, 0, 0, 0];
    let mut pixels = vec![0; (SIZE * SIZE * 4) as usize];
    for y in 0..SIZE {
        for x in 0..SIZE {
            let border = x < 3 || x >= 29 || y < 3 || y >= 29;
            let letter_g = (y >= 8 && y < 11 && x >= 9 && x < 23)
                || (x >= 8 && x < 11 && y >= 9 && y < 23)
                || (y >= 20 && y < 23 && x >= 9 && x < 23)
                || (x >= 21 && x < 24 && y >= 17 && y < 22)
                || (y >= 15 && y < 18 && x >= 17 && x < 24);
            let color = if border || letter_g {
                GOLD
            } else if x >= 3 && x < 29 && y >= 3 && y < 29 {
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

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(ClientSessionState(Mutex::new(HashSet::new())))
        .manage(ManagedBackendState::default())
        .invoke_handler(tauri::generate_handler![
            discover_lan,
            last_server,
            connect_to_server,
            launch_local_backend,
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
            let local_url = window
                .url()
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let hide_window = window.clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = hide_window.hide();
                }
            });

            let open = MenuItem::with_id(app, "open", "Open Workbench", true, None::<&str>)?;
            let scan = MenuItem::with_id(app, "scan", "Find NAS", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &scan, &quit])?;
            TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(true)
                .tooltip("Gods Workbench")
                .icon(make_tray_icon())
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "scan" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let mut scan_url = local_url.clone();
                            scan_url.set_query(Some("rescan=1"));
                            let _ = window.navigate(scan_url);
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        if let Ok(content) = fs::read(&active_lock_path) {
                            if let Ok(lock) = serde_json::from_slice::<InstanceLock>(&content) {
                                if lock.pid == std::process::id() {
                                    let _ = fs::remove_file(&active_lock_path);
                                }
                            }
                        }
                        let state = app.state::<ManagedBackendState>();
                        stop_managed_backend(&state);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Failed to launch Gods Workbench desktop client");
}

#[cfg(test)]
mod tests {
    use super::*;

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
        let url = normalize_server_url("http://192.168.0.10").unwrap();
        assert_eq!(url.as_str(), "http://192.168.0.10:2077/");
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

        // 传入 150 毫秒的超短超时
        let res = tauri::async_runtime::block_on(wait_for_backend_ready(
            &client,
            &mut child,
            &tracker,
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
}
