// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! 受信本机一次性票据的桌面侧接线。
//!
//! - 仅在 loopback、安装版清单匹配且 ACL 通过时调用 QIN-630 CLI；
//! - 票据只从内存 stdout 读出，经 URL fragment 交付，绝不写盘/日志/远程；
//! - 远程/NAS 永不调用 `trusted-open`，也永不附加 `gw_trusted`；
//! - 630 未合入时能力探测会判定不可用，直接回退密码登录。

use serde::Deserialize;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const TICKET_MARKER: &str = "gw_trusted=";
const MAX_TICKET_LEN: usize = 4096;

#[derive(Debug, Clone, Deserialize, Default)]
pub struct AclReport {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub reason: String,
}

pub fn helper_script(backend_root: &Path) -> Option<PathBuf> {
    [
        backend_root.join("tools").join("verify_trusted_acl.py"),
        backend_root
            .join("clients")
            .join("desktop")
            .join("packaging")
            .join("verify_trusted_acl.py"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
}

fn command_for(python: &Path, backend_root: &Path, args: &[&str]) -> Command {
    let mut command = Command::new(python);
    command
        .current_dir(backend_root)
        .args(args)
        .env("GW_RUNTIME_MODE", "prod")
        .env("GW_HOST", "127.0.0.1")
        .env("GW_PORT", "2077")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn run_with_timeout(
    mut command: Command,
    timeout: Duration,
) -> Result<(std::process::ExitStatus, Vec<u8>, Vec<u8>), String> {
    let mut child = command
        .spawn()
        .map_err(|_| "无法启动本机受信组件".to_string())?;
    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    let stdout_reader = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        if let Some(stream) = stdout.as_mut() {
            let _ = stream.read_to_end(&mut buffer);
        }
        buffer
    });
    let stderr_reader = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        if let Some(stream) = stderr.as_mut() {
            let _ = stream.read_to_end(&mut buffer);
        }
        buffer
    });

    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() < timeout => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("本机受信组件响应超时".into());
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("本机受信组件状态异常".into());
            }
        }
    };
    let stdout = stdout_reader.join().unwrap_or_default();
    let stderr = stderr_reader.join().unwrap_or_default();
    Ok((status, stdout, stderr))
}

pub fn verify_acl(
    python: &Path,
    backend_root: &Path,
    auth_dir: &Path,
) -> Result<AclReport, String> {
    let script =
        helper_script(backend_root).ok_or_else(|| "缺少受信本机 ACL 校验器".to_string())?;
    let script_text = script.to_string_lossy().to_string();
    let auth_dir_text = auth_dir.to_string_lossy().to_string();
    let command = command_for(
        python,
        backend_root,
        &[
            script_text.as_str(),
            "--json",
            "--auth-dir",
            auth_dir_text.as_str(),
        ],
    );
    let (status, stdout, _stderr) = run_with_timeout(command, Duration::from_secs(15))?;
    let report: AclReport = serde_json::from_slice(&stdout)
        .map_err(|_| "受信本机 ACL 校验器输出不可解析".to_string())?;
    if !status.success() || !report.ok {
        return Ok(AclReport {
            ok: false,
            reason: if report.reason.is_empty() {
                "受信本机账户目录权限不安全".into()
            } else {
                report.reason
            },
            ..report
        });
    }
    Ok(report)
}

pub fn issue_ticket(
    python: &Path,
    backend_root: &Path,
    host: &str,
    port: u16,
) -> Result<String, String> {
    if !matches!(host, "127.0.0.1" | "::1") {
        return Err("只允许为 loopback 后端签发本机票据".into());
    }
    let host_arg = host.to_string();
    let port_arg = port.to_string();
    let command = command_for(
        python,
        backend_root,
        &[
            "-m",
            "gw.core.local_accounts_cli",
            "--mode",
            "prod",
            "trusted-open",
            "--host",
            host_arg.as_str(),
            "--port",
            port_arg.as_str(),
        ],
    );
    let (status, stdout, _stderr) = run_with_timeout(command, Duration::from_secs(20))?;
    if !status.success() {
        return Err("本机票据签发失败，请使用密码登录".into());
    }
    let text = String::from_utf8(stdout).map_err(|_| "本机票据输出不是有效文本".to_string())?;
    parse_ticket_url(&text, host, port)
}

pub fn parse_ticket_url(
    stdout: &str,
    expected_host: &str,
    expected_port: u16,
) -> Result<String, String> {
    let mut tickets = Vec::new();
    for line in stdout.lines() {
        let line = line.trim();
        if !line.contains(TICKET_MARKER) {
            continue;
        }
        let url = reqwest::Url::parse(line).map_err(|_| "本机票据链接格式非法".to_string())?;
        if url.scheme() != "http"
            || url.host_str() != Some(expected_host)
            || url.port() != Some(expected_port)
            || url.path() != "/static/pages/index.html"
            || url.query().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err("本机票据链接未通过 loopback/路径校验".into());
        }
        let fragment = url.fragment().unwrap_or_default();
        let ticket = fragment
            .strip_prefix(TICKET_MARKER)
            .ok_or_else(|| "本机票据片段格式非法".to_string())?;
        if ticket.is_empty()
            || ticket.len() > MAX_TICKET_LEN
            || !ticket
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        {
            return Err("本机票据内容非法".into());
        }
        tickets.push(ticket.to_string());
    }
    match tickets.as_slice() {
        [ticket] => Ok(ticket.clone()),
        [] => Err("本机票据签发输出缺失".into()),
        _ => Err("本机票据输出不唯一".into()),
    }
}

pub fn destination_with_ticket(base: &reqwest::Url, ticket: &str) -> Result<String, String> {
    if !crate::compat::is_loopback_url(base) {
        return Err("拒绝向非 loopback 地址附加本机票据".into());
    }
    if ticket.is_empty()
        || ticket.len() > MAX_TICKET_LEN
        || !ticket
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err("本机票据内容非法".into());
    }
    let mut url = base.clone();
    url.set_path("/static/pages/index.html");
    url.set_query(None);
    url.set_fragment(Some(&format!("{TICKET_MARKER}{ticket}")));
    Ok(url.to_string())
}

pub fn destination_without_ticket(base: &reqwest::Url) -> String {
    let mut url = base.clone();
    url.set_path("/static/pages/index.html");
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::Url;

    #[test]
    fn ticket_url_accepts_only_loopback_fragment_form() {
        let ticket = "abc.DEF_123-xyz";
        let output = format!(
            "请打开：\nhttp://127.0.0.1:2077/static/pages/index.html#gw_trusted={ticket}\n"
        );
        assert_eq!(
            parse_ticket_url(&output, "127.0.0.1", 2077).unwrap(),
            ticket
        );
        let wrong_host = output.replace("127.0.0.1", "nas.example");
        assert!(parse_ticket_url(&wrong_host, "127.0.0.1", 2077).is_err());
        let query = output.replace("#gw_trusted", "?gw_trusted");
        assert!(parse_ticket_url(&query, "127.0.0.1", 2077).is_err());
        let remote = "http://nas.example:2077/static/pages/index.html#gw_trusted=abc";
        assert!(parse_ticket_url(remote, "127.0.0.1", 2077).is_err());
        let userinfo = "http://user:pass@127.0.0.1:2077/static/pages/index.html#gw_trusted=abc";
        assert!(parse_ticket_url(userinfo, "127.0.0.1", 2077).is_err());
        let duplicate = format!("{output}{output}");
        assert!(parse_ticket_url(&duplicate, "127.0.0.1", 2077).is_err());
    }

    #[test]
    fn destination_keeps_ticket_in_fragment_only() {
        let base = Url::parse("http://127.0.0.1:2077/").unwrap();
        let destination = destination_with_ticket(&base, "abc.DEF_123-xyz").unwrap();
        assert_eq!(
            destination,
            "http://127.0.0.1:2077/static/pages/index.html#gw_trusted=abc.DEF_123-xyz"
        );
        assert!(!destination.contains("?"));
        let remote = Url::parse("http://nas.example:2077/").unwrap();
        assert!(destination_with_ticket(&remote, "abc").is_err());
        assert_eq!(
            destination_without_ticket(&base),
            "http://127.0.0.1:2077/static/pages/index.html"
        );
    }

    #[test]
    fn helper_script_prefers_installed_tools_copy() {
        let root = std::env::temp_dir().join(format!(
            "gw-trusted-helper-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|value| value.as_nanos())
                .unwrap_or_default()
        ));
        let tools = root.join("tools");
        std::fs::create_dir_all(&tools).unwrap();
        let script = tools.join("verify_trusted_acl.py");
        std::fs::write(&script, b"# test").unwrap();
        assert_eq!(helper_script(&root).unwrap(), script);
        let _ = std::fs::remove_dir_all(&root);
    }
}
