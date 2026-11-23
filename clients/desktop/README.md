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

The initial screen starts the workbench locally first: it reuses an already healthy loopback instance or launches the bundled backend, waits for health and completes the version/capability handshake before entering the full workbench. It never scans the LAN or connects to a remote server automatically. Remote connections are a secondary entry, manual or after an explicit "scan LAN" click, and must use a private IP, `localhost`, a single-label LAN hostname, or a `.local` hostname. Only the normalized server URL is saved in the operating-system application settings directory.

Closing the window hides it in the system tray. The tray menu can reopen the Workbench, return to NAS discovery, or quit the client. For scripted checks, `gods-workbench-desktop.exe --quit` uses the same cleanup path as the tray quit action, and `gods-workbench-desktop.exe --diagnostics <file>` writes a version/handshake/trusted/backend snapshot without starting a second client. Both commands authenticate over a loopback control channel protected by the per-instance lock token and never accept remote callers.

## Local backend discovery and automatic launch (QIN-593)

Local-first startup and remote entry (QIN-626): on startup, the desktop client always targets the local workbench first. It probes the loopback health endpoint and reuses a healthy local instance; otherwise it launches the bundled local backend in production mode. LAN discovery does not run at startup and cannot divert the first launch into a server list. The secondary "Connect to remote workbench" entry opens a manual address form with an optional, explicitly clicked LAN scan; a saved remote address is only prefilled, never auto-connected.

The local backend is launched as follows:
- **Backend root resolution**: Checked in the following order:
  1. `GW_BACKEND_ROOT` or `GW_ROOT_DIR` environment variables (must contain `run.py` and `gw/`).
  2. Current working directory.
  3. Parent directory hierarchy up to 6 levels above the running executable.
- **Python executable resolution**: Checked in the following order:
  1. `GW_PYTHON` environment variable pointing to a Python executable.
  2. Project virtual environment at `<backend_root>/.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix), or `venv/`.
  3. System PATH `python` or `python3` command (verified via `--version`).
- **Execution & Port**: Runs `python run.py --prod` bound to `127.0.0.1:2077` without `--open-browser` (no external browser is silently launched). If port `2077` is already bound by a foreign process, launch aborts with an actionable error and guidance.
- **Single Instance**: Arbitrated via atomic exclusive lock file creation (`create_new`). Concurrent or subsequent launches wake and focus the existing desktop window (even if hidden in the system tray) and cleanly exit without spawning duplicate client processes or duplicate backends.
- **Lifecycle**: Backends launched by the desktop client terminate when the user chooses "Quit" from the tray menu. Backends launched externally (such as via `start.bat`) are never stopped by the desktop client.

## Windows standard installer (QIN-626 / QIN-638)

The Windows NSIS installer ships the desktop shell, a private CPython 3.11.9 embeddable runtime, the `gw/` backend source tree, `web/`, `run.py`, hash-locked wheels, and `bundle-manifest.json` under `backend/`. It uses `perMachine` mode (defaulting to `%ProgramFiles%\Gods Workbench`, changeable in the wizard) and provides a second wizard page for the user data directory (fresh installs default to `%LOCALAPPDATA%\Gods Workbench data` for the originally logged-in standard user, persisted in the single authoritative bootstrap pointer `%LOCALAPPDATA%\GodsWorkbench\runtime-root.json`; upgrades preserve existing `%LOCALAPPDATA%\GodsWorkbench` data or existing pointers). The install directory and the effective data directory must remain distinct and non-overlapping after path normalization; the installer, backend, and desktop shell all verify this relationship.

The desktop and the bundled backend are built from the same release version. Release tags drive the product version; the collapsed "About / connection details" section shows the desktop version, bundled backend version and process state, the connected backend version and its provenance, the capability-contract profile/digest, the trusted-local status, and the latest error. The first screen is intentionally limited to starting the local workbench; failures show a localized reason, next step, retry action, and a shortcut to the full details. The shell uses the workbench black-gold design tokens (`#08090c` / `#111216` / `#e8e8ea` / `#dfc384`, light `rgb(241, 242, 245)` / `rgb(255, 255, 255)` / `rgb(17, 20, 26)`), supports a remembered dark/light toggle, and keeps version/diagnostic/command text out of the first screen. Missing or legacy `0.1.0` values are shown as unknown and never treated as compatible. Remote NAS connections are judged by the [`gw-compat-v1` capability contract](../../docs/guides/DESKTOP-COMPATIBILITY-CONTRACT.md), not by exact version equality.

Upgrades back up the previous program files and the client configuration, write a journal (`prepare` → `backed_up` → `installing` → `installed` → `verified` → `committed`), run `run.py --self-check --expect-version <version>` after install, and restore the backup on failure. Only an interrupted (`installing`) or failed (`installed`) prior upgrade triggers automatic recovery; `verified` and `committed` are successful generations, so the next upgrade backs up that installed generation instead of restoring an older one. The journal is committed only after the first successful version/capability handshake; a failed install or first launch must not be reported as success. Uninstall removes program files and shortcuts but keeps the data root and upgrade backups.

**Unsigned test builds:** the first test packages may be unsigned. Windows may therefore show a warning or block the installer. Download only from a trusted source and verify the published SHA-256 (or other published digest) before running. Do not disable Defender/SmartScreen, weaken security policy, or bypass an organization's controls; this project does not provide instructions for doing so. An unsigned test build is not a formal release authorization.

Security boundaries for the installed client:

- The trusted-local one-time ticket is issued only for a loopback backend whose installed manifest matches, is delivered only in the URL fragment, is read from child-process memory, and is never written to disk, logs, or a remote host.
- Before trusted-local entry the desktop verifies the Windows ACL of the account directory and device-key files (current user, SYSTEM, and Administrators only); failure closes automatic entry and keeps password login available without changing the ACL.
- Remote/NAS connections never receive a local trusted ticket.
- The desktop and QIN-630 backend implement this path together. Installing QIN-626 without the QIN-630 backend does not make desktop passwordless entry complete; joint acceptance is required before claiming it.

To build the payload and installer from the repository root:

```powershell
python clients/desktop/packaging/build_release.py --version <X.Y.Z> --build-id <build-id> --output build/desktop-release --download-wheels
cargo tauri build --config build/desktop-release/tauri.release.conf.json --bundles nsis --ci -- --locked
```

### Automated install acceptance

[`packaging/acceptance/run_install_acceptance.ps1`](packaging/acceptance/run_install_acceptance.ps1) is the repeatable black-box entry for the installed product. It verifies the installer SHA-256/version/commit, runs a fresh install, opens the real desktop window, checks local-first startup and cold/hot start, confirms `/healthz` and `/openapi.json` carry `no-store` and a `+gw.` source marker, drives the installed shell through WebView2 CDP for failure/retry, remote-compatible and remote-incompatible entry, dark/light toggling, and 724/900/1440/1920 layouts, checks 2077 single-instance behavior, injects stale and live-unresponsive locks, holds port 2077 with a foreign process, rotates two upgrade generations, forces a failed third upgrade to prove rollback to the immediately preceding generation, and uninstalls while checking that user data and upgrade backups remain. Evidence is written to `acceptance.json` and `acceptance.md` with installer exit codes, journal states, generation markers, PIDs, listeners, headers and diagnostics; local paths are replaced by placeholders.

On a shared machine that already owns `%LOCALAPPDATA%\GodsWorkbench`, pass `-LocalAppDataOverride` with a fresh `gw-qin626-accept-*` directory so the desktop and backend use isolated data while the real data root is never touched. The installer's postinstall self-check is read-only against the real data root. The real `%LOCALAPPDATA%\ai.godsworkbench.desktop` journal/backups are still exercised; `-Reset` only removes them when they carry the acceptance marker written by this script, and a default run removes the acceptance-owned state after writing evidence. The remote scenarios start a second bundled backend bound to the machine's private LAN address on port `2078` and a proxy that deliberately strips `no-store` or the `+gw.` marker; both addresses are replaced by placeholders in evidence. See [`packaging/acceptance/README.md`](packaging/acceptance/README.md) for the full command and scenario list.

The acceptance entry records trusted-ticket availability but does not require it: QIN-626 is not a claim that desktop passwordless entry is complete until QIN-630 is merged and jointly accepted.

## Directory bridge commands (QIN-432)

The client registers `tauri-plugin-dialog` and exposes five bridge commands: `pick_client_folder`, `pick_client_upload_files`, `read_client_file`, `get_client_setting`, and `set_client_setting`. They let the connected server page open the **client's own** native folder dialog and persist the client-side upload source directory next to the saved server URL in the OS application settings directory (`client.json`). Client settings never leave the device: they are not submitted to the server and are not validated against server allowed roots.

Security boundaries:

- Every bridge command validates the webview URL in Rust: only the local discovery screen (`tauri.localhost` / `asset://localhost`) and the page whose origin equals the saved server URL may call them. Other pages are rejected.
- `read_client_file` only returns bytes for files picked with `pick_client_upload_files` in the same client session; arbitrary filesystem reads are rejected.
- `get_client_setting`/`set_client_setting` accept only the whitelisted key `upload_source_dir`.
- The capability file grants `core:default` plus remote IPC reachability; command-level authorization is enforced in Rust, not by the capability list.

## Browser installation

The browser PWA uses `/static/manifest.json` and `/static/sw.js`. Browsers require a secure origin for installation and service workers: use HTTPS with a certificate trusted by each client device, or `localhost`. Direct `http://<NAS-IP>:2077` remains usable in a browser and in the desktop client, but browsers cannot install the PWA or register its offline worker from that HTTP LAN address. An HTTPS reverse proxy can terminate TLS and forward the existing paths to NAS port `2077`.
