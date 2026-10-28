# Gods Workbench Desktop Client

## Prerequisites

- Rust stable for the MSVC target and the C++ build tools.
- Microsoft Edge WebView2 Runtime on Windows.
- Tauri CLI 2.12.3: `cargo install tauri-cli --version 2.12.3 --locked`.
- A Gods Workbench NAS reachable on the same private LAN, serving port `2077` and `GET /healthz` with `status: "ok"`.

## Run and package

From this directory:

```powershell
cargo tauri dev
cargo tauri build
```

The initial screen checks the local host and each active private IPv4 subnet, capped to a `/24`. It sends concurrent unauthenticated `GET /healthz` probes to port `2077`; it does not submit API keys or other credentials. Manual connections must use a private IP, `localhost`, a single-label LAN hostname, or a `.local` hostname. Only the normalized server URL is saved in the operating-system application settings directory.

Closing the window hides it in the system tray. The tray menu can reopen the Workbench, return to NAS discovery, or quit the client.

## Browser installation

The browser PWA uses `/static/manifest.json` and `/static/sw.js`. Browsers require a secure origin for installation and service workers: use HTTPS with a certificate trusted by each client device, or `localhost`. Direct `http://<NAS-IP>:2077` remains usable in a browser and in the desktop client, but browsers cannot install the PWA or register its offline worker from that HTTP LAN address. An HTTPS reverse proxy can terminate TLS and forward the existing paths to NAS port `2077`.
