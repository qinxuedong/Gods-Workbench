#requires -Version 5.1
<#
QIN-626 Windows installer acceptance entry.

Runs the NSIS installer, the Tauri desktop client and the bundled backend as a
real user process. The script is intentionally evidence-first: every scenario
records exit codes, journal states, generation markers, PIDs, listeners and the
last desktop error before asserting.

Isolation:
  * -LocalAppDataOverride redirects LOCALAPPDATA for the desktop process and the
    backend. This lets a non-admin QA account run on a machine that already owns
    production data without touching it.
  * The installer's postinstall self-check stays read-only against the real
    data root; it never creates, writes or deletes production data.
  * The real %LOCALAPPDATA%\ai.godsworkbench.desktop (journal, backups, client
    config) is still exercised; -Reset removes it only when it carries the
    acceptance marker written by this script. After evidence is written, the
    default run removes the acceptance-owned journal, backups and client config.

The script exits 0 only when all checks pass. Evidence is written to
<OutputDir>\acceptance.json and <OutputDir>\acceptance.md.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$ExpectedSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [string]$InstallDir = "",
    [string]$OutputDir = "",
    [string]$LocalAppDataOverride = "",
    [switch]$Reset,
    [switch]$KeepInstalled,
    [switch]$KeepState
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$script:startedAt = (Get-Date).ToUniversalTime()
$script:checks = New-Object System.Collections.ArrayList
$script:steps = New-Object System.Collections.ArrayList
$script:installerResults = New-Object System.Collections.ArrayList
$script:lifecycle = [ordered]@{}
$script:desktop = $null
$script:desktopLaunch = $null
$script:holders = New-Object System.Collections.ArrayList
$script:fatal = ""
$script:result = "FAIL"
$script:outputDirPath = Join-Path $env:TEMP "gw-qin626-accept-uninitialized"
$script:installDirPath = Join-Path $env:PUBLIC "gw-qin626-accept-uninitialized"
$script:desktopExe = Join-Path $script:installDirPath "gods-workbench-desktop.exe"
$script:installedRunPy = Join-Path $script:installDirPath "backend\run.py"
$script:uninstaller = Join-Path $script:installDirPath "uninstall.exe"
$script:realLocalAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA", "Process")
if (-not $script:realLocalAppData) { $script:realLocalAppData = [Environment]::GetFolderPath("LocalApplicationData") }
$script:processLocalAppData = $script:realLocalAppData
$script:upgradeRoot = Join-Path $script:realLocalAppData "ai.godsworkbench.desktop\upgrade"
$script:journalPath = Join-Path $script:upgradeRoot "journal.txt"
$script:backupProgram = Join-Path $script:upgradeRoot "backup-last\program"
$script:clientConfigDir = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "ai.godsworkbench.desktop"
$script:clientConfig = Join-Path $script:clientConfigDir "client.json"
$script:serverConfig = Join-Path $script:clientConfigDir "server.json"
$script:markerPath = Join-Path $script:clientConfigDir "qin626-acceptance-owned.json"
$script:sentinelPath = Join-Path $script:processLocalAppData "GodsWorkbench\acceptance-sentinel.json"
$script:lockPath = Join-Path ([IO.Path]::GetTempPath()) "ai.godsworkbench.desktop.instance.json"
$script:powershellPath = ""
$script:installerPath = ""
$script:installerSha = ""
$script:expectedSha256Value = ""
$script:expectedVersionValue = ""
$script:expectedCommitValue = ""
$script:dataMode = "uninitialized"
$script:installDirClaimed = $false
$script:overrideClaimed = $false
$script:stateClaimed = $false

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    $null = $script:checks.Add([pscustomobject]@{
        name = $Name
        ok = $Ok
        detail = [string]$Detail
    })
    if (-not $Ok) {
        Write-Host ("[FAIL] {0}: {1}" -f $Name, $Detail)
    } else {
        Write-Host ("[PASS] {0}" -f $Name)
    }
}

function Require-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    Add-Check -Name $Name -Ok $Ok -Detail $Detail
    if (-not $Ok) {
        throw ("{0}: {1}" -f $Name, $Detail)
    }
}

function Add-Step {
    param([string]$Name, [string]$Detail = "")
    $null = $script:steps.Add([pscustomobject]@{ name = $Name; detail = [string]$Detail })
    Write-Host ("[STEP] {0}" -f $Name)
}

function Format-SafeText {
    param([object]$Value)
    if ($null -eq $Value) { return "" }
    $text = [string]$Value
    $pairs = @(
        @($script:outputDirPath, "<OUTPUT_DIR>"),
        @($script:processLocalAppData, "<PROCESS_LOCALAPPDATA>"),
        @($script:realLocalAppData, "<LOCALAPPDATA>"),
        @($script:installDirPath, "<INSTALL_DIR>"),
        @([Environment]::GetFolderPath("ApplicationData"), "<APPDATA>"),
        @([Environment]::GetFolderPath("LocalApplicationData"), "<LOCALAPPDATA>"),
        @($env:USERPROFILE, "<USERPROFILE>"),
        @($env:TEMP, "<TEMP>")
    )
    foreach ($pair in $pairs) {
        $needle = [string]$pair[0]
        if ($needle.Length -gt 2) {
            $text = $text.Replace($needle, [string]$pair[1])
        }
    }
    return $text
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Get-RegistryString {
    param([string]$Path, [string]$Name)
    try {
        return [string](Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop).$Name
    } catch {
        return ""
    }
}

function Get-Generation {
    param([string]$RunPy)
    if (-not (Test-Path -LiteralPath $RunPy)) { return "<missing>" }
    $text = [IO.File]::ReadAllText($RunPy)
    $found = [regex]::Matches($text, "QIN626_ACCEPTANCE_GENERATION=([0-9A-Za-z._-]+)")
    if ($found.Count -eq 0) { return "<none>" }
    return $found[$found.Count - 1].Groups[1].Value
}

function Set-Generation {
    param([string]$RunPy, [string]$Generation)
    Require-Check "generation.run-py-exists-$Generation" (Test-Path -LiteralPath $RunPy) "installed run.py missing"
    $text = [IO.File]::ReadAllText($RunPy)
    if ($text -match ("QIN626_ACCEPTANCE_GENERATION=" + [regex]::Escape($Generation))) {
        return
    }
    $marker = "`r`n# QIN626_ACCEPTANCE_GENERATION=$Generation`r`n"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::AppendAllText($RunPy, $marker, $encoding)
    Add-Check "generation.marked-$Generation" ((Get-Generation -RunPy $RunPy) -eq $Generation) "generation marker not visible"
}

function Get-JournalState {
    if (-not (Test-Path -LiteralPath $script:journalPath)) { return "<missing>" }
    return ([IO.File]::ReadAllText($script:journalPath)).Trim()
}

function Get-BackupGeneration {
    $backupRun = Join-Path $script:backupProgram "backend\run.py"
    return (Get-Generation -RunPy $backupRun)
}

function Get-SelfCheckTail {
    if (-not (Test-Path -LiteralPath $script:upgradeRoot)) { return "" }
    $files = @(Get-ChildItem -LiteralPath $script:upgradeRoot -Filter "self-check*.log" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) { return "" }
    $lines = @(Get-Content -LiteralPath $files[0].FullName -Tail 24 -ErrorAction SilentlyContinue)
    return ($lines -join "`n")
}

function Get-ListeningLines {
    param([int]$Port)
    $lines = @()
    try {
        $lines = @(& netstat.exe -ano -p tcp 2>$null)
    } catch {
        return @()
    }
    $result = @()
    foreach ($line in $lines) {
        if ($line -match ("^\s*TCP\s+\S+:" + $Port + "\s+\S+\s+LISTENING\s+(\d+)\s*$")) {
            $result += [pscustomobject]@{ line = $line.Trim(); pid = [int]$Matches[1] }
        }
    }
    return $result
}

function Get-ListeningPids {
    param([int]$Port)
    return @(Get-ListeningLines -Port $Port | ForEach-Object { $_.pid } | Sort-Object -Unique)
}

function Test-PortOpen {
    param([int]$Port, [int]$TimeoutMs = 800)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-HttpResult {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
        return [pscustomobject]@{ ok = $true; status = [int]$response.StatusCode; body = [string]$response.Content }
    } catch {
        $status = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        return [pscustomobject]@{ ok = $false; status = $status; body = [string]$_.Exception.Message }
    }
}

function Wait-Health {
    param([int]$TimeoutSeconds = 150)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Get-HttpResult -Url "http://127.0.0.1:2077/healthz"
        if ($last.ok -and $last.status -eq 200) { return $last }
        Start-Sleep -Milliseconds 1000
    }
    return $last
}

function Get-OpenApiVersion {
    $result = Get-HttpResult -Url "http://127.0.0.1:2077/openapi.json"
    if (-not $result.ok) { return "" }
    try {
        return [string](($result.body | ConvertFrom-Json).info.version)
    } catch {
        return ""
    }
}

function Test-ExpectedOpenApiVersion {
    param([string]$Version)
    if (-not $Version) { return $false }
    return $Version.StartsWith($script:expectedVersionValue + "+gw.", [StringComparison]::Ordinal)
}

function Get-DesktopPids {
    $pids = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path -and ($process.Path -eq $script:desktopExe)) {
                $pids += [int]$process.Id
            }
        } catch {
        }
    }
    return @($pids | Sort-Object -Unique)
}

function Get-BackendPids {
    $pids = @()
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        return @()
    }
    foreach ($process in $processes) {
        $command = [string]$process.CommandLine
        $path = [string]$process.ExecutablePath
        if ($command -like "*$script:installDirPath*" -and $command -like "*run.py*" -and ($command -like "*--prod*" -or $command -like "*--self-check*")) {
            $pids += [int]$process.ProcessId
        } elseif ($path -like "$script:installDirPath*" -and $process.Name -match "^python") {
            $pids += [int]$process.ProcessId
        }
    }
    return @($pids | Sort-Object -Unique)
}

function Get-ProcessStartTime {
    param([int]$ProcessId)
    try {
        return [long]((Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToFileTimeUtc())
    } catch {
        return [long]0
    }
}

function New-InstanceLockJson {
    param([int]$Port, [string]$Token, [int]$OwnerPid, [string]$ExePath)
    $payload = [ordered]@{
        port = $Port
        token = $Token
        pid = $OwnerPid
        exe_path = $ExePath
        process_start_time = (Get-ProcessStartTime -ProcessId $OwnerPid)
    }
    return ($payload | ConvertTo-Json -Compress)
}

function Get-InstanceLock {
    $data = Read-JsonFile -Path $script:lockPath
    if ($null -eq $data) { return $null }
    return $data
}

function Wait-ForLock {
    param([int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $script:lockPath) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ForFile {
    param([string]$Path, [int]$TimeoutSeconds = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Path) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return (Test-Path -LiteralPath $Path)
}

function Wait-ForNoLock {
    param([int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path -LiteralPath $script:lockPath)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ForPortFree {
    param([int]$Port, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortOpen -Port $Port -TimeoutMs 500)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ProcessExit {
    param($Process, [int]$TimeoutSeconds = 60)
    try {
        return $Process.WaitForExit($TimeoutSeconds * 1000)
    } catch {
        return $false
    }
}

function Get-ProcessExitCode {
    param($Process)
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) { return $null }
        return [int]$Process.ExitCode
    } catch {
        return $null
    }
}

function Start-GwProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$Label,
        [hashtable]$Environment = @{},
        [string]$WorkingDirectory = ""
    )
    $stdout = Join-Path $script:outputDirPath ($Label + ".stdout.log")
    $stderr = Join-Path $script:outputDirPath ($Label + ".stderr.log")
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
    }
    try {
        $startParams = @{
            FilePath = $FilePath
            ArgumentList = $Arguments
            PassThru = $true
            RedirectStandardOutput = $stdout
            RedirectStandardError = $stderr
        }
        if ($WorkingDirectory) { $startParams.WorkingDirectory = $WorkingDirectory }
        $process = Start-Process @startParams
    } finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
    return [pscustomobject]@{ process = $process; stdout = $stdout; stderr = $stderr }
}

function Start-DesktopProcess {
    param([string]$Label, [string[]]$Arguments = @(), [string]$WorkingDirectory = "")
    if (-not $WorkingDirectory) { $WorkingDirectory = $script:installDirPath }
    $environment = @{
        LOCALAPPDATA = $script:processLocalAppData
        GW_BACKEND_ROOT = ""
        GW_ROOT_DIR = ""
        GW_PYTHON = ""
        GW_BUNDLE_MANIFEST = ""
        GW_PRODUCT_VERSION = ""
    }
    return (Start-GwProcess -FilePath $script:desktopExe -Arguments $Arguments -Label $Label -Environment $environment -WorkingDirectory $WorkingDirectory)
}

function Wait-DesktopWindow {
    param($Process, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $Process.Refresh()
            if ($Process.HasExited) { return $null }
            $handle = $Process.MainWindowHandle
            if ($handle.ToInt64() -ne 0) {
                $title = [string]$Process.MainWindowTitle
                if ($title.Length -gt 0) {
                    return [pscustomobject]@{ handle = $handle; title = $title }
                }
            }
        } catch {
            return $null
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Get-WindowInfo {
    param($Process)
    try {
        $Process.Refresh()
    } catch {
    }
    $title = ""
    try { $title = [string]$Process.MainWindowTitle } catch { }
    $texts = @()
    try {
        Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
        Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$Process.Id)
        $elements = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition))
        foreach ($element in $elements) {
            try {
                $name = [string]$element.Current.Name
                if ($name.Length -gt 0) { $texts += $name }
            } catch {
            }
            if ($texts.Count -ge 24) { break }
        }
    } catch {
    }
    if ($texts.Count -eq 0 -and $title.Length -gt 0) { $texts = @($title) }
    return [pscustomobject]@{
        title = $title
        text = (($texts | Select-Object -Unique) -join " | ")
        handle = $Process.MainWindowHandle
    }
}

function Wait-FatalDialog {
    param($Process, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $Process.Refresh()
            if ($Process.HasExited) { return $null }
            if ($Process.MainWindowHandle.ToInt64() -ne 0) {
                return (Get-WindowInfo -Process $Process)
            }
        } catch {
            return $null
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Close-Window {
    param([IntPtr]$Handle)
    if ($Handle.ToInt64() -eq 0) { return $false }
    try {
        return [GwNative]::PostMessage($Handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    } catch {
        return $false
    }
}

function Save-Screenshot {
    param([string]$Path)
    try {
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $width = [int]$bounds.Width
        $height = [int]$bounds.Height
        $bitmap = New-Object System.Drawing.Bitmap($width, $height)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()
        return $true
    } catch {
        return $false
    }
}

function Start-TcpHolder {
    param([int]$Port, [string]$Label)
    $ready = Join-Path $script:outputDirPath ($Label + ".port.txt")
    $holderScript = Join-Path $script:outputDirPath ($Label + ".holder.ps1")
    $code = @"
`$listener = New-Object System.Net.Sockets.TcpListener -ArgumentList ([System.Net.IPAddress]::Loopback), $Port
`$listener.Start()
`$actual = [int]`$listener.LocalEndpoint.Port
[IO.File]::WriteAllText('$ready', [string]`$actual)
while (`$true) {
    try {
        `$client = `$listener.AcceptTcpClient()
        Start-Sleep -Seconds 30
        `$client.Close()
    } catch {
        Start-Sleep -Seconds 1
    }
}
"@
    [IO.File]::WriteAllText($holderScript, $code, (New-Object System.Text.ASCIIEncoding))
    $started = Start-Process -FilePath $script:powershellPath -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ('"' + $holderScript + '"')) -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $ready) {
            $actual = 0
            try { $actual = [int]([IO.File]::ReadAllText($ready).Trim()) } catch { }
            if ($actual -gt 0) {
                $holder = [pscustomobject]@{ process = $started; port = $actual; script = $holderScript; ready = $ready }
                $null = $script:holders.Add($holder)
                return $holder
            }
        }
        if ($started.HasExited) { break }
        Start-Sleep -Milliseconds 300
    }
    try { Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue } catch { }
    throw ("TCP holder failed to bind port {0}" -f $Port)
}

function Stop-TcpHolder {
    param($Holder)
    if ($null -eq $Holder) { return }
    try { Stop-Process -Id $Holder.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    try { $Holder.process.WaitForExit(10000) | Out-Null } catch { }
}

function Stop-Holders {
    foreach ($holder in @($script:holders)) {
        Stop-TcpHolder -Holder $holder
    }
    $script:holders.Clear()
}

function Invoke-Installer {
    param([string]$Label, [hashtable]$Environment = @{}, [string]$ManifestPath = "")
    $beforeJournal = Get-JournalState
    $beforeBackup = Get-BackupGeneration
    $beforeInstalled = Get-Generation -RunPy $script:installedRunPy
    $installerEnvironment = @{
        GW_BUNDLE_MANIFEST = ""
        GW_PRODUCT_VERSION = ""
    }
    foreach ($key in $Environment.Keys) {
        $installerEnvironment[$key] = $Environment[$key]
    }
    $launch = Start-GwProcess -FilePath $script:installerPath -Arguments @("/S", ("/D=" + $script:installDirPath)) -Label $Label -Environment $installerEnvironment -WorkingDirectory $script:outputDirPath
    $exited = Wait-ProcessExit -Process $launch.process -TimeoutSeconds 900
    if (-not $exited) {
        try { Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $code = Get-ProcessExitCode -Process $launch.process
    $result = [pscustomobject]@{
        label = $Label
        exit_code = $code
        journal_before = $beforeJournal
        journal_after = (Get-JournalState)
        backup_generation_before = $beforeBackup
        backup_generation_after = (Get-BackupGeneration)
        installed_generation_before = $beforeInstalled
        installed_generation_after = (Get-Generation -RunPy $script:installedRunPy)
        manifest = $ManifestPath
        stdout = (Split-Path -Leaf $launch.stdout)
        stderr = (Split-Path -Leaf $launch.stderr)
    }
    $null = $script:installerResults.Add($result)
    return $result
}

function Get-DesktopDiagnosticsSnapshot {
    param([string]$Label, [int]$WaitSeconds = 20)
    $fileName = $Label + ".diagnostics.json"
    $path = Join-Path $script:outputDirPath $fileName
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    $launch = Start-DesktopProcess -Label ($Label + ".helper") -Arguments @("--diagnostics", $fileName) -WorkingDirectory $script:outputDirPath
    $exited = Wait-ProcessExit -Process $launch.process -TimeoutSeconds $WaitSeconds
    if (-not $exited) {
        try { Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $code = Get-ProcessExitCode -Process $launch.process
    $data = Read-JsonFile -Path $path
    return [pscustomobject]@{
        exit_code = $code
        file = $fileName
        data = $data
        stdout = $launch.stdout
        stderr = $launch.stderr
    }
}

function Invoke-DesktopQuit {
    param([string]$Label)
    $launch = Start-DesktopProcess -Label ($Label + ".quit") -Arguments @("--quit") -WorkingDirectory $script:outputDirPath
    $exited = Wait-ProcessExit -Process $launch.process -TimeoutSeconds 40
    if (-not $exited) {
        try { Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    return [pscustomobject]@{ exit_code = (Get-ProcessExitCode -Process $launch.process); exited = $exited; stdout = $launch.stdout; stderr = $launch.stderr }
}

function Start-DesktopPrimary {
    param([string]$Label)
    $launch = Start-DesktopProcess -Label $Label
    $window = Wait-DesktopWindow -Process $launch.process -TimeoutSeconds 90
    if ($null -eq $window) {
        if ($launch.process.HasExited) {
            $code = Get-ProcessExitCode -Process $launch.process
            throw ("desktop exited before showing a window (exit code {0})" -f $code)
        }
        throw "desktop did not show a window within 90 seconds"
    }
    $script:desktop = $launch.process
    $script:desktopLaunch = $launch
    return [pscustomobject]@{ launch = $launch; window = $window }
}

function Assert-DesktopReady {
    param([string]$Label)
    Add-Step "$Label.ready"
    $health = Wait-Health -TimeoutSeconds 180
    Require-Check "$Label.health" ([bool]($health.ok -and $health.status -eq 200)) ("health status " + [string]$health.status)
    $openapiVersion = Get-OpenApiVersion
    Require-Check "$Label.openapi-version" (Test-ExpectedOpenApiVersion $openapiVersion) ("expected prefix " + $script:expectedVersionValue + "+gw., got " + $openapiVersion)
    $manifest = Read-JsonFile -Path (Join-Path $script:installDirPath "backend\bundle-manifest.json")
    Require-Check "$Label.manifest-present" ($null -ne $manifest) "bundle manifest unreadable"
    Require-Check "$Label.manifest-version" ([string]$manifest.product_version -eq $script:expectedVersionValue) ("manifest version " + [string]$manifest.product_version)
    Require-Check "$Label.manifest-commit" (([string]$manifest.git_commit).ToLowerInvariant().StartsWith($script:expectedCommitValue)) ("manifest commit " + [string]$manifest.git_commit)
    $backendPids = @(Get-BackendPids)
    Require-Check "$Label.backend-single" ($backendPids.Count -eq 1) ("backend PIDs: " + ($backendPids -join ","))
    $listenerPids = @(Get-ListeningPids -Port 2077)
    Require-Check "$Label.port-owner" ((@($listenerPids) -contains $backendPids[0])) ("listener PIDs: " + ($listenerPids -join ",") + "; backend PIDs: " + ($backendPids -join ","))
    $lock = Get-InstanceLock
    Require-Check "$Label.lock-present" ($null -ne $lock) "instance lock missing"
    Require-Check "$Label.lock-owner" ($null -ne $lock -and [int]$lock.pid -eq [int]$script:desktop.Id) ("lock pid " + [string]$lock.pid + "; desktop pid " + [string]$script:desktop.Id)
    $diag = $null
    $view = $null
    $diagDeadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $diagDeadline) {
        $diag = Get-DesktopDiagnosticsSnapshot -Label ($Label + ".diagnostics") -WaitSeconds 30
        if ($null -ne $diag.data -and [bool]$diag.data.running -and $null -ne $diag.data.diagnostics) {
            $view = $diag.data.diagnostics
            if ($null -ne $view.handshake -and [bool]$view.handshake.verified -and [string]$view.backend_phase -eq "running") {
                break
            }
        }
        Start-Sleep -Seconds 1
    }
    Require-Check "$Label.diagnostics-exit" ($null -ne $diag -and $diag.exit_code -eq 0) ("diagnostics helper exit " + $(if ($null -ne $diag) { [string]$diag.exit_code } else { "<none>" }))
    Require-Check "$Label.diagnostics-running" ($null -ne $diag.data -and [bool]$diag.data.running) "diagnostics did not report a running instance"
    Require-Check "$Label.diagnostics-view" ($null -ne $view) "diagnostics payload missing"
    Require-Check "$Label.diagnostics-version" ([string]$view.desktop_version -eq $script:expectedVersionValue) ("desktop version " + [string]$view.desktop_version)
    Require-Check "$Label.diagnostics-backend" ([string]$view.backend_phase -eq "running") ("backend phase " + [string]$view.backend_phase)
    Require-Check "$Label.diagnostics-handshake" ($null -ne $view.handshake -and [bool]$view.handshake.verified) "capability handshake not verified"
    $healthJson = $null
    try { $healthJson = $health.body | ConvertFrom-Json } catch { }
    if ($null -ne $healthJson -and $healthJson.pid) {
        Require-Check "$Label.health-pid" ([int]$healthJson.pid -eq [int]$backendPids[0]) ("health pid " + [string]$healthJson.pid + "; backend PIDs " + ($backendPids -join ","))
    }
    $script:lifecycle[$Label] = [ordered]@{
        desktop_pid = [int]$script:desktop.Id
        backend_pids = $backendPids
        listener_pids = $listenerPids
        lock_pid = if ($null -ne $lock) { [int]$lock.pid } else { 0 }
        health_status = [int]$health.status
        openapi_version = $openapiVersion
        handshake = if ($null -ne $view -and $null -ne $view.handshake) { [bool]$view.handshake.verified } else { $false }
        trusted = if ($null -ne $view -and $null -ne $view.trusted) { [bool]$view.trusted.available } else { $false }
    }
}

function Wait-ForNoBackend {
    param([int]$TimeoutSeconds = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ((@(Get-BackendPids)).Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return ((@(Get-BackendPids)).Count -eq 0)
}

function Stop-DesktopGracefully {
    param([string]$Label)
    if ($null -eq $script:desktop) { return }
    $pidToStop = [int]$script:desktop.Id
    Add-Step "$Label.quit"
    $quit = Invoke-DesktopQuit -Label $Label
    Require-Check "$Label.quit-helper-exit" ($quit.exit_code -eq 0) ("quit helper exit " + [string]$quit.exit_code)
    $exited = Wait-ProcessExit -Process $script:desktop -TimeoutSeconds 30
    if (-not $exited) {
        try { Close-Window -Handle $script:desktop.MainWindowHandle | Out-Null } catch { }
        $exited = Wait-ProcessExit -Process $script:desktop -TimeoutSeconds 10
    }
    if (-not $exited) {
        try { Stop-Process -Id $script:desktop.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $desktopExited = $false
    try { $script:desktop.Refresh(); $desktopExited = [bool]$script:desktop.HasExited } catch { }
    Require-Check "$Label.desktop-exited" $desktopExited "desktop process still alive"
    $lockGone = Wait-ForNoLock -TimeoutSeconds 30
    Require-Check "$Label.lock-removed" $lockGone "instance lock still present"
    $backendGone = Wait-ForNoBackend -TimeoutSeconds 30
    Require-Check "$Label.backend-stopped" $backendGone "backend process survived quit"
    $portFree = Wait-ForPortFree -Port 2077 -TimeoutSeconds 30
    Require-Check "$Label.port-released" $portFree "port 2077 still listening"
    $backendLeft = @(Get-BackendPids)
    Require-Check "$Label.backend-stopped-after-wait" ($backendLeft.Count -eq 0) ("backend PIDs survived quit: " + ($backendLeft -join ","))
    $desktopLeft = @(Get-DesktopPids)
    Require-Check "$Label.desktop-single-exit" ($desktopLeft.Count -eq 0) ("desktop PIDs survived quit: " + ($desktopLeft -join ","))
    $script:lifecycle[$Label] = [ordered]@{
        helper_exit = $quit.exit_code
        desktop_pid = $pidToStop
        lock_removed = $lockGone
        port_released = $portFree
        backend_pids_after = $backendLeft
        desktop_pids_after = $desktopLeft
    }
    $script:desktop = $null
    $script:desktopLaunch = $null
}

function Wait-ForSingleDesktopWinner {
    param($First, $Second, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $firstAlive = -not $First.HasExited
        $secondAlive = -not $Second.HasExited
        if ($firstAlive -xor $secondAlive) {
            return $true
        }
        if ($firstAlive -and $secondAlive) {
            $firstWindow = 0
            $secondWindow = 0
            try { $first.Refresh(); $firstWindow = $First.MainWindowHandle.ToInt64() } catch { }
            try { $second.Refresh(); $secondWindow = $Second.MainWindowHandle.ToInt64() } catch { }
            if ($firstWindow -ne 0 -or $secondWindow -ne 0) {
                $health = Get-HttpResult -Url "http://127.0.0.1:2077/healthz"
                if ($health.ok) { return $true }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Save-Evidence {
    New-Item -ItemType Directory -Force -Path $script:outputDirPath | Out-Null
    $failed = @($script:checks | Where-Object { -not $_.ok })
    $result = "PASS"
    if ($failed.Count -gt 0 -or $script:fatal.Length -gt 0) { $result = "FAIL" }
    $checks = @()
    foreach ($check in @($script:checks)) {
        $checks += [ordered]@{
            name = $check.name
            ok = [bool]$check.ok
            detail = Format-SafeText $check.detail
        }
    }
    $steps = @()
    foreach ($step in @($script:steps)) {
        $steps += [ordered]@{ name = $step.name; detail = Format-SafeText $step.detail }
    }
    $installerResults = @()
    foreach ($item in @($script:installerResults)) {
        $installerResults += [ordered]@{
            label = $item.label
            exit_code = $item.exit_code
            journal_before = $item.journal_before
            journal_after = $item.journal_after
            backup_generation_before = $item.backup_generation_before
            backup_generation_after = $item.backup_generation_after
            installed_generation_before = $item.installed_generation_before
            installed_generation_after = $item.installed_generation_after
            stdout = $item.stdout
            stderr = $item.stderr
        }
    }
    $lifecycle = [ordered]@{}
    foreach ($key in $script:lifecycle.Keys) {
        $value = $script:lifecycle[$key]
        $copy = [ordered]@{}
        if ($value -is [System.Collections.IDictionary]) {
            foreach ($innerKey in $value.Keys) {
                $innerValue = $value[$innerKey]
                if ($innerValue -is [array]) {
                    $copy[$innerKey] = @($innerValue | ForEach-Object {
                        if ($_ -is [string]) { Format-SafeText $_ } else { $_ }
                    })
                } elseif ($innerValue -is [string]) {
                    $copy[$innerKey] = Format-SafeText $innerValue
                } else {
                    $copy[$innerKey] = $innerValue
                }
            }
        } else {
            $copy = $value
        }
        $lifecycle[$key] = $copy
    }
    $installerSize = 0
    $installerFile = "<unresolved>"
    if ($script:installerPath.Length -gt 0 -and (Test-Path -LiteralPath $script:installerPath)) {
        $installerFile = Split-Path -Leaf $script:installerPath
        $installerSize = (Get-Item -LiteralPath $script:installerPath).Length
    }
    $payload = [ordered]@{
        schema = "qin626.acceptance.v1"
        result = $result
        started_at = $script:startedAt.ToString("o")
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
        package = [ordered]@{
            file = $installerFile
            sha256 = $script:installerSha
            size = $installerSize
            expected_sha256 = $script:expectedSha256Value
            expected_version = $script:expectedVersionValue
            expected_commit = $script:expectedCommitValue
        }
        environment = [ordered]@{
            powershell = $PSVersionTable.PSVersion.ToString()
            os = [Environment]::OSVersion.VersionString
            computer = "<HOST>"
            user = "<USER>"
            data_mode = $script:dataMode
            local_app_data_override = "<PROCESS_LOCALAPPDATA>"
            install_dir = "<INSTALL_DIR>"
            journal = "<LOCALAPPDATA>\ai.godsworkbench.desktop\upgrade\journal.txt"
        }
        installer = $installerResults
        lifecycle = $lifecycle
        checks = $checks
        steps = $steps
        journal_final = (Get-JournalState)
        installed_generation = (Get-Generation -RunPy $script:installedRunPy)
        backup_generation = (Get-BackupGeneration)
        data_sentinel = (Test-Path -LiteralPath $script:sentinelPath)
        server_config_present = (Test-Path -LiteralPath $script:serverConfig)
        client_settings_present = (Test-Path -LiteralPath $script:clientConfig)
        self_check_tail = Format-SafeText (Get-SelfCheckTail)
        fatal = Format-SafeText $script:fatal
    }
    $json = $payload | ConvertTo-Json -Depth 12
    $jsonPath = Join-Path $script:outputDirPath "acceptance.json"
    Write-Utf8NoBom -Path $jsonPath -Text ($json + "`r`n")
    $md = New-Object System.Collections.ArrayList
    $null = $md.Add("# QIN-626 installer acceptance")
    $null = $md.Add("")
    $null = $md.Add("- Result: **$result**")
    $null = $md.Add("- Installer: " + $installerFile)
    $null = $md.Add("- SHA-256: " + $script:installerSha)
    $null = $md.Add("- Expected version: " + $script:expectedVersionValue)
    $null = $md.Add("- Expected commit: " + $script:expectedCommitValue)
    $null = $md.Add("- Started: " + $script:startedAt.ToString("o"))
    $null = $md.Add("- Finished: " + (Get-Date).ToUniversalTime().ToString("o"))
    $null = $md.Add("- Data mode: " + $script:dataMode)
    $null = $md.Add("- Journal final: " + (Get-JournalState))
    $null = $md.Add("- Installed generation: " + (Get-Generation -RunPy $script:installedRunPy))
    $null = $md.Add("- Backup generation: " + (Get-BackupGeneration))
    $null = $md.Add("")
    $null = $md.Add("## Installer transitions")
    foreach ($item in $installerResults) {
        $null = $md.Add(("- {0}: exit={1}, journal {2} -> {3}, backup {4} -> {5}, installed {6} -> {7}" -f $item.label, $item.exit_code, $item.journal_before, $item.journal_after, $item.backup_generation_before, $item.backup_generation_after, $item.installed_generation_before, $item.installed_generation_after))
    }
    $null = $md.Add("")
    $null = $md.Add("## Checks")
    foreach ($check in $checks) {
        $mark = "FAIL"
        if ($check.ok) { $mark = "PASS" }
        $null = $md.Add(("- [{0}] {1}: {2}" -f $mark, $check.name, $check.detail))
    }
    if ($script:fatal.Length -gt 0) {
        $null = $md.Add("")
        $null = $md.Add("## Fatal")
        $null = $md.Add((Format-SafeText $script:fatal))
    }
    $null = $md.Add("")
    $null = $md.Add("Evidence JSON: acceptance.json (paths are placeholders).")
    $mdPath = Join-Path $script:outputDirPath "acceptance.md"
    Write-Utf8NoBom -Path $mdPath -Text (($md -join "`r`n") + "`r`n")
    $script:result = $result
    return $result
}

function Remove-AcceptanceInstallDir {
    if (-not (Test-Path -LiteralPath $script:installDirPath)) { return }
    $safeName = Split-Path -Leaf $script:installDirPath
    if (-not $safeName.StartsWith("gw-qin626-accept-")) {
        Add-Check "cleanup-install-dir-guarded" $false ("refusing to remove non-acceptance directory " + $safeName)
        return
    }
    try {
        Remove-Item -LiteralPath $script:installDirPath -Recurse -Force -ErrorAction Stop
        Add-Check "cleanup-install-dir" (-not (Test-Path -LiteralPath $script:installDirPath)) "install directory still present"
    } catch {
        Add-Check "cleanup-install-dir" $false (Format-SafeText $_.Exception.Message)
    }
}

function Remove-AcceptanceState {
    $ok = $true
    $outputInsideOverride = $false
    if ($script:processLocalAppData) {
        $overridePrefix = $script:processLocalAppData.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        $outputInsideOverride = $script:outputDirPath.Equals($script:processLocalAppData, [StringComparison]::OrdinalIgnoreCase) -or $script:outputDirPath.StartsWith($overridePrefix, [StringComparison]::OrdinalIgnoreCase)
    }
    if ($script:overrideClaimed -and $script:dataMode -eq "isolated-localappdata-override" -and (Test-Path -LiteralPath $script:processLocalAppData)) {
        $overrideName = Split-Path -Leaf $script:processLocalAppData
        if (-not $overrideName.StartsWith("gw-qin626-accept-")) {
            Write-Host ("[WARN] refusing to remove non-acceptance LocalAppDataOverride " + $overrideName)
            $ok = $false
        } elseif ($outputInsideOverride) {
            Write-Host "[WARN] evidence output is inside the LocalAppDataOverride; preserving it"
        } else {
            try {
                Remove-Item -LiteralPath $script:processLocalAppData -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Host ("[WARN] could not remove isolated LocalAppDataOverride: " + (Format-SafeText $_.Exception.Message))
                $ok = $false
            }
        }
    }
    if ($script:stateClaimed -and (Test-Path -LiteralPath $script:markerPath)) {
        if (Test-Path -LiteralPath $script:upgradeRoot) {
            try {
                Remove-Item -LiteralPath $script:upgradeRoot -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Host ("[WARN] could not remove acceptance upgrade state: " + (Format-SafeText $_.Exception.Message))
                $ok = $false
            }
        }
        if (Test-Path -LiteralPath $script:clientConfigDir) {
            try {
                Remove-Item -LiteralPath $script:clientConfigDir -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Host ("[WARN] could not remove acceptance client config: " + (Format-SafeText $_.Exception.Message))
                $ok = $false
            }
        }
    }
    return $ok
}

function Main {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class GwNative {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int maxCount);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, System.Text.StringBuilder text, int maxCount);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
}
'@

    $script:powershellPath = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $script:powershellPath)) {
        $script:powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
    }

    if (-not $OutputDir) {
        $OutputDir = Join-Path $env:TEMP ("gw-qin626-accept-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    }
    $script:outputDirPath = [IO.Path]::GetFullPath($OutputDir)
    New-Item -ItemType Directory -Force -Path $script:outputDirPath | Out-Null

    if (-not $InstallDir) {
        $InstallDir = Join-Path $env:PUBLIC ("gw-qin626-accept-install-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    }
    $script:installDirPath = [IO.Path]::GetFullPath($InstallDir)
    $script:desktopExe = Join-Path $script:installDirPath "gods-workbench-desktop.exe"
    $script:installedRunPy = Join-Path $script:installDirPath "backend\run.py"
    $script:uninstaller = Join-Path $script:installDirPath "uninstall.exe"

    $script:realLocalAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA", "Process")
    if (-not $script:realLocalAppData) { $script:realLocalAppData = [Environment]::GetFolderPath("LocalApplicationData") }
    $script:realDataRoot = Join-Path $script:realLocalAppData "GodsWorkbench"
    $script:appData = [Environment]::GetFolderPath("ApplicationData")
    $script:clientConfigDir = Join-Path $script:appData "ai.godsworkbench.desktop"
    $script:clientConfig = Join-Path $script:clientConfigDir "client.json"
    $script:serverConfig = Join-Path $script:clientConfigDir "server.json"
    $script:upgradeRoot = Join-Path $script:realLocalAppData "ai.godsworkbench.desktop\upgrade"
    $script:journalPath = Join-Path $script:upgradeRoot "journal.txt"
    $script:backupProgram = Join-Path $script:upgradeRoot "backup-last\program"
    $script:lockPath = Join-Path ([IO.Path]::GetTempPath()) "ai.godsworkbench.desktop.instance.json"
    $script:markerPath = Join-Path $script:clientConfigDir "qin626-acceptance-owned.json"
    $script:processLocalAppData = $script:realLocalAppData
    $script:dataMode = "uninitialized"
    $script:installerPath = ""
    $script:installerSha = ""
    $script:expectedSha256Value = ""
    $script:expectedVersionValue = ""
    $script:expectedCommitValue = ""
    $script:sentinelPath = Join-Path $script:processLocalAppData "GodsWorkbench\acceptance-sentinel.json"

    $script:installerPath = (Resolve-Path -LiteralPath $Installer -ErrorAction Stop).Path
    $script:expectedSha256Value = $ExpectedSha256.Trim().ToLowerInvariant()
    $script:expectedVersionValue = $ExpectedVersion.Trim()
    $script:expectedCommitValue = $ExpectedCommit.Trim().ToLowerInvariant()
    Require-Check "install-dir.no-spaces" (-not ($script:installDirPath -match "\s")) "install directory must not contain spaces (NSIS /D)"
    if ($script:installDirPath -eq $script:realDataRoot -or $script:installDirPath.StartsWith($script:realDataRoot + "\") -or $script:realDataRoot.StartsWith($script:installDirPath + "\")) {
        throw "install directory overlaps the real production data root"
    }
    Require-Check "input.sha-format" ($script:expectedSha256Value -match "^[0-9a-f]{64}$") "SHA-256 must be 64 lowercase hexadecimal characters"
    Require-Check "input.version-format" ($script:expectedVersionValue -match "^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$") "version must be X.Y.Z or X.Y.Z-suffix"
    Require-Check "input.commit-format" ($script:expectedCommitValue -match "^[0-9a-f]{7,40}$") "commit must be 7-40 lowercase hexadecimal characters"
    $script:installerSha = Get-FileSha256 -Path $script:installerPath
    Require-Check "installer.sha256" ($script:installerSha -eq $script:expectedSha256Value) ("expected " + $script:expectedSha256Value + ", got " + $script:installerSha)

    if ($LocalAppDataOverride) {
        $script:processLocalAppData = [IO.Path]::GetFullPath($LocalAppDataOverride)
        $script:dataMode = "isolated-localappdata-override"
        if ($Reset -and (Test-Path -LiteralPath $script:processLocalAppData)) {
            $overrideName = Split-Path -Leaf $script:processLocalAppData
            if (-not $overrideName.StartsWith("gw-qin626-accept-")) {
                throw "refusing to reset a LocalAppDataOverride that is not named gw-qin626-accept-*"
            }
            Remove-Item -LiteralPath $script:processLocalAppData -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $script:processLocalAppData | Out-Null
        $bootstrap = Join-Path $script:processLocalAppData "GodsWorkbench"
        $existing = @(Get-ChildItem -LiteralPath $script:processLocalAppData -Force -ErrorAction SilentlyContinue)
        if ($existing.Count -gt 0 -and -not (Test-Path -LiteralPath $bootstrap)) {
            throw "LocalAppDataOverride is not empty and does not contain GodsWorkbench"
        }
        $dataRootForRun = Join-Path $script:processLocalAppData "GodsWorkbench"
        if (Test-Path -LiteralPath $dataRootForRun) {
            $dataItems = @(Get-ChildItem -LiteralPath $dataRootForRun -Force -ErrorAction SilentlyContinue)
            if ($dataItems.Count -gt 0 -and -not $Reset) {
                throw "isolated data root is not empty; use -Reset or a new -LocalAppDataOverride"
            }
        }
        $script:overrideClaimed = $true
    } else {
        $script:processLocalAppData = $script:realLocalAppData
        $script:dataMode = "standard-real-localappdata"
        if (Test-Path -LiteralPath $script:realDataRoot) {
            throw "real production data root already exists; use -LocalAppDataOverride on a shared machine or a fresh Windows user"
        }
    }
    $script:sentinelPath = Join-Path $script:processLocalAppData "GodsWorkbench\acceptance-sentinel.json"

    if (Test-Path -LiteralPath $script:lockPath) {
        $lock = Get-InstanceLock
        if ($null -ne $lock -and $lock.pid -and (Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue)) {
            throw "another desktop instance is running; close it before acceptance"
        }
        Remove-Item -LiteralPath $script:lockPath -Force -ErrorAction SilentlyContinue
    }

    $upgradeItems = @()
    if (Test-Path -LiteralPath $script:upgradeRoot) {
        $upgradeItems = @(Get-ChildItem -LiteralPath $script:upgradeRoot -Force -ErrorAction SilentlyContinue)
    }
    $clientItems = @()
    if (Test-Path -LiteralPath $script:clientConfigDir) {
        $clientItems = @(Get-ChildItem -LiteralPath $script:clientConfigDir -Force -ErrorAction SilentlyContinue)
    }
    if ($Reset) {
        if (($upgradeItems.Count -gt 0 -or $clientItems.Count -gt 0) -and -not (Test-Path -LiteralPath $script:markerPath)) {
            throw "desktop upgrade/client state exists without the acceptance marker; refusing to reset"
        }
        if (Test-Path -LiteralPath $script:upgradeRoot) {
            Remove-Item -LiteralPath $script:upgradeRoot -Recurse -Force
        }
        if ($clientItems.Count -gt 0) {
            Remove-Item -LiteralPath $script:clientConfigDir -Recurse -Force
        }
    } else {
        if ($upgradeItems.Count -gt 0 -or $clientItems.Count -gt 0) {
            throw "desktop upgrade/client state already exists; use -Reset (only if owned by this acceptance) or a fresh Windows user"
        }
    }
    New-Item -ItemType Directory -Force -Path $script:clientConfigDir | Out-Null
    $marker = [ordered]@{
        kind = "qin626-acceptance"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        installer_sha256 = $script:installerSha
    }
    Write-Utf8NoBom -Path $script:markerPath -Text (($marker | ConvertTo-Json -Compress) + "`r`n")
    $script:stateClaimed = $true

    if (Test-Path -LiteralPath $script:installDirPath) {
        $contents = @(Get-ChildItem -LiteralPath $script:installDirPath -Force -ErrorAction SilentlyContinue)
        if ($contents.Count -gt 0) {
            if (-not $Reset) {
                throw "install directory is not empty; use a new -InstallDir or -Reset"
            }
            $installName = Split-Path -Leaf $script:installDirPath
            if (-not $installName.StartsWith("gw-qin626-accept-")) {
                throw "refusing to reset a non-acceptance install directory"
            }
            Remove-Item -LiteralPath $script:installDirPath -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Force -Path $script:installDirPath | Out-Null
    $script:installDirClaimed = $true

    Add-Step "preflight"
    Add-Check "preflight.port-free" (-not (Test-PortOpen -Port 2077 -TimeoutMs 500)) "port 2077 is already listening"
    Add-Check "preflight.no-desktop" ((@(Get-DesktopPids)).Count -eq 0) ("desktop PIDs: " + ((@(Get-DesktopPids)) -join ","))
    Add-Check "preflight.no-backend" ((@(Get-BackendPids)).Count -eq 0) ("backend PIDs: " + ((@(Get-BackendPids)) -join ","))

    Add-Step "fresh-install"
    $fresh = Invoke-Installer -Label "01-fresh-install"
    Require-Check "fresh.installer-exit" ($fresh.exit_code -eq 0) ("installer exit " + [string]$fresh.exit_code)
    Require-Check "fresh.journal-verified" ($fresh.journal_after -eq "verified") ("journal " + $fresh.journal_after)
    Require-Check "fresh.desktop-exe" (Test-Path -LiteralPath $script:desktopExe) "desktop exe missing"
    Require-Check "fresh.backend-run" (Test-Path -LiteralPath $script:installedRunPy) "backend run.py missing"
    Require-Check "fresh.uninstaller" (Test-Path -LiteralPath $script:uninstaller) "uninstaller missing"
    Set-Generation -RunPy $script:installedRunPy -Generation "G0"

    Add-Step "first-run"
    $first = Start-DesktopPrimary -Label "02-first-run"
    Assert-DesktopReady -Label "02-first-run"
    $shot = Save-Screenshot -Path (Join-Path $script:outputDirPath "02-first-run.png")
    Add-Check "first-run.window-visible" ([GwNative]::IsWindowVisible($first.window.handle)) "main window not visible"
    Add-Check "first-run.screenshot" $shot "screenshot failed"
    New-Item -ItemType Directory -Force -Path (Join-Path $script:processLocalAppData "GodsWorkbench") | Out-Null
    Write-Utf8NoBom -Path $script:sentinelPath -Text ("{""kind"":""qin626-acceptance-sentinel"",""version"":""" + $script:expectedVersionValue + """}`r`n")
    Add-Check "first-run.data-sentinel" (Test-Path -LiteralPath $script:sentinelPath) "data sentinel not created"
    $serverConfigReady = Wait-ForFile -Path $script:serverConfig -TimeoutSeconds 20
    Add-Check "first-run.server-config" $serverConfigReady "server.json not written"
    $clientSettingsFixture = [ordered]@{ upload_source_dir = "<acceptance-upload-source>" } | ConvertTo-Json -Compress
    Write-Utf8NoBom -Path $script:clientConfig -Text ($clientSettingsFixture + "`r`n")
    Add-Check "first-run.client-settings-fixture" (Test-Path -LiteralPath $script:clientConfig) "client.json fixture not created"

    Add-Step "hot-start"
    $hot = Start-DesktopProcess -Label "03-hot-start"
    $hotExited = Wait-ProcessExit -Process $hot.process -TimeoutSeconds 30
    $hotCode = Get-ProcessExitCode -Process $hot.process
    if (-not $hotExited) {
        try { Stop-Process -Id $hot.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    Require-Check "hot-start.exit-zero" ($hotCode -eq 0) ("second instance exit " + [string]$hotCode)
    Start-Sleep -Seconds 2
    $desktopPids = @(Get-DesktopPids)
    Require-Check "hot-start.single-desktop" ($desktopPids.Count -eq 1) ("desktop PIDs " + ($desktopPids -join ","))
    $backendPids = @(Get-BackendPids)
    Require-Check "hot-start.single-backend" ($backendPids.Count -eq 1) ("backend PIDs " + ($backendPids -join ","))
    $listenerPids = @(Get-ListeningPids -Port 2077)
    Require-Check "hot-start.single-listener" ($listenerPids.Count -eq 1 -and $listenerPids[0] -eq $backendPids[0]) ("listener PIDs " + ($listenerPids -join ",") + "; backend PIDs " + ($backendPids -join ","))
    $lock = Get-InstanceLock
    Require-Check "hot-start.lock-unchanged" ($null -ne $lock -and [int]$lock.pid -eq [int]$script:desktop.Id) ("lock pid " + [string]$lock.pid)
    $script:lifecycle["03-hot-start"] = [ordered]@{
        helper_exit = $hotCode
        desktop_pids = $desktopPids
        backend_pids = $backendPids
        listener_pids = $listenerPids
        lock_pid = if ($null -ne $lock) { [int]$lock.pid } else { 0 }
    }

    Stop-DesktopGracefully -Label "04-clean-quit"

    Add-Step "cold-restart"
    $cold = Start-DesktopPrimary -Label "05-cold-restart"
    Assert-DesktopReady -Label "05-cold-restart"
    Stop-DesktopGracefully -Label "06-cold-quit"

    Add-Step "stale-lock-recovery"
    $stalePid = 999999
    $stale = New-InstanceLockJson -Port 2077 -Token "qin626-stale-token" -OwnerPid $stalePid -ExePath $script:desktopExe
    Write-Utf8NoBom -Path $script:lockPath -Text $stale
    $staleLaunch = Start-DesktopProcess -Label "07-stale-lock"
    $staleWindow = Wait-DesktopWindow -Process $staleLaunch.process -TimeoutSeconds 90
    Require-Check "stale-lock.primary-started" ($null -ne $staleWindow) "primary did not start from a stale lock"
    $script:desktop = $staleLaunch.process
    Assert-DesktopReady -Label "07-stale-lock"
    $staleLock = Get-InstanceLock
    Require-Check "stale-lock.replaced" ($null -ne $staleLock -and [int]$staleLock.pid -eq [int]$script:desktop.Id) "stale lock was not replaced"
    Stop-DesktopGracefully -Label "08-stale-quit"

    Add-Step "concurrent-stale-recovery"
    $concurrentStale = New-InstanceLockJson -Port 2077 -Token "qin626-concurrent-token" -OwnerPid 999998 -ExePath $script:desktopExe
    Write-Utf8NoBom -Path $script:lockPath -Text $concurrentStale
    $firstLaunch = Start-DesktopProcess -Label "09-concurrent-a"
    $secondLaunch = Start-DesktopProcess -Label "09-concurrent-b"
    $winnerKnown = Wait-ForSingleDesktopWinner -First $firstLaunch.process -Second $secondLaunch.process -TimeoutSeconds 120
    if (-not $winnerKnown) {
        $healthAny = Get-HttpResult -Url "http://127.0.0.1:2077/healthz"
        if ($healthAny.ok) { $winnerKnown = $true }
    }
    $firstAlive = -not $firstLaunch.process.HasExited
    $secondAlive = -not $secondLaunch.process.HasExited
    $winner = $null
    $loser = $null
    if ($firstAlive -and -not $secondAlive) {
        $winner = $firstLaunch.process
        $firstCode = Get-ProcessExitCode -Process $firstLaunch.process
        $secondCode = Get-ProcessExitCode -Process $secondLaunch.process
        Add-Check "concurrent-stale.exited-secondary" ($secondCode -eq 0) ("second exit " + [string]$secondCode)
    } elseif ($secondAlive -and -not $firstAlive) {
        $winner = $secondLaunch.process
        $firstCode = Get-ProcessExitCode -Process $firstLaunch.process
        $secondCode = Get-ProcessExitCode -Process $secondLaunch.process
        Add-Check "concurrent-stale.exited-secondary" ($firstCode -eq 0) ("first exit " + [string]$firstCode)
    } elseif ($firstAlive -and $secondAlive) {
        $lockNow = Get-InstanceLock
        if ($null -ne $lockNow -and [int]$lockNow.pid -eq [int]$firstLaunch.process.Id) {
            $winner = $firstLaunch.process
            $loser = $secondLaunch.process
        } elseif ($null -ne $lockNow -and [int]$lockNow.pid -eq [int]$secondLaunch.process.Id) {
            $winner = $secondLaunch.process
            $loser = $firstLaunch.process
        } else {
            $firstWindow = 0
            $secondWindow = 0
            try { $firstLaunch.process.Refresh(); $firstWindow = $firstLaunch.process.MainWindowHandle.ToInt64() } catch { }
            try { $secondLaunch.process.Refresh(); $secondWindow = $secondLaunch.process.MainWindowHandle.ToInt64() } catch { }
            if ($firstWindow -eq 0 -and $secondWindow -ne 0) {
                $winner = $secondLaunch.process
                $loser = $firstLaunch.process
            } elseif ($secondWindow -eq 0 -and $firstWindow -ne 0) {
                $winner = $firstLaunch.process
                $loser = $secondLaunch.process
            }
        }
    }
    Require-Check "concurrent-stale.one-primary" ($null -ne $winner) ("first alive=" + [string]$firstAlive + ", second alive=" + [string]$secondAlive + ", winner known=" + [string]$winnerKnown)
    $script:desktop = $winner
    if ($winner -is [System.Diagnostics.Process]) {
        if ([int]$winner.Id -eq [int]$firstLaunch.process.Id) {
            $script:desktopLaunch = $firstLaunch
        } else {
            $script:desktopLaunch = $secondLaunch
        }
    }
    if ($null -ne $loser) {
        $loserInfo = Wait-FatalDialog -Process $loser -TimeoutSeconds 5
        if ($null -ne $loserInfo) {
            $null = Close-Window -Handle $loserInfo.handle
        }
        $loserExited = Wait-ProcessExit -Process $loser -TimeoutSeconds 60
        if (-not $loserExited) {
            try { Stop-Process -Id $loser.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        $loserCode = Get-ProcessExitCode -Process $loser
        Add-Check "concurrent-stale.loser-exit" ($loserCode -eq 0) ("concurrent loser exit " + [string]$loserCode)
    }
    $concurrentHealth = Wait-Health -TimeoutSeconds 180
    Require-Check "concurrent-stale.health" ([bool]($concurrentHealth.ok -and $concurrentHealth.status -eq 200)) ("health status " + [string]$concurrentHealth.status)
    $desktopPids = @(Get-DesktopPids)
    Require-Check "concurrent-stale.one-desktop" ($desktopPids.Count -eq 1) ("desktop PIDs " + ($desktopPids -join ","))
    $backendPids = @(Get-BackendPids)
    Require-Check "concurrent-stale.one-backend" ($backendPids.Count -eq 1) ("backend PIDs " + ($backendPids -join ","))
    $listenerPids = @(Get-ListeningPids -Port 2077)
    Require-Check "concurrent-stale.one-listener" ($listenerPids.Count -eq 1 -and $listenerPids[0] -eq $backendPids[0]) ("listener PIDs " + ($listenerPids -join ",") + "; backend PIDs " + ($backendPids -join ","))
    $concurrentLock = Get-InstanceLock
    Require-Check "concurrent-stale.lock-owner" ($null -ne $concurrentLock -and [int]$concurrentLock.pid -eq [int]$script:desktop.Id) ("lock pid " + [string]$concurrentLock.pid + "; desktop pid " + [string]$script:desktop.Id)
    $script:lifecycle["09-concurrent-stale"] = [ordered]@{
        first_alive = $firstAlive
        second_alive = $secondAlive
        desktop_pids = $desktopPids
        backend_pids = $backendPids
        listener_pids = $listenerPids
        lock_pid = if ($null -ne $concurrentLock) { [int]$concurrentLock.pid } else { 0 }
    }
    Stop-DesktopGracefully -Label "10-concurrent-quit"

    Add-Step "live-unresponsive-lock"
    $holder = Start-TcpHolder -Port 0 -Label "11-live-holder"
    $holderPid = [int]$holder.process.Id
    $holderLock = New-InstanceLockJson -Port $holder.port -Token "qin626-live-token" -OwnerPid $holderPid -ExePath $script:powershellPath
    Write-Utf8NoBom -Path $script:lockPath -Text $holderLock
    $blockedLaunch = Start-DesktopProcess -Label "11-live-lock"
    $dialog = Wait-FatalDialog -Process $blockedLaunch.process -TimeoutSeconds 90
    Require-Check "live-lock.fatal-dialog" ($null -ne $dialog) "no fatal dialog appeared"
    if ($null -ne $dialog) {
        Add-Check "live-lock.dialog-title" ($dialog.title.Length -gt 0) ("dialog title: " + $dialog.title)
        Add-Check "live-lock.dialog-text" ($dialog.text.Length -gt 0) ("dialog text: " + $dialog.text)
        $null = Close-Window -Handle $dialog.handle
    }
    $blockedExited = Wait-ProcessExit -Process $blockedLaunch.process -TimeoutSeconds 30
    if (-not $blockedExited) {
        try { Stop-Process -Id $blockedLaunch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $blockedCode = Get-ProcessExitCode -Process $blockedLaunch.process
    Require-Check "live-lock.exit-one" ($blockedCode -eq 1) ("blocked desktop exit " + [string]$blockedCode)
    $lockAfterBlock = Get-InstanceLock
    Require-Check "live-lock.lock-preserved" ($null -ne $lockAfterBlock -and [int]$lockAfterBlock.pid -eq $holderPid) "lock was removed or replaced"
    Require-Check "live-lock.no-desktop" ((@(Get-DesktopPids)).Count -eq 0) ("desktop PIDs " + ((@(Get-DesktopPids)) -join ","))
    Require-Check "live-lock.no-backend" ((@(Get-BackendPids)).Count -eq 0) ("backend PIDs " + ((@(Get-BackendPids)) -join ","))
    Require-Check "live-lock.port-free" (-not (Test-PortOpen -Port 2077 -TimeoutMs 500)) "port 2077 unexpectedly open"
    Add-Check "live-lock.dialog-closed" $blockedExited "fatal dialog did not close"
    $script:lifecycle["11-live-lock"] = [ordered]@{
        holder_pid = $holderPid
        holder_port = $holder.port
        dialog_title = if ($null -ne $dialog) { $dialog.title } else { "" }
        dialog_text = if ($null -ne $dialog) { $dialog.text } else { "" }
        exit_code = $blockedCode
        lock_preserved = ($null -ne $lockAfterBlock -and [int]$lockAfterBlock.pid -eq $holderPid)
    }
    Stop-TcpHolder -Holder $holder
    Remove-Item -LiteralPath $script:lockPath -Force -ErrorAction SilentlyContinue

    Add-Step "port-2077-conflict"
    $portHolder = Start-TcpHolder -Port 2077 -Label "12-port-holder"
    $portOwnerBefore = @(Get-ListeningPids -Port 2077)
    Add-Check "port-conflict.holder" ($portOwnerBefore.Count -eq 1) ("pre-holder listeners " + ($portOwnerBefore -join ","))
    $conflictLaunch = Start-DesktopProcess -Label "12-port-conflict"
    $conflictWindow = Wait-DesktopWindow -Process $conflictLaunch.process -TimeoutSeconds 90
    Require-Check "port-conflict.window" ($null -ne $conflictWindow) "desktop window did not open"
    $script:desktop = $conflictLaunch.process
    $conflictDiag = $null
    $conflictDeadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $conflictDeadline) {
        $candidate = Get-DesktopDiagnosticsSnapshot -Label "12-port-conflict.diagnostics" -WaitSeconds 30
        if ($null -ne $candidate.data -and $null -ne $candidate.data.diagnostics -and [string]$candidate.data.diagnostics.last_error -match "2077") {
            $conflictDiag = $candidate
            break
        }
        Start-Sleep -Seconds 1
    }
    $conflictError = ""
    if ($null -ne $conflictDiag -and $null -ne $conflictDiag.data) {
        $conflictError = [string]$conflictDiag.data.diagnostics.last_error
    }
    Require-Check "port-conflict.last-error" ($conflictError -match "2077") ("desktop last_error " + $conflictError)
    $conflictShot = Save-Screenshot -Path (Join-Path $script:outputDirPath "12-port-conflict.png")
    Add-Check "port-conflict.screenshot" $conflictShot "screenshot failed"
    $backendDuringConflict = @(Get-BackendPids)
    Require-Check "port-conflict.no-backend" ($backendDuringConflict.Count -eq 0) ("backend PIDs " + ($backendDuringConflict -join ","))
    $ownerDuringConflict = @(Get-ListeningPids -Port 2077)
    Require-Check "port-conflict.owner-unchanged" ($ownerDuringConflict.Count -eq 1 -and $ownerDuringConflict[0] -eq [int]$portHolder.process.Id) ("listener PIDs " + ($ownerDuringConflict -join ",") + "; holder pid " + [string]$portHolder.process.Id)
    $conflictQuit = Invoke-DesktopQuit -Label "12-port-conflict"
    Require-Check "port-conflict.quit" ($conflictQuit.exit_code -eq 0) ("quit helper exit " + [string]$conflictQuit.exit_code)
    $conflictExited = Wait-ProcessExit -Process $conflictLaunch.process -TimeoutSeconds 30
    if (-not $conflictExited) {
        try { Stop-Process -Id $conflictLaunch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    Require-Check "port-conflict.desktop-exit" $conflictExited "desktop process did not exit"
    Require-Check "port-conflict.lock-removed" (Wait-ForNoLock -TimeoutSeconds 30) "instance lock still present"
    $script:desktop = $null
    $script:lifecycle["12-port-conflict"] = [ordered]@{
        error = $conflictError
        holder_pid = [int]$portHolder.process.Id
        backend_pids = $backendDuringConflict
        listener_pids = $ownerDuringConflict
        desktop_pid = [int]$conflictLaunch.process.Id
    }
    Stop-TcpHolder -Holder $portHolder
    Require-Check "port-conflict.port-freed" (Wait-ForPortFree -Port 2077 -TimeoutSeconds 30) "port 2077 did not free after holder stop"

    Add-Step "upgrade-rotation"
    Set-Generation -RunPy $script:installedRunPy -Generation "G0"
    $upgradeOne = Invoke-Installer -Label "13-upgrade-g1"
    Require-Check "upgrade-g1.exit" ($upgradeOne.exit_code -eq 0) ("installer exit " + [string]$upgradeOne.exit_code)
    Require-Check "upgrade-g1.journal" ($upgradeOne.journal_after -eq "verified") ("journal " + $upgradeOne.journal_after)
    Require-Check "upgrade-g1.backup" ($upgradeOne.backup_generation_after -eq "G0") ("backup generation " + $upgradeOne.backup_generation_after)
    Set-Generation -RunPy $script:installedRunPy -Generation "G1"
    $upgradeTwo = Invoke-Installer -Label "14-upgrade-g2"
    Require-Check "upgrade-g2.exit" ($upgradeTwo.exit_code -eq 0) ("installer exit " + [string]$upgradeTwo.exit_code)
    Require-Check "upgrade-g2.journal" ($upgradeTwo.journal_after -eq "verified") ("journal " + $upgradeTwo.journal_after)
    Require-Check "upgrade-g2.backup" ($upgradeTwo.backup_generation_after -eq "G1") ("backup generation " + $upgradeTwo.backup_generation_after)
    Set-Generation -RunPy $script:installedRunPy -Generation "G2"

    Add-Step "failed-upgrade-rollback"
    $badManifest = Join-Path $script:outputDirPath "invalid-bundle-manifest.json"
    Write-Utf8NoBom -Path $badManifest -Text "{ this is not valid json "
    $failedUpgrade = Invoke-Installer -Label "15-failed-upgrade" -Environment @{ GW_BUNDLE_MANIFEST = $badManifest }
    Require-Check "failed-upgrade.exit-two" ($failedUpgrade.exit_code -eq 2) ("installer exit " + [string]$failedUpgrade.exit_code)
    Require-Check "failed-upgrade.journal-rolled-back" ($failedUpgrade.journal_after -eq "rolled_back") ("journal " + $failedUpgrade.journal_after)
    Require-Check "failed-upgrade.installed-neighbor" ((Get-Generation -RunPy $script:installedRunPy) -eq "G2") ("installed generation " + (Get-Generation -RunPy $script:installedRunPy))
    Require-Check "failed-upgrade.backup-neighbor" ((Get-BackupGeneration) -eq "G2") ("backup generation " + (Get-BackupGeneration))
    Require-Check "failed-upgrade.exe-present" (Test-Path -LiteralPath $script:desktopExe) "desktop exe missing after rollback"
    Require-Check "failed-upgrade.server-config" (Test-Path -LiteralPath $script:serverConfig) "server config missing after rollback"
    Require-Check "failed-upgrade.client-settings" (Test-Path -LiteralPath $script:clientConfig) "client settings missing after rollback"
    Add-Check "failed-upgrade.self-check-log" ((Get-SelfCheckTail).Length -gt 0) "no self-check log captured"

    Add-Step "rollback-launch"
    $rollback = Start-DesktopPrimary -Label "16-rollback-launch"
    Assert-DesktopReady -Label "16-rollback-launch"
    $rollbackOpenApi = Get-OpenApiVersion
    Require-Check "rollback-launch.openapi" (Test-ExpectedOpenApiVersion $rollbackOpenApi) ("openapi version " + $rollbackOpenApi)
    Require-Check "rollback-launch.journal-unchanged" ((Get-JournalState) -eq "rolled_back") ("journal " + (Get-JournalState))
    Stop-DesktopGracefully -Label "17-rollback-quit"

    Add-Step "uninstall-preserves-data"
    Require-Check "uninstall.install-exists" (Test-Path -LiteralPath $script:uninstaller) "uninstaller missing"
    Require-Check "uninstall.sentinel-exists" (Test-Path -LiteralPath $script:sentinelPath) "data sentinel missing before uninstall"
    Require-Check "uninstall.server-config-exists" (Test-Path -LiteralPath $script:serverConfig) "server config missing before uninstall"
    Require-Check "uninstall.client-settings-exists" (Test-Path -LiteralPath $script:clientConfig) "client settings missing before uninstall"
    $backupBeforeUninstall = Get-BackupGeneration
    $installedBeforeUninstall = Get-Generation -RunPy $script:installedRunPy
    $uninstallLaunch = Start-GwProcess -FilePath $script:uninstaller -Arguments @("/S") -Label "18-uninstall" -Environment @{} -WorkingDirectory $script:outputDirPath
    $uninstallExited = Wait-ProcessExit -Process $uninstallLaunch.process -TimeoutSeconds 300
    if (-not $uninstallExited) {
        try { Stop-Process -Id $uninstallLaunch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $uninstallCode = Get-ProcessExitCode -Process $uninstallLaunch.process
    Require-Check "uninstall.exit" ($uninstallCode -eq 0) ("uninstaller exit " + [string]$uninstallCode)
    $installGone = $false
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path -LiteralPath $script:installDirPath)) { $installGone = $true; break }
        Start-Sleep -Milliseconds 500
    }
    Require-Check "uninstall.install-removed" $installGone "install directory still present after uninstall"
    Require-Check "uninstall.data-preserved" (Test-Path -LiteralPath $script:sentinelPath) "data root sentinel was removed"
    Require-Check "uninstall.server-config-preserved" (Test-Path -LiteralPath $script:serverConfig) "server config was removed"
    Require-Check "uninstall.client-settings-preserved" (Test-Path -LiteralPath $script:clientConfig) "client settings were removed"
    Require-Check "uninstall.backup-preserved" ((Get-BackupGeneration) -eq $backupBeforeUninstall) ("backup generation " + (Get-BackupGeneration) + " vs " + $backupBeforeUninstall)
    Require-Check "uninstall.port-free" (-not (Test-PortOpen -Port 2077 -TimeoutMs 500)) "port 2077 still open"
    Require-Check "uninstall.no-desktop" ((@(Get-DesktopPids)).Count -eq 0) ("desktop PIDs " + ((@(Get-DesktopPids)) -join ","))
    Require-Check "uninstall.no-backend" ((@(Get-BackendPids)).Count -eq 0) ("backend PIDs " + ((@(Get-BackendPids)) -join ","))
    $script:lifecycle["18-uninstall"] = [ordered]@{
        exit_code = $uninstallCode
        installed_generation_before = $installedBeforeUninstall
        install_removed = $installGone
        data_sentinel = (Test-Path -LiteralPath $script:sentinelPath)
        server_config = (Test-Path -LiteralPath $script:serverConfig)
        client_settings = (Test-Path -LiteralPath $script:clientConfig)
        backup_generation = (Get-BackupGeneration)
        port_free = (-not (Test-PortOpen -Port 2077 -TimeoutMs 500))
    }
    if ($KeepInstalled) {
        Add-Step "reinstate-for-inspection"
        $reinstate = Invoke-Installer -Label "19-reinstate"
        Add-Check "reinstate.exit" ($reinstate.exit_code -eq 0) ("installer exit " + [string]$reinstate.exit_code)
    }
}

$exitCode = 1
try {
    Main
    $failed = @($script:checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0 -and $script:fatal.Length -eq 0) {
        $exitCode = 0
    }
} catch {
    $script:fatal = $_.Exception.Message
    Write-Host ("[TRACE] " + $_.InvocationInfo.PositionMessage)
    Write-Host ("[TRACE] " + $_.ScriptStackTrace)
    Add-Check "unhandled-error" $false (Format-SafeText $script:fatal)
} finally {
    Stop-Holders
    if ($null -ne $script:desktop) {
        try {
            $quit = Invoke-DesktopQuit -Label "99-cleanup-quit"
            $null = Wait-ProcessExit -Process $script:desktop -TimeoutSeconds 20
        } catch {
        }
        if (-not $script:desktop.HasExited) {
            try { Stop-Process -Id $script:desktop.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
    foreach ($extraPid in @(Get-DesktopPids)) {
        try { Stop-Process -Id $extraPid -Force -ErrorAction SilentlyContinue } catch { }
    }
    foreach ($backendPid in @(Get-BackendPids)) {
        try { Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue } catch { }
    }
    if ($script:installDirClaimed -and (Test-Path -LiteralPath $script:installDirPath)) {
        if (-not $KeepInstalled) {
            Remove-AcceptanceInstallDir
        }
    }
    $cleanupStateOk = $true
    try {
        $final = Save-Evidence
        Write-Host ("[{0}] evidence: {1}" -f $final, (Join-Path $script:outputDirPath "acceptance.json"))
        if ($final -ne "PASS") { $exitCode = 1 }
    } catch {
        Write-Host ("[FAIL] evidence write: " + $_.Exception.Message)
        $exitCode = 1
    }
    if (-not $KeepState) {
        $cleanupStateOk = Remove-AcceptanceState
        $badManifest = Join-Path $script:outputDirPath "invalid-bundle-manifest.json"
        if (Test-Path -LiteralPath $badManifest) { Remove-Item -LiteralPath $badManifest -Force -ErrorAction SilentlyContinue }
    }
    if (-not $cleanupStateOk) { $exitCode = 1 }
}
exit $exitCode
