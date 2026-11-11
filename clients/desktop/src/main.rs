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
    sync::Mutex,
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

/// 托管的本地后端子进程句柄（仅当由本桌面端拉起时持有）。
#[derive(Default)]
struct ManagedBackendState(Mutex<Option<std::process::Child>>);

#[derive(Debug, Serialize, Deserialize)]
struct InstanceLock {
    port: u16,
    token: String,
    pid: u32,
}

fn instance_lock_path() -> PathBuf {
    std::env::temp_dir().join("ai.godsworkbench.desktop.instance.json")
}

fn check_and_wake_existing_instance() -> bool {
    let path = instance_lock_path();
    let content = match fs::read(&path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let lock: InstanceLock = match serde_json::from_slice(&content) {
        Ok(l) => l,
        Err(_) => return false,
    };
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], lock.port));
    let mut stream = match std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
    use std::io::{BufRead, BufReader, Write};
    if writeln!(stream, "WAKEUP {}", lock.token).is_err() {
        return false;
    }
    let _ = stream.flush();
    let mut reader = BufReader::new(stream);
    let mut reply = String::new();
    if reader.read_line(&mut reply).is_ok() && reply.trim() == "WAKEN" {
        return true;
    }
    false
}

fn init_single_instance_listener() -> Result<(std::net::TcpListener, String), String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("Cannot bind ephemeral TCP port: {e}"))?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let token = format!(
        "{:x}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(1)
    );
    let lock = InstanceLock {
        port,
        token: token.clone(),
        pid: std::process::id(),
    };
    let content = serde_json::to_vec_pretty(&lock).map_err(|e| e.to_string())?;
    fs::write(instance_lock_path(), content).map_err(|e| e.to_string())?;
    Ok((listener, token))
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
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], 2077));
    if std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok() {
        return LocalPortStatus::OccupiedForeign;
    }
    LocalPortStatus::Available
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

async fn wait_for_backend_ready(
    client: &Client,
    child: &mut std::process::Child,
    timeout: Duration,
) -> Result<(), String> {
    let local_base = Url::parse("http://127.0.0.1:2077/").unwrap();
    let start = std::time::Instant::now();

    while start.elapsed() < timeout {
        if let Ok(Some(status)) = child.try_wait() {
            let mut err_msg = format!("后端进程提前退出（状态码: {status}）。");
            if let Some(ref mut stderr) = child.stderr {
                use std::io::Read;
                let mut buf = Vec::new();
                let _ = stderr.read_to_end(&mut buf);
                if !buf.is_empty() {
                    let text = String::from_utf8_lossy(&buf);
                    err_msg.push_str(&format!(" 错误详情: {}", text.trim()));
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

    Err("等待本机后端健康检查超时（30秒内未就绪）。建议备用方式：可双击 start.bat 尝试启动并查看控制台日志。".into())
}

fn stop_managed_backend(state: &ManagedBackendState) {
    if let Ok(mut lock) = state.0.lock() {
        if let Some(mut child) = lock.take() {
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
            return Err("端口 2077 已被其他程序占用，无法启动本机后端。建议备用方式：请先检查并关闭占用 2077 端口的程序，或通过终端指定独立端口。".into());
        }
        LocalPortStatus::Available => {}
    }

    let already_running = {
        let lock = backend_state
            .0
            .lock()
            .map_err(|e| format!("Managed backend state unavailable: {e}"))?;
        lock.is_some()
    };

    if !already_running {
        let backend_root = find_backend_root()?;
        let python = find_python_executable(&backend_root)?;
        let mut child = spawn_backend_process(&python, &backend_root)?;
        wait_for_backend_ready(&client, &mut child, Duration::from_secs(30)).await?;
        let mut lock = backend_state
            .0
            .lock()
            .map_err(|e| format!("Managed backend state unavailable: {e}"))?;
        *lock = Some(child);
    } else {
        let local_base = Url::parse("http://127.0.0.1:2077/").unwrap();
        check_health(&client, &local_base).await?;
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
    if check_and_wake_existing_instance() {
        return;
    }
    let single_instance = init_single_instance_listener().ok();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(ClientSessionState(Mutex::new(HashSet::new())))
        .manage(ManagedBackendState(Mutex::new(None)))
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
        .setup(|app| {
            if let Some((listener, token)) = single_instance {
                let app_handle = app.handle().clone();
                std::thread::spawn(move || {
                    use std::io::{BufRead, BufReader, Write};
                    for stream in listener.incoming() {
                        if let Ok(mut stream) = stream {
                            let _ = stream.set_read_timeout(Some(Duration::from_millis(1000)));
                            let _ = stream.set_write_timeout(Some(Duration::from_millis(1000)));
                            let mut reader = BufReader::new(stream.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).is_ok() {
                                let parts: Vec<&str> = line.trim().split_whitespace().collect();
                                if parts.len() == 2 && parts[0] == "WAKEUP" && parts[1] == token {
                                    if let Some(window) = app_handle.get_webview_window("main") {
                                        let _ = window.show();
                                        let _ = window.unminimize();
                                        let _ = window.set_focus();
                                    }
                                    let _ = writeln!(stream, "WAKEN");
                                    let _ = stream.flush();
                                }
                            }
                        }
                    }
                });
            }

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
                        let _ = fs::remove_file(instance_lock_path());
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
        };
        let encoded = serde_json::to_vec(&lock).expect("serialize lock");
        let decoded: InstanceLock = serde_json::from_slice(&encoded).expect("deserialize lock");
        assert_eq!(decoded.port, 2077);
        assert_eq!(decoded.token, "test_token_123");
        assert_eq!(decoded.pid, 42);
    }

    #[test]
    fn stop_managed_backend_is_noop_when_empty() {
        let state = ManagedBackendState(Mutex::new(None));
        stop_managed_backend(&state);
        assert!(state.0.lock().unwrap().is_none());
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
}
