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

## Directory bridge commands (QIN-432)

The client registers `tauri-plugin-dialog` and exposes five bridge commands: `pick_client_folder`, `pick_client_upload_files`, `read_client_file`, `get_client_setting`, and `set_client_setting`. They let the connected server page open the **client's own** native folder dialog and persist the client-side upload source directory next to the saved server URL in the OS application settings directory (`client.json`). Client settings never leave the device: they are not submitted to the server and are not validated against server allowed roots.

Security boundaries:

- Every bridge command validates the webview URL in Rust: only the local discovery screen (`tauri.localhost` / `asset://localhost`) and the page whose origin equals the saved server URL may call them. Other pages are rejected.
- `read_client_file` only returns bytes for files picked with `pick_client_upload_files` in the same client session; arbitrary filesystem reads are rejected.
- `get_client_setting`/`set_client_setting` accept only the whitelisted key `upload_source_dir`.
- The capability file grants `core:default` plus remote IPC reachability; command-level authorization is enforced in Rust, not by the capability list.

## Browser installation

The browser PWA uses `/static/manifest.json` and `/static/sw.js`. Browsers require a secure origin for installation and service workers: use HTTPS with a certificate trusted by each client device, or `localhost`. Direct `http://<NAS-IP>:2077` remains usable in a browser and in the desktop client, but browsers cannot install the PWA or register its offline worker from that HTTP LAN address. An HTTPS reverse proxy can terminate TLS and forward the existing paths to NAS port `2077`.
