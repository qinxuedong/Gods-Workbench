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

//! 安装版与远程 NAS 共用的 gw-compat-v1 能力握手。
//!
//! 只以能力形状判定兼容，不以精确版本或 semver 区间放行：
//! - 版本必须带 `+gw.<来源>` 标记，未知版本一律失败关闭；
//! - 探测必须带随机 nonce 且两次结果一致；
//! - 出现任何缓存命中信号即视为不可信；
//! - 版本不同但能力 digest 相同允许连接，反之拒绝。

use crate::sha256::sha256_hex;
use reqwest::header::HeaderMap;
use reqwest::{Client, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const CAPABILITY_PROFILE: &str = "gw-compat-v1";
pub const OPENAPI_TITLE: &str = "Gods-Workbench Cleanroom API";
pub const TRUSTED_BOOTSTRAP_OPERATION: &str = "POST /api/asset-auth/local/trusted-bootstrap";
pub const REQUIRED_OPERATIONS: [&str; 10] = [
    "GET /api/asset-auth/status",
    "GET /api/asset-registry/projects",
    "GET /api/canvases",
    "GET /healthz",
    "PATCH /api/asset-registry/projects/{project_id}",
    "PATCH /api/canvases/{canvas_id}",
    "POST /api/asset-auth/local/login",
    "POST /api/asset-auth/local/setup",
    "POST /api/asset-registry/projects",
    "POST /api/canvases",
];
pub const HEALTH_REQUIRED_KEYS: [&str; 6] = [
    "auth_mode",
    "frozen_contracts",
    "mode",
    "oidc_ready",
    "release_authorized",
    "status",
];
pub const STATUS_REQUIRED_KEYS: [&str; 7] = [
    "auth_mode",
    "authenticated",
    "login_available",
    "logout_available",
    "oidc_ready",
    "reason",
    "release_authorized",
];
pub const BUNDLE_MANIFEST_NAME: &str = "bundle-manifest.json";

static PROBE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct BundleManifest {
    pub schema_version: u32,
    pub product_version: String,
    pub build_id: String,
    pub git_commit: String,
    pub capability_profile: String,
    pub capability_digest: String,
    #[serde(default)]
    pub runtime_inventory_sha256: String,
}

impl BundleManifest {
    pub fn app_version(&self) -> String {
        format!("{}+gw.{}", self.product_version, self.build_id)
    }

    fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err("随包清单 schema_version 不受支持".into());
        }
        if !is_product_version(&self.product_version) {
            return Err("随包清单 product_version 非法".into());
        }
        if !is_build_id(&self.build_id) {
            return Err("随包清单 build_id 非法".into());
        }
        if !is_hex(&self.git_commit, 7, 40) {
            return Err("随包清单 git_commit 非法".into());
        }
        if self.capability_profile != CAPABILITY_PROFILE {
            return Err("随包清单能力契约编号未知".into());
        }
        if !is_hex(&self.capability_digest, 64, 64) {
            return Err("随包清单 capability_digest 非法".into());
        }
        Ok(())
    }
}

fn is_product_version(value: &str) -> bool {
    let (core, suffix) = match value.split_once('-') {
        Some((core, suffix)) => (core, Some(suffix)),
        None => (value, None),
    };
    let parts = core.split('.').collect::<Vec<_>>();
    if parts.len() != 3
        || parts
            .iter()
            .any(|part| part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()))
    {
        return false;
    }
    match suffix {
        None => true,
        Some(suffix) => {
            !suffix.is_empty()
                && suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
        }
    }
}

fn is_build_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn is_hex(value: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&value.len()) && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

pub fn manifest_path(backend_root: &Path) -> PathBuf {
    backend_root.join(BUNDLE_MANIFEST_NAME)
}

pub fn load_manifest_from(path: &Path) -> Result<Option<BundleManifest>, String> {
    if !path.exists() {
        return Ok(None);
    }
    let raw = std::fs::read(path).map_err(|_| "随包清单不可读取，安装可能已损坏".to_string())?;
    let manifest: BundleManifest = serde_json::from_slice(&raw)
        .map_err(|_| "随包清单不是合法 JSON，安装可能已损坏".to_string())?;
    manifest.validate()?;
    Ok(Some(manifest))
}

pub fn load_bundle_manifest(backend_root: &Path) -> Result<Option<BundleManifest>, String> {
    load_manifest_from(&manifest_path(backend_root))
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct HandshakeReport {
    pub verified: bool,
    pub local: bool,
    pub remote_url: String,
    pub remote_version: String,
    pub version_source: String,
    pub profile: String,
    pub digest: String,
    pub digest_match: bool,
    pub source: String,
    pub trusted_bootstrap_available: bool,
    pub failures: Vec<String>,
    pub warnings: Vec<String>,
}

impl HandshakeReport {
    pub fn failure_summary(&self) -> String {
        if self.failures.is_empty() {
            String::new()
        } else {
            self.failures.join("；")
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TrustedStatus {
    pub available: bool,
    pub auto_login: bool,
    pub reason: String,
}

pub async fn evaluate(
    client: &Client,
    base: &Url,
    manifest: Option<&BundleManifest>,
) -> Result<HandshakeReport, String> {
    let local = is_loopback_url(base);
    let mut report = HandshakeReport {
        local,
        remote_url: format!(
            "{}://{}",
            base.scheme(),
            base.host_str().unwrap_or_default()
        ) + &base
            .port()
            .map(|port| format!(":{port}"))
            .unwrap_or_default(),
        remote_version: "unknown".into(),
        version_source: "unknown".into(),
        profile: "unknown".into(),
        source: if manifest.is_some() {
            "manifest".into()
        } else {
            "source".into()
        },
        ..HandshakeReport::default()
    };
    if manifest.is_none() && !local {
        report
            .failures
            .push("开发源构建无法验证远程 NAS 能力契约；请使用正式安装版".into());
    }

    let first = probe_core(client, base).await;
    let first = match first {
        Ok(value) => value,
        Err(error) => {
            report.failures.push(error);
            return Ok(report);
        }
    };
    apply_core(&mut report, &first, manifest);

    let second = probe_core(client, base).await;
    match second {
        Ok(second) => {
            if first.openapi_version != second.openapi_version {
                report
                    .failures
                    .push("两次探测返回的版本不一致（疑似缓存）".into());
            }
            if first.operations != second.operations {
                report
                    .failures
                    .push("两次探测返回的能力集合不一致（疑似缓存）".into());
            }
        }
        Err(error) => report.failures.push(error),
    }

    let auth = probe_json(client, base, "/api/asset-auth/status").await;
    match auth {
        Ok(response) => {
            if !response.status.is_success() {
                report.failures.push(format!(
                    "认证状态端点返回 HTTP {}",
                    response.status.as_u16()
                ));
            } else if let Some(problem) = cache_problem(&response.headers) {
                report.failures.push(problem);
            }
            if let Some(object) = response.body.as_object() {
                let missing = STATUS_REQUIRED_KEYS
                    .iter()
                    .filter(|key| !object.contains_key(**key))
                    .copied()
                    .collect::<Vec<_>>();
                if !missing.is_empty() {
                    report
                        .failures
                        .push(format!("认证状态缺少必需字段：{}", missing.join("、")));
                }
                if object.get("authenticated").and_then(Value::as_bool) != Some(false) {
                    report.failures.push("无会话认证状态必须为未登录".into());
                }
            } else {
                report.failures.push("认证状态响应不是 JSON 对象".into());
            }
        }
        Err(error) => report.failures.push(error),
    }

    let projects = probe_json(client, base, "/api/asset-registry/projects").await;
    match projects {
        Ok(response) => {
            if response.status != StatusCode::UNAUTHORIZED {
                report.failures.push(format!(
                    "未认证项目探测应返回 401，实际为 HTTP {}",
                    response.status.as_u16()
                ));
            }
            let code = response
                .body
                .get("detail")
                .and_then(Value::as_object)
                .and_then(|detail| detail.get("code"))
                .and_then(Value::as_str)
                .unwrap_or_default();
            if code != "UNAUTHORIZED" {
                report
                    .failures
                    .push("未认证项目探测未返回标准 UNAUTHORIZED 错误包".into());
            }
        }
        Err(error) => report.failures.push(error),
    }

    if let Some(manifest) = manifest {
        if report.digest != manifest.capability_digest {
            report
                .failures
                .push("后端能力摘要与安装包基线不一致".into());
        } else {
            report.digest_match = true;
        }
        if local && report.remote_version != manifest.app_version() {
            report
                .failures
                .push("本机后端版本与安装包清单不一致（未验证实例）".into());
        }
    } else if local {
        report
            .warnings
            .push("开发源构建：仅用于本机开发，不参与远程兼容放行".into());
    }

    report.verified = report.failures.is_empty();
    Ok(report)
}

struct CoreProbe {
    openapi_version: String,
    operations: Vec<String>,
    openapi_headers: HeaderMap,
    health_headers: HeaderMap,
}

async fn probe_core(client: &Client, base: &Url) -> Result<CoreProbe, String> {
    let health = probe_json(client, base, "/healthz").await?;
    if !health.status.is_success() {
        return Err(format!("健康检查返回 HTTP {}", health.status.as_u16()));
    }
    if let Some(problem) = cache_problem(&health.headers) {
        return Err(problem);
    }
    match health.body.as_object() {
        Some(object) => {
            if object.get("status").and_then(Value::as_str) != Some("ok") {
                return Err("健康检查 status 不是 ok".into());
            }
            if object.get("mode").and_then(Value::as_str) != Some("cleanroom") {
                return Err("健康检查 mode 不是 cleanroom".into());
            }
            let missing = HEALTH_REQUIRED_KEYS
                .iter()
                .filter(|key| !object.contains_key(**key))
                .copied()
                .collect::<Vec<_>>();
            if !missing.is_empty() {
                return Err(format!("健康检查缺少必需字段：{}", missing.join("、")));
            }
        }
        None => return Err("健康检查响应不是 JSON 对象".into()),
    }

    let openapi = probe_json(client, base, "/openapi.json").await?;
    if !openapi.status.is_success() {
        return Err(format!("OpenAPI 返回 HTTP {}", openapi.status.as_u16()));
    }
    if let Some(problem) = cache_problem(&openapi.headers) {
        return Err(problem);
    }
    let title = openapi
        .body
        .get("info")
        .and_then(Value::as_object)
        .and_then(|info| info.get("title"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    if title != OPENAPI_TITLE {
        return Err("OpenAPI 身份与神工坊不符".into());
    }
    let openapi_version = openapi
        .body
        .get("info")
        .and_then(Value::as_object)
        .and_then(|info| info.get("version"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let operations = openapi_operations(&openapi.body);
    let missing = REQUIRED_OPERATIONS
        .iter()
        .filter(|operation| !operations.iter().any(|present| present == *operation))
        .copied()
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(format!("能力契约缺少必需端点：{}", missing.join("、")));
    }
    Ok(CoreProbe {
        openapi_version,
        operations,
        openapi_headers: openapi.headers,
        health_headers: health.headers,
    })
}

fn apply_core(report: &mut HandshakeReport, core: &CoreProbe, manifest: Option<&BundleManifest>) {
    report.remote_version = if core.openapi_version.is_empty() {
        "unknown".into()
    } else {
        core.openapi_version.clone()
    };
    report.version_source = version_source(&core.openapi_version);
    report.profile = if manifest.is_some() {
        CAPABILITY_PROFILE.into()
    } else {
        "unknown".into()
    };
    report.trusted_bootstrap_available = core
        .operations
        .iter()
        .any(|operation| operation == TRUSTED_BOOTSTRAP_OPERATION);
    report.digest = capability_digest();
    if let Some(problem) =
        cache_problem(&core.health_headers).or_else(|| cache_problem(&core.openapi_headers))
    {
        report.failures.push(problem);
    }
    if version_source(&core.openapi_version) == "unknown" {
        report
            .failures
            .push("后端版本缺少 +gw. 来源标记，不能作为真实版本".into());
    }
}

fn version_source(version: &str) -> String {
    version
        .split_once("+gw.")
        .map(|(_, source)| source.to_string())
        .filter(|source| !source.is_empty() && source != "unknown")
        .unwrap_or_else(|| "unknown".into())
}

fn openapi_operations(schema: &Value) -> Vec<String> {
    let mut operations = Vec::new();
    if let Some(paths) = schema.get("paths").and_then(Value::as_object) {
        for (path, methods) in paths {
            if let Some(methods) = methods.as_object() {
                for method in methods.keys() {
                    let upper = method.to_ascii_uppercase();
                    if matches!(
                        upper.as_str(),
                        "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS"
                    ) {
                        operations.push(format!("{upper} {path}"));
                    }
                }
            }
        }
    }
    operations.sort();
    operations
}

struct JsonProbe {
    status: StatusCode,
    headers: HeaderMap,
    body: Value,
}

async fn probe_json(client: &Client, base: &Url, path: &str) -> Result<JsonProbe, String> {
    let mut url = base.clone();
    url.set_path(path);
    let nonce = next_nonce();
    url.query_pairs_mut().append_pair("gw_probe", &nonce);
    let response = client
        .get(url)
        .header("Cache-Control", "no-cache, no-store")
        .header("Pragma", "no-cache")
        .send()
        .await
        .map_err(|_| "后端探测请求失败".to_string())?;
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = response
        .bytes()
        .await
        .map_err(|_| "后端探测响应读取失败".to_string())?;
    if bytes.len() > 4 * 1024 * 1024 {
        return Err("后端探测响应过大，已拒绝".into());
    }
    let body: Value =
        serde_json::from_slice(&bytes).map_err(|_| format!("{} 响应不是合法 JSON", path))?;
    Ok(JsonProbe {
        status,
        headers,
        body,
    })
}

fn next_nonce() -> String {
    let counter = PROBE_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("{nanos:x}{counter:x}")
}

pub fn cache_problem(headers: &HeaderMap) -> Option<String> {
    let header = |name: &str| {
        headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
    };
    if let Ok(age) = header("age").parse::<u64>() {
        if age > 0 {
            return Some("探测响应出现 Age 缓存信号，版本结果不可信".into());
        }
    }
    let x_cache = header("x-cache");
    if !x_cache.is_empty() && x_cache != "miss" && x_cache != "bypass" && x_cache != "dynamic" {
        return Some("探测响应出现 X-Cache 缓存命中信号，版本结果不可信".into());
    }
    let cf_cache = header("cf-cache-status");
    if cf_cache == "hit" || cf_cache == "revalidated" {
        return Some("探测响应出现 CDN 缓存命中信号，版本结果不可信".into());
    }
    if !header("via").is_empty() {
        return Some("探测响应经过中间代理（Via），版本结果不可信".into());
    }
    let cache_control = header("cache-control");
    if !cache_control.contains("no-store") {
        return Some("探测响应未声明 no-store，版本结果不可信".into());
    }
    None
}

pub fn capability_digest() -> String {
    sha256_hex(canonical_json().as_bytes())
}

fn canonical_json() -> String {
    let operations = REQUIRED_OPERATIONS
        .iter()
        .map(|operation| format!("\"{operation}\""))
        .collect::<Vec<_>>()
        .join(",");
    let health_keys = HEALTH_REQUIRED_KEYS
        .iter()
        .map(|key| format!("\"{key}\""))
        .collect::<Vec<_>>()
        .join(",");
    let status_keys = STATUS_REQUIRED_KEYS
        .iter()
        .map(|key| format!("\"{key}\""))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "{{\"health\":{{\"mode\":\"cleanroom\",\"required_keys\":[{health_keys}],\"status\":\"ok\"}},\"openapi\":{{\"operations\":[{operations}],\"title\":\"{OPENAPI_TITLE}\"}},\"profile\":\"{CAPABILITY_PROFILE}\",\"projects_unauth\":{{\"code\":\"UNAUTHORIZED\",\"status\":401}},\"status\":{{\"authenticated\":false,\"required_keys\":[{status_keys}]}},\"version_provenance\":true}}"
    )
}

pub fn is_loopback_url(url: &Url) -> bool {
    matches!(url.host_str(), Some("127.0.0.1") | Some("[::1]"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::header::{HeaderName, HeaderValue, AGE, CACHE_CONTROL, VIA};

    #[test]
    fn digest_matches_python_contract_baseline() {
        assert_eq!(
            capability_digest(),
            "cc47d8d4ac74adc87f073127886e13e71ffbcd3aef3248a11bb957d55c4086e2"
        );
    }

    #[test]
    fn manifest_requires_real_version_and_digest() {
        let valid = BundleManifest {
            schema_version: 1,
            product_version: "1.2.3".into(),
            build_id: "build-1".into(),
            git_commit: "abcdef0".into(),
            capability_profile: CAPABILITY_PROFILE.into(),
            capability_digest: capability_digest(),
            runtime_inventory_sha256: String::new(),
        };
        assert!(valid.validate().is_ok());
        assert_eq!(valid.app_version(), "1.2.3+gw.build-1");

        let mut static_default = valid.clone();
        static_default.product_version = "0.1.0".into();
        assert!(static_default.validate().is_ok());
        // 0.1.0 本身不是错；只有缺少清单/来源标记时才按未知处理。
        static_default.build_id = "bad id".into();
        assert!(static_default.validate().is_err());

        let mut bad_digest = valid;
        bad_digest.capability_digest = "not-a-digest".into();
        assert!(bad_digest.validate().is_err());
    }

    #[test]
    fn version_source_requires_marker() {
        assert_eq!(version_source("1.2.3"), "unknown");
        assert_eq!(version_source("0.1.0"), "unknown");
        assert_eq!(version_source("1.2.3+gw.build-1"), "build-1");
        assert_eq!(version_source("unknown"), "unknown");
    }

    #[test]
    fn cache_signals_fail_closed_but_miss_is_allowed() {
        let mut headers = HeaderMap::new();
        headers.insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
        assert!(cache_problem(&headers).is_none());

        headers.insert(
            HeaderName::from_static("x-cache"),
            HeaderValue::from_static("MISS"),
        );
        assert!(cache_problem(&headers).is_none());

        headers.insert(
            HeaderName::from_static("x-cache"),
            HeaderValue::from_static("HIT"),
        );
        assert!(cache_problem(&headers).is_some());

        headers.remove("x-cache");
        headers.insert(AGE, HeaderValue::from_static("12"));
        assert!(cache_problem(&headers).is_some());

        headers.remove(AGE);
        headers.insert(VIA, HeaderValue::from_static("1.1 nas-proxy"));
        assert!(cache_problem(&headers).is_some());

        headers.remove(VIA);
        headers.insert(
            CACHE_CONTROL,
            HeaderValue::from_static("public, max-age=60"),
        );
        assert!(cache_problem(&headers).is_some());
    }

    #[test]
    fn only_literal_loopback_is_local_for_ticket_purposes() {
        assert!(is_loopback_url(
            &Url::parse("http://127.0.0.1:2077/").unwrap()
        ));
        assert!(is_loopback_url(&Url::parse("http://[::1]:2077/").unwrap()));
        assert!(!is_loopback_url(
            &Url::parse("http://localhost:2077/").unwrap()
        ));
        assert!(!is_loopback_url(
            &Url::parse("http://nas.example:2077/").unwrap()
        ));
    }
}
