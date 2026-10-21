#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use futures_util::future::join_all;
use if_addrs::{get_if_addrs, IfAddr};
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs,
    net::{IpAddr, Ipv4Addr},
    path::PathBuf,
    time::Duration,
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, WebviewWindow, WindowEvent,
};

#[derive(Debug, Serialize)]
struct DiscoveryResult {
    name: String,
    url: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct PersistedConfig {
    server_url: String,
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
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            discover_lan,
            last_server,
            connect_to_server
        ])
        .setup(|app| {
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
                    "quit" => app.exit(0),
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
        let url = normalize_server_url("http://192.168.1.20").unwrap();
        assert_eq!(url.as_str(), "http://192.168.1.20:2077/");
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
}
