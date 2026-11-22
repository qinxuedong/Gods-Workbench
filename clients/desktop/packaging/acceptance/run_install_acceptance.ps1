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
$script:processLaunches = @{}
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
$script:lanAddress = ""
$script:remoteLocalAppData = ""
$script:remoteBackends = New-Object System.Collections.ArrayList
$script:remoteBackendPids = New-Object System.Collections.ArrayList
$script:remoteBackendEvidence = [ordered]@{}
$script:ui = [ordered]@{}

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
        @($script:remoteLocalAppData, "<REMOTE_LOCALAPPDATA>"),
        @($script:processLocalAppData, "<PROCESS_LOCALAPPDATA>"),
        @($script:realLocalAppData, "<LOCALAPPDATA>"),
        @($script:installDirPath, "<INSTALL_DIR>"),
        @([Environment]::GetFolderPath("ApplicationData"), "<APPDATA>"),
        @([Environment]::GetFolderPath("LocalApplicationData"), "<LOCALAPPDATA>"),
        @($env:USERPROFILE, "<USERPROFILE>"),
        @($env:TEMP, "<TEMP>"),
        @($script:lanAddress, "<LAN_IP>")
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

function Get-PropertyValue {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        foreach ($key in $Object.Keys) {
            if ([string]::Equals([string]$key, $Name, [StringComparison]::OrdinalIgnoreCase)) {
                return $Object[$key]
            }
        }
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
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

function Get-JournalStateWeight([string]$state) {
    switch ($state.Trim().ToLowerInvariant()) {
        "committed"   { return 6 }
        "rolled_back" { return 5 }
        "verified"    { return 4 }
        "installed"   { return 3 }
        "backed_up"   { return 2 }
        "installing"  { return 1 }
        "prepare"     { return 0 }
        default       { return -1 }
    }
}

function Sync-UpgradeState {
    if ($script:processLocalAppData -and ($script:processLocalAppData -ne $script:realLocalAppData)) {
        $realUpgrade = Join-Path $script:realLocalAppData "ai.godsworkbench.desktop\upgrade"
        $sandboxUpgrade = Join-Path $script:processLocalAppData "ai.godsworkbench.desktop\upgrade"
        New-Item -ItemType Directory -Force -Path $realUpgrade | Out-Null
        New-Item -ItemType Directory -Force -Path $sandboxUpgrade | Out-Null
        $realJournal = Join-Path $realUpgrade "journal.txt"
        $sandboxJournal = Join-Path $sandboxUpgrade "journal.txt"
        $realContent = if (Test-Path -LiteralPath $realJournal) { [IO.File]::ReadAllText($realJournal).Trim() } else { "" }
        $sandboxContent = if (Test-Path -LiteralPath $sandboxJournal) { [IO.File]::ReadAllText($sandboxJournal).Trim() } else { "" }
        $realWeight = Get-JournalStateWeight $realContent
        $sandboxWeight = Get-JournalStateWeight $sandboxContent
        $winningContent = ""
        if ($realWeight -gt $sandboxWeight) {
            $winningContent = $realContent
        } elseif ($sandboxWeight -gt $realWeight) {
            $winningContent = $sandboxContent
        } else {
            $realTime = if (Test-Path -LiteralPath $realJournal) { (Get-Item -LiteralPath $realJournal).LastWriteTimeUtc } else { [DateTime]::MinValue }
            $sandboxTime = if (Test-Path -LiteralPath $sandboxJournal) { (Get-Item -LiteralPath $sandboxJournal).LastWriteTimeUtc } else { [DateTime]::MinValue }
            $winningContent = if ($realTime -gt $sandboxTime) { $realContent } else { $sandboxContent }
        }
        if ($winningContent.Length -gt 0) {
            [IO.File]::WriteAllText($realJournal, $winningContent)
            [IO.File]::WriteAllText($sandboxJournal, $winningContent)
        }
        $realBackup = Join-Path $realUpgrade "backup-last"
        $sandboxBackup = Join-Path $sandboxUpgrade "backup-last"
        if (Test-Path -LiteralPath $realBackup) {
            if (Test-Path -LiteralPath $sandboxBackup) {
                Remove-Item -LiteralPath $sandboxBackup -Recurse -Force
            }
            Copy-Item -LiteralPath $realBackup -Destination $sandboxBackup -Recurse -Force
        } elseif (Test-Path -LiteralPath $sandboxBackup) {
            if (Test-Path -LiteralPath $realBackup) {
                Remove-Item -LiteralPath $realBackup -Recurse -Force
            }
            Copy-Item -LiteralPath $sandboxBackup -Destination $realBackup -Recurse -Force
        }
        foreach ($logFile in @(Get-ChildItem -LiteralPath $realUpgrade -Filter "self-check*.log" -File -ErrorAction SilentlyContinue)) {
            Copy-Item -LiteralPath $logFile.FullName -Destination (Join-Path $sandboxUpgrade $logFile.Name) -Force -ErrorAction SilentlyContinue
        }
        foreach ($logFile in @(Get-ChildItem -LiteralPath $sandboxUpgrade -Filter "self-check*.log" -File -ErrorAction SilentlyContinue)) {
            Copy-Item -LiteralPath $logFile.FullName -Destination (Join-Path $realUpgrade $logFile.Name) -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-JournalState {
    Sync-UpgradeState
    if (-not (Test-Path -LiteralPath $script:journalPath)) { return "<missing>" }
    return ([IO.File]::ReadAllText($script:journalPath)).Trim()
}

function Get-BackupGeneration {
    Sync-UpgradeState
    $backupRun = Join-Path $script:backupProgram "backend\run.py"
    return (Get-Generation -RunPy $backupRun)
}

function Get-SelfCheckTail {
    Sync-UpgradeState
    if (-not (Test-Path -LiteralPath $script:upgradeRoot)) { return "" }
    $files = @(Get-ChildItem -LiteralPath $script:upgradeRoot -Filter "self-check*.log" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    # 失败自检的 stdout 可能只有 UTF-8 BOM；必须跳过空文件继续读 stderr，不能只看最新一个。
    foreach ($file in $files) {
        $lines = @(Get-Content -LiteralPath $file.FullName -Tail 24 -ErrorAction SilentlyContinue)
        $text = ($lines -join "`n").Trim()
        if ($text.Length -gt 0) { return $text }
    }
    return ""
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
    # HttpWebRequest avoids the machine proxy that Invoke-WebRequest applies to
    # private LAN addresses; headers are required by the no-store fail-closed checks.
    $request = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        $request.AllowAutoRedirect = $false
        $response = $request.GetResponse()
        try {
            $stream = $response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $body = $reader.ReadToEnd()
            $reader.Close()
            $stream.Close()
            $headers = @{}
            foreach ($key in $response.Headers.AllKeys) { $headers[$key] = [string]$response.Headers[$key] }
            return [pscustomobject]@{ ok = $true; status = [int]$response.StatusCode; body = $body; headers = $headers }
        } finally {
            $response.Close()
        }
    } catch [System.Net.WebException] {
        $response = $_.Exception.Response
        if ($null -eq $response) {
            return [pscustomobject]@{ ok = $false; status = 0; body = [string]$_.Exception.Message; headers = @{} }
        }
        try {
            $headers = @{}
            foreach ($key in $response.Headers.AllKeys) { $headers[$key] = [string]$response.Headers[$key] }
            $body = ""
            try {
                $stream = $response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                $body = $reader.ReadToEnd()
                $reader.Close()
                $stream.Close()
            } catch {
            }
            $status = 0
            try { $status = [int]$response.StatusCode } catch { }
            return [pscustomobject]@{ ok = $false; status = $status; body = $body; headers = $headers }
        } finally {
            try { $response.Close() } catch { }
        }
    } catch {
        return [pscustomobject]@{ ok = $false; status = 0; body = [string]$_.Exception.Message; headers = @{} }
    }
}

function Test-NoStoreHeaders {
    param($Result)
    if ($null -eq $Result) { return $false }
    $cache = [string](Get-PropertyValue -Object $Result.headers -Name "Cache-Control")
    $pragma = [string](Get-PropertyValue -Object $Result.headers -Name "Pragma")
    $expires = [string](Get-PropertyValue -Object $Result.headers -Name "Expires")
    return ($cache -match "(?i)no-store") -and ($pragma -match "(?i)no-cache") -and ($expires -match "(?i)^0$")
}

function Wait-Health {
    param([int]$TimeoutSeconds = 150, [string]$BaseUrl = "http://127.0.0.1:2077")
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Get-HttpResult -Url ($BaseUrl + "/healthz")
        if ($last.ok -and $last.status -eq 200) { return $last }
        Start-Sleep -Milliseconds 1000
    }
    return $last
}

function Get-OpenApiVersion {
    param([string]$BaseUrl = "http://127.0.0.1:2077")
    $result = Get-HttpResult -Url ($BaseUrl + "/openapi.json")
    if (-not $result.ok) { return "" }
    try {
        return [string](($result.body | ConvertFrom-Json).info.version)
    } catch {
        return ""
    }
}

function Get-FreeTcpPort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return [int]$listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }
}

function Get-FreeAddressPort {
    param([string]$Address)
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse($Address), 0)
    $listener.Start()
    try {
        return [int]$listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }
}

function Get-PrivateLanAddress {
    $configs = @()
    try { $configs = @(Get-NetIPConfiguration -ErrorAction Stop) } catch { }
    foreach ($config in $configs) {
        if ($null -eq (Get-PropertyValue -Object $config -Name "IPv4DefaultGateway")) { continue }
        $addresses = @(Get-PropertyValue -Object $config -Name "IPv4Address")
        foreach ($address in $addresses) {
            $ip = [string](Get-PropertyValue -Object $address -Name "IPAddress")
            if ($ip -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') { return $ip }
        }
    }
    try {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop)
    } catch {
        return ""
    }
    foreach ($address in $addresses) {
        $ip = [string](Get-PropertyValue -Object $address -Name "IPAddress")
        if ($ip -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') { return $ip }
    }
    return ""
}

function Test-AddressPortOpen {
    param([string]$Address, [int]$Port, [int]$TimeoutMs = 800)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($Address, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-ForAddressPortFree {
    param([string]$Address, [int]$Port, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-AddressPortOpen -Address $Address -Port $Port -TimeoutMs 500)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function ConvertTo-SafeObject {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) { return (Format-SafeText $Value) }
    if ($Value -is [System.Collections.IDictionary]) {
        $copy = [ordered]@{}
        foreach ($key in $Value.Keys) { $copy[[string]$key] = ConvertTo-SafeObject -Value $Value[$key] }
        return $copy
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $copy = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) { $copy[$property.Name] = ConvertTo-SafeObject -Value $property.Value }
        return $copy
    }
    if ($Value -is [array]) {
        return @($Value | ForEach-Object { ConvertTo-SafeObject -Value $_ })
    }
    return $Value
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
        if ($script:remoteBackendPids -contains [int]$process.ProcessId) { continue }
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

function Get-CdpTargets {
    param([int]$Port)
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/json")
        $request.Proxy = $null
        $request.Timeout = 3000
        $request.ReadWriteTimeout = 3000
        $response = $request.GetResponse()
        try {
            $stream = $response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $raw = $reader.ReadToEnd()
            $reader.Close()
            $stream.Close()
        } finally {
            $response.Close()
        }
        $parsed = $raw | ConvertFrom-Json
        if ($parsed -is [System.Array]) { return $parsed }
        return @($parsed)
    } catch {
        return @()
    }
}

function Get-CdpPageTarget {
    param([int]$Port)
    $fallback = $null
    foreach ($target in @(Get-CdpTargets -Port $Port)) {
        $type = [string](Get-PropertyValue -Object $target -Name "type")
        $url = [string](Get-PropertyValue -Object $target -Name "url")
        if ($type -ne "page" -or $url -like "devtools://*") { continue }
        if ($url -like "http://tauri.localhost*") { return $target }
        if ($null -eq $fallback) { $fallback = $target }
    }
    return $fallback
}

function Wait-CdpTargetUrl {
    param([int]$Port, [string]$Prefix, [int]$TimeoutSeconds = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = ""
    while ((Get-Date) -lt $deadline) {
        $target = Get-CdpPageTarget -Port $Port
        if ($null -ne $target) {
            $last = [string](Get-PropertyValue -Object $target -Name "url")
            if ($last.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) { return $last }
        }
        Start-Sleep -Milliseconds 500
    }
    return ""
}

function New-CdpConnection {
    param([int]$Port, [int]$TimeoutSeconds = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $target = $null
    while ((Get-Date) -lt $deadline) {
        $target = Get-CdpPageTarget -Port $Port
        if ($null -ne $target) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $target) { throw ("CDP target not available on port " + $Port) }
    $socket = New-Object System.Net.WebSockets.ClientWebSocket
    $cts = New-Object System.Threading.CancellationTokenSource
    $cts.CancelAfter(10000)
    try {
        [void]$socket.ConnectAsync([Uri]([string](Get-PropertyValue -Object $target -Name "webSocketDebuggerUrl")), $cts.Token).GetAwaiter().GetResult()
    } catch {
        $socket.Dispose()
        throw ("CDP websocket connect failed: " + $_.Exception.Message)
    } finally {
        $cts.Dispose()
    }
    return [pscustomobject]@{
        socket = $socket
        next_id = 0
        port = $Port
        target_url = [string](Get-PropertyValue -Object $target -Name "url")
    }
}

function Receive-CdpMessage {
    param($Connection, [int]$TimeoutMs = 5000)
    $deadline = (Get-Date).AddMilliseconds([Math]::Max(1, $TimeoutMs))
    $buffer = New-Object byte[] 65536
    $memory = New-Object System.IO.MemoryStream
    try {
        while ((Get-Date) -lt $deadline) {
            $remaining = [int]($deadline - (Get-Date)).TotalMilliseconds
            if ($remaining -lt 1) { return $null }
            $cts = New-Object System.Threading.CancellationTokenSource
            $cts.CancelAfter($remaining)
            try {
                $segment = [System.ArraySegment[byte]]::new($buffer)
                $received = $Connection.socket.ReceiveAsync($segment, $cts.Token).GetAwaiter().GetResult()
            } catch {
                return $null
            } finally {
                $cts.Dispose()
            }
            if ($received.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) { return $null }
            $memory.Write($buffer, 0, $received.Count)
            if ($received.EndOfMessage) {
                $text = [System.Text.Encoding]::UTF8.GetString($memory.ToArray())
                $memory.SetLength(0)
                try { return ($text | ConvertFrom-Json) } catch { return $null }
            }
        }
        return $null
    } finally {
        $memory.Dispose()
    }
}

function Invoke-CdpCommand {
    param($Connection, [string]$Method, [hashtable]$Params = @{}, [int]$TimeoutMs = 5000)
    $Connection.next_id = [int]$Connection.next_id + 1
    $id = [int]$Connection.next_id
    $payloadJson = ([ordered]@{ id = $id; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 20)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payloadJson)
    $cts = New-Object System.Threading.CancellationTokenSource
    $cts.CancelAfter($TimeoutMs)
    try {
        $segment = [System.ArraySegment[byte]]::new($bytes)
        [void]$Connection.socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
    } catch {
        return $null
    } finally {
        $cts.Dispose()
    }
    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
    while ((Get-Date) -lt $deadline) {
        $remaining = [int]($deadline - (Get-Date)).TotalMilliseconds
        if ($remaining -lt 1) { return $null }
        $message = Receive-CdpMessage -Connection $Connection -TimeoutMs $remaining
        if ($null -eq $message) { return $null }
        if ([int](Get-PropertyValue -Object $message -Name "id") -eq $id) { return $message }
    }
    return $null
}

function Invoke-CdpExpression {
    param($Connection, [string]$Expression, [int]$TimeoutMs = 8000)
    $response = Invoke-CdpCommand -Connection $Connection -Method "Runtime.evaluate" -Params @{
        expression = $Expression
        returnByValue = $true
        awaitPromise = $true
    } -TimeoutMs $TimeoutMs
    if ($null -eq $response) { throw "CDP Runtime.evaluate timed out" }
    $result = Get-PropertyValue -Object $response -Name "result"
    $exception = Get-PropertyValue -Object $result -Name "exceptionDetails"
    if ($null -ne $exception) { throw ("CDP Runtime.evaluate failed: " + [string](Get-PropertyValue -Object $exception -Name "text")) }
    $remoteObject = Get-PropertyValue -Object $result -Name "result"
    return (Get-PropertyValue -Object $remoteObject -Name "value")
}

function Save-CdpScreenshot {
    param($Connection, [string]$Path)
    $response = Invoke-CdpCommand -Connection $Connection -Method "Page.captureScreenshot" -Params @{
        format = "png"
        fromSurface = $true
    } -TimeoutMs 15000
    if ($null -eq $response) { return $false }
    $result = Get-PropertyValue -Object $response -Name "result"
    $data = [string](Get-PropertyValue -Object $result -Name "data")
    if ([string]::IsNullOrEmpty($data)) { return $false }
    try {
        [IO.File]::WriteAllBytes($Path, [Convert]::FromBase64String($data))
        return $true
    } catch {
        return $false
    }
}

function Wait-CdpExpression {
    param($Connection, [string]$Expression, [int]$TimeoutSeconds = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $value = Invoke-CdpExpression -Connection $Connection -Expression $Expression -TimeoutMs 4000
            if ($value -eq $true) { return $true }
        } catch {
        }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Close-CdpConnection {
    param($Connection)
    if ($null -eq $Connection) { return }
    try { $Connection.socket.Dispose() } catch { }
}

function ConvertTo-NativeArgument {
    param([string]$Value)
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Complete-GwOutput {
    param($Launch)
    if ($null -eq $Launch -or $Launch.output_complete) { return }
    $Launch.output_complete = $true
    try {
        $stdoutText = ""
        $stderrText = ""
        if ($null -ne $Launch.stdout_task) { $stdoutText = $Launch.stdout_task.GetAwaiter().GetResult() }
        if ($null -ne $Launch.stderr_task) { $stderrText = $Launch.stderr_task.GetAwaiter().GetResult() }
        Write-Utf8NoBom -Path $Launch.stdout -Text $stdoutText
        Write-Utf8NoBom -Path $Launch.stderr -Text $stderrText
    } catch {
    }
}

function Get-ProcessExitCode {
    param($Process)
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) { return $null }
        $launch = $script:processLaunches[[int]$Process.Id]
        if ($null -ne $launch) { Complete-GwOutput -Launch $launch }
        $exitCode = $Process.ExitCode
        if ($null -eq $exitCode) { return $null }
        return [int]$exitCode
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
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FilePath
        if ($Arguments -and @($Arguments).Count -gt 0) {
            $startInfo.Arguments = (@($Arguments) | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join " "
        }
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $startInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
        if ($WorkingDirectory) { $startInfo.WorkingDirectory = $WorkingDirectory }
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { throw ("failed to start " + $FilePath) }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
    } finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
    $launch = [pscustomobject]@{
        process = $process
        stdout = $stdout
        stderr = $stderr
        stdout_task = $stdoutTask
        stderr_task = $stderrTask
        output_complete = $false
    }
    $script:processLaunches[[int]$process.Id] = $launch
    return $launch
}

function Start-DesktopProcess {
    param([string]$Label, [string[]]$Arguments = @(), [string]$WorkingDirectory = "", [int]$CdpPort = 0)
    if (-not $WorkingDirectory) { $WorkingDirectory = $script:installDirPath }
    $environment = @{
        LOCALAPPDATA = $script:processLocalAppData
        GW_BACKEND_ROOT = ""
        GW_ROOT_DIR = ""
        GW_PYTHON = ""
        GW_BUNDLE_MANIFEST = ""
        GW_PRODUCT_VERSION = ""
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = ""
        NO_PROXY = "*"
    }
    if ($CdpPort -gt 0) {
        $environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=$CdpPort --remote-allow-origins=*"
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
                return [pscustomobject]@{ handle = $handle; title = $(if ($title.Length -gt 0) { $title } else { "Gods Workbench" }) }
            }
            try {
                Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
                Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue
                $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$Process.Id)
                $topElement = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
                if ($null -ne $topElement) {
                    $uHandle = [IntPtr]$topElement.Current.NativeWindowHandle
                    if ($uHandle.ToInt64() -ne 0) {
                        $uTitle = [string]$topElement.Current.Name
                        return [pscustomobject]@{ handle = $uHandle; title = $(if ($uTitle.Length -gt 0) { $uTitle } else { "Gods Workbench" }) }
                    }
                }
            } catch { }
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
    $resolvedHandle = [IntPtr]::Zero
    try { $resolvedHandle = $Process.MainWindowHandle } catch { }
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
                if ($resolvedHandle.ToInt64() -eq 0) {
                    $h = [IntPtr]$element.Current.NativeWindowHandle
                    if ($h.ToInt64() -ne 0) { $resolvedHandle = $h }
                }
                $name = [string]$element.Current.Name
                if ($name.Length -gt 0) { $texts += $name }
            } catch {
            }
            if ($texts.Count -ge 24) { break }
        }
    } catch {
    }
    if ($texts.Count -eq 0 -and $title.Length -gt 0) { $texts = @($title) }
    $resolvedTitle = if ($title.Length -gt 0) { $title } elseif ($texts.Count -gt 0) { $texts[0] } else { "神工坊桌面端启动错误" }
    return [pscustomobject]@{
        title = $resolvedTitle
        text = (($texts | Select-Object -Unique) -join " | ")
        handle = $resolvedHandle
    }
}

function Wait-FatalDialog {
    param($Process, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $Process.Refresh()
            if ($Process.HasExited) { return $null }
            $hwnds = [GwNative]::GetProcessWindows([int]$Process.Id)
            if ($hwnds.Count -gt 0) {
                return (Get-WindowInfo -Process $Process)
            }
            if ($Process.MainWindowHandle.ToInt64() -ne 0) {
                return (Get-WindowInfo -Process $Process)
            }
            try {
                Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
                $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$Process.Id)
                $topElement = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
                if ($null -ne $topElement) {
                    return (Get-WindowInfo -Process $Process)
                }
            } catch { }
        } catch {
            return $null
        }
        Start-Sleep -Milliseconds 400
    }
    return $null
}

function Close-Window {
    param([IntPtr]$Handle, [int]$ProcessId = 0)
    try {
        if ($ProcessId -gt 0) {
            [GwNative]::DismissProcessDialog($ProcessId) | Out-Null
        }
        if ($Handle -and $Handle.ToInt64() -ne 0) {
            [GwNative]::PostMessage($Handle, 0x0111, [IntPtr]1, [IntPtr]::Zero) | Out-Null
            [GwNative]::PostMessage($Handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        }
        try {
            Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
            Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue
            if ($Handle -and $Handle.ToInt64() -ne 0) {
                $elem = [System.Windows.Automation.AutomationElement]::FromHandle($Handle)
                if ($null -ne $elem) {
                    $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
                    $btn = $elem.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
                    if ($null -ne $btn) {
                        $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                        if ($null -ne $inv) { $inv.Invoke() }
                    }
                }
            }
            if ($ProcessId -gt 0) {
                $condPid = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ProcessId)
                $root = [System.Windows.Automation.AutomationElement]::RootElement
                $elems = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condPid))
                foreach ($el in $elems) {
                    try {
                        $h = [IntPtr]$el.Current.NativeWindowHandle
                        if ($h.ToInt64() -ne 0) {
                            [GwNative]::PostMessage($h, 0x0111, [IntPtr]1, [IntPtr]::Zero) | Out-Null
                            [GwNative]::PostMessage($h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
                        }
                        if ($el.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) {
                            $inv = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                            if ($null -ne $inv) { $inv.Invoke() }
                        }
                    } catch { }
                }
            }
        } catch { }
        return $true
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
        if ($width -le 0 -or $height -le 0) {
            $width = 1920
            $height = 1080
        }
        $bitmap = New-Object System.Drawing.Bitmap($width, $height)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
        } catch {
            $graphics.Clear([System.Drawing.Color]::FromArgb(16, 18, 22))
            $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::Gold)
            $font = New-Object System.Drawing.Font("Arial", 16)
            $graphics.DrawString("Desktop screen locked or non-interactive session; screenshot placeholder", $font, $brush, [float]50, [float]50)
            $font.Dispose()
            $brush.Dispose()
        }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()
        return (Test-Path -LiteralPath $Path)
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
                $visibleDeadline = (Get-Date).AddSeconds(10)
                while ((Get-Date) -lt $visibleDeadline) {
                    if (@(Get-ListeningPids -Port $actual) -contains [int]$started.Id) { break }
                    Start-Sleep -Milliseconds 200
                }
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
    try {
        $Holder.process.Refresh()
        if (-not $Holder.process.HasExited) {
            Stop-Process -Id $Holder.process.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
    try { $Holder.process.WaitForExit(10000) | Out-Null } catch { }
    try { if ($script:holders.Contains($Holder)) { $script:holders.Remove($Holder) } } catch { }
}

function Stop-Holders {
    foreach ($holder in @($script:holders)) {
        Stop-TcpHolder -Holder $holder
    }
    $script:holders.Clear()
}

function Invoke-Installer {
    param([string]$Label, [hashtable]$Environment = @{}, [string]$ManifestPath = "")
    Sync-UpgradeState
    $beforeJournal = Get-JournalState
    $beforeBackup = Get-BackupGeneration
    $beforeInstalled = Get-Generation -RunPy $script:installedRunPy
    $installerEnvironment = @{
        LOCALAPPDATA = $script:processLocalAppData
        GW_BUNDLE_MANIFEST = ""
        GW_PRODUCT_VERSION = ""
    }
    foreach ($key in $Environment.Keys) {
        $installerEnvironment[$key] = $Environment[$key]
    }
    if ($script:processLocalAppData -and ($script:processLocalAppData -ne $script:realLocalAppData)) {
        $realJournal = Join-Path $script:realLocalAppData "ai.godsworkbench.desktop\upgrade\journal.txt"
        if (Test-Path -LiteralPath $script:journalPath) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $realJournal) | Out-Null
            [IO.File]::WriteAllText($realJournal, [IO.File]::ReadAllText($script:journalPath))
        }
    }
    $launch = Start-GwProcess -FilePath $script:installerPath -Arguments @("/S", ("/D=" + $script:installDirPath)) -Label $Label -Environment $installerEnvironment -WorkingDirectory $script:outputDirPath
    $exited = Wait-ProcessExit -Process $launch.process -TimeoutSeconds 900
    if (-not $exited) {
        try { Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    $code = Get-ProcessExitCode -Process $launch.process
    Sync-UpgradeState
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
    $code = 1
    $data = $null
    $lastLaunch = $null
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
        $launch = Start-DesktopProcess -Label ($Label + ".helper") -Arguments @("--diagnostics", $fileName) -WorkingDirectory $script:outputDirPath
        $lastLaunch = $launch
        $exited = Wait-ProcessExit -Process $launch.process -TimeoutSeconds $WaitSeconds
        if (-not $exited) {
            try { Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        $code = Get-ProcessExitCode -Process $launch.process
        $data = Read-JsonFile -Path $path
        if ($code -eq 0 -and $null -ne $data) { break }
        if ($attempt -eq 0) { Start-Sleep -Milliseconds 600 }
    }
    return [pscustomobject]@{
        exit_code = $code
        file = $fileName
        data = $data
        stdout = $(if ($null -ne $lastLaunch) { $lastLaunch.stdout } else { "" })
        stderr = $(if ($null -ne $lastLaunch) { $lastLaunch.stderr } else { "" })
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
    param([string]$Label, [int]$CdpPort = 0)
    $launch = Start-DesktopProcess -Label $Label -CdpPort $CdpPort
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
    $preShot = Save-Screenshot -Path (Join-Path $script:outputDirPath ($Label + ".window.png"))
    Add-Check "$Label.window-screenshot" $preShot "screenshot failed before health check"
    $preDiag = Get-DesktopDiagnosticsSnapshot -Label ($Label + ".pre-health") -WaitSeconds 30
    if ($null -ne $preDiag -and $null -ne $preDiag.data) {
        $preDiagPath = Join-Path $script:outputDirPath ($Label + ".pre-health.json")
        Write-Utf8NoBom -Path $preDiagPath -Text (($preDiag.data | ConvertTo-Json -Depth 16) + "`r`n")
    }
    Add-Check "$Label.pre-health-diagnostics" ($null -ne $preDiag -and $preDiag.exit_code -eq 0) ("diagnostics helper exit " + $(if ($null -ne $preDiag) { [string]$preDiag.exit_code } else { "<none>" }))
    $health = Wait-Health -TimeoutSeconds 240
    if (-not ($health.ok -and $health.status -eq 200)) {
        $failShot = Save-Screenshot -Path (Join-Path $script:outputDirPath ($Label + ".health-failure.png"))
        Add-Check "$Label.health-failure-screenshot" $failShot "screenshot failed after health timeout"
        $failDiag = Get-DesktopDiagnosticsSnapshot -Label ($Label + ".health-failure") -WaitSeconds 30
        if ($null -ne $failDiag -and $null -ne $failDiag.data) {
            $failDiagPath = Join-Path $script:outputDirPath ($Label + ".health-failure.json")
            Write-Utf8NoBom -Path $failDiagPath -Text (($failDiag.data | ConvertTo-Json -Depth 16) + "`r`n")
        }
        $failBackend = @(Get-BackendPids)
        $failListeners = @(Get-ListeningPids -Port 2077)
        $failExit = Get-ProcessExitCode -Process $script:desktop
        Add-Check "$Label.health-failure-diagnostics" ($null -ne $failDiag -and $failDiag.exit_code -eq 0) ("diagnostics helper exit " + $(if ($null -ne $failDiag) { [string]$failDiag.exit_code } else { "<none>" }))
        Add-Check "$Label.health-failure-desktop-exit" ($null -ne $failExit) ("desktop exit " + $(if ($null -ne $failExit) { [string]$failExit } else { "<running>" }))
        Add-Check "$Label.health-failure-backend-count" ($failBackend.Count -gt 0) ("backend PIDs " + ($failBackend -join ","))
        Add-Check "$Label.health-failure-listener-count" ($failListeners.Count -gt 0) ("listener PIDs " + ($failListeners -join ","))
        $script:lifecycle[$Label + ".health-failure"] = [ordered]@{
            health_status = [int]$health.status
            health_body = [string]$health.body
            desktop_exit = $failExit
            backend_pids = $failBackend
            listener_pids = $failListeners
            diagnostics = if ($null -ne $failDiag) { $failDiag.data } else { $null }
        }
    }
    Require-Check "$Label.health" ([bool]($health.ok -and $health.status -eq 200)) ("health status " + [string]$health.status)
    $healthCache = [string](Get-PropertyValue -Object $health.headers -Name "Cache-Control")
    $healthPragma = [string](Get-PropertyValue -Object $health.headers -Name "Pragma")
    Require-Check "$Label.health-no-store" (Test-NoStoreHeaders -Result $health) ("Cache-Control=" + $healthCache + "; Pragma=" + $healthPragma)
    $openapiResult = Get-HttpResult -Url "http://127.0.0.1:2077/openapi.json"
    $openapiVersion = Get-OpenApiVersion
    Require-Check "$Label.openapi-version" (Test-ExpectedOpenApiVersion $openapiVersion) ("expected prefix " + $script:expectedVersionValue + "+gw., got " + $openapiVersion)
    $openapiCache = [string](Get-PropertyValue -Object $openapiResult.headers -Name "Cache-Control")
    Require-Check "$Label.openapi-no-store" (Test-NoStoreHeaders -Result $openapiResult) ("Cache-Control=" + $openapiCache)
    $manifest = Read-JsonFile -Path (Join-Path $script:installDirPath "backend\bundle-manifest.json")
    $manifestVersion = Get-PropertyValue -Object $manifest -Name "product_version"
    $manifestCommit = Get-PropertyValue -Object $manifest -Name "git_commit"
    Require-Check "$Label.manifest-present" ($null -ne $manifest) "bundle manifest unreadable"
    Require-Check "$Label.manifest-version" ([string]$manifestVersion -eq $script:expectedVersionValue) ("manifest version " + [string]$manifestVersion)
    Require-Check "$Label.manifest-commit" (([string]$manifestCommit).ToLowerInvariant().StartsWith($script:expectedCommitValue)) ("manifest commit " + [string]$manifestCommit)
    $backendPids = @(Get-BackendPids)
    Require-Check "$Label.backend-single" ($backendPids.Count -eq 1) ("backend PIDs: " + ($backendPids -join ","))
    $listenerPids = @(Get-ListeningPids -Port 2077)
    Require-Check "$Label.port-owner" ((@($listenerPids) -contains $backendPids[0])) ("listener PIDs: " + ($listenerPids -join ",") + "; backend PIDs: " + ($backendPids -join ","))
    $lock = Get-InstanceLock
    $lockPid = Get-PropertyValue -Object $lock -Name "pid"
    Require-Check "$Label.lock-present" ($null -ne $lock) "instance lock missing"
    Require-Check "$Label.lock-owner" ($null -ne $lock -and [int]$lockPid -eq [int]$script:desktop.Id) ("lock pid " + [string]$lockPid + "; desktop pid " + [string]$script:desktop.Id)
    $diag = $null
    $view = $null
    $diagDeadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $diagDeadline) {
        $diag = Get-DesktopDiagnosticsSnapshot -Label ($Label + ".diagnostics") -WaitSeconds 30
        $diagData = Get-PropertyValue -Object $diag -Name "data"
        if ($null -ne $diagData -and [bool](Get-PropertyValue -Object $diagData -Name "running")) {
            $view = Get-PropertyValue -Object $diagData -Name "diagnostics"
            $loopHandshake = Get-PropertyValue -Object $view -Name "handshake"
            if ($null -ne $loopHandshake -and [bool](Get-PropertyValue -Object $loopHandshake -Name "verified") -and [string](Get-PropertyValue -Object $view -Name "backend_phase") -eq "running") {
                break
            }
        }
        Start-Sleep -Seconds 1
    }
    $diagData = Get-PropertyValue -Object $diag -Name "data"
    $viewDesktopVersion = Get-PropertyValue -Object $view -Name "desktop_version"
    $viewBackendPhase = Get-PropertyValue -Object $view -Name "backend_phase"
    $viewHandshake = Get-PropertyValue -Object $view -Name "handshake"
    $viewTrusted = Get-PropertyValue -Object $view -Name "trusted"
    Require-Check "$Label.diagnostics-exit" ($null -ne $diag -and $diag.exit_code -eq 0) ("diagnostics helper exit " + $(if ($null -ne $diag) { [string]$diag.exit_code } else { "<none>" }))
    Require-Check "$Label.diagnostics-running" ($null -ne $diagData -and [bool](Get-PropertyValue -Object $diagData -Name "running")) "diagnostics did not report a running instance"
    Require-Check "$Label.diagnostics-view" ($null -ne $view) "diagnostics payload missing"
    Require-Check "$Label.diagnostics-version" ([string]$viewDesktopVersion -eq $script:expectedVersionValue) ("desktop version " + [string]$viewDesktopVersion)
    Require-Check "$Label.diagnostics-backend" ([string]$viewBackendPhase -eq "running") ("backend phase " + [string]$viewBackendPhase)
    Require-Check "$Label.diagnostics-handshake" ($null -ne $viewHandshake -and [bool](Get-PropertyValue -Object $viewHandshake -Name "verified")) "capability handshake not verified"
    $healthJson = $null
    try { $healthJson = $health.body | ConvertFrom-Json } catch { }
    $healthPid = Get-PropertyValue -Object $healthJson -Name "pid"
    if ($null -ne $healthPid) {
        Require-Check "$Label.health-pid" ([int]$healthPid -eq [int]$backendPids[0]) ("health pid " + [string]$healthPid + "; backend PIDs " + ($backendPids -join ","))
    }
    $script:lifecycle[$Label] = [ordered]@{
        desktop_pid = [int]$script:desktop.Id
        backend_pids = $backendPids
        listener_pids = $listenerPids
        lock_pid = if ($null -ne $lock) { [int]$lockPid } else { 0 }
        health_status = [int]$health.status
        health_pid = $healthPid
        health_cache_control = $healthCache
        health_pragma = $healthPragma
        openapi_version = $openapiVersion
        openapi_cache_control = $openapiCache
        handshake = if ($null -ne $viewHandshake) { [bool](Get-PropertyValue -Object $viewHandshake -Name "verified") } else { $false }
        trusted = if ($null -ne $viewTrusted) { [bool](Get-PropertyValue -Object $viewTrusted -Name "available") } else { $false }
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
    $primaryExit = Get-ProcessExitCode -Process $script:desktop
    $script:lifecycle[$Label] = [ordered]@{
        helper_exit = $quit.exit_code
        desktop_pid = $pidToStop
        desktop_exit = $primaryExit
        lock_removed = $lockGone
        port_released = $portFree
        backend_pids_after = $backendLeft
        desktop_pids_after = $desktopLeft
    }
    $script:desktop = $null
    $script:desktopLaunch = $null
    Sync-UpgradeState
}

function Wait-ShellFailure {
    param($Cdp, [int]$TimeoutSeconds = 120)
    $expression = @"
(() => {
  const error = document.getElementById('local-error');
  const title = document.getElementById('local-error-title');
  const retry = document.getElementById('local-retry');
  return !!(error && !error.hidden && title && title.textContent.trim().length > 0 && retry && !retry.disabled);
})()
"@
    return (Wait-CdpExpression -Connection $Cdp -Expression $expression -TimeoutSeconds $TimeoutSeconds)
}

function Get-ShellFailureSnapshot {
    param($Cdp)
    return (Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const error = document.getElementById('local-error');
  const home = document.getElementById('home-view');
  const remote = document.getElementById('remote-view');
  const retry = document.getElementById('local-retry');
  return {
    title: document.getElementById('local-error-title').textContent.trim(),
    reason: document.getElementById('local-error-reason').textContent.trim(),
    next: document.getElementById('local-error-next').textContent.trim(),
    error_visible: !!(error && !error.hidden),
    retry_visible: !!(retry && !retry.disabled && retry.offsetParent !== null),
    home_visible: !!(home && !home.hidden),
    remote_visible: !!(remote && !remote.hidden),
    remote_entry_present: !!document.getElementById('remote-entry')
  };
})()
"@)
}

function Set-ShellTheme {
    param($Cdp, [string]$Theme)
    return (Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const current = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  if (current !== '$Theme') { document.getElementById('theme-toggle').click(); }
  const style = getComputedStyle(document.documentElement);
  return {
    theme: document.documentElement.dataset.theme,
    surface: style.getPropertyValue('--gw-surface-bg').trim(),
    surface_card: style.getPropertyValue('--gw-surface-card').trim(),
    text_main: style.getPropertyValue('--gw-text-main').trim(),
    gold: style.getPropertyValue('--gold-primary').trim(),
    stored: localStorage.getItem('gw.desktop.theme') || ''
  };
})()
"@)
}

function Measure-ShellLayout {
    param($Cdp, [int]$Width)
    $null = Invoke-CdpCommand -Connection $Cdp -Method "Emulation.setDeviceMetricsOverride" -Params @{
        width = $Width
        height = 900
        deviceScaleFactor = 1
        mobile = $false
    } -TimeoutMs 5000
    Start-Sleep -Milliseconds 350
    try {
        $measurement = Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const steps = document.querySelector('.steps');
  const card = document.querySelector('.launch-card');
  const style = steps ? getComputedStyle(steps) : null;
  const columns = style ? style.gridTemplateColumns.trim().split(/\s+/).filter(Boolean).length : 0;
  const rect = card ? card.getBoundingClientRect() : { width: 0 };
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    columns: columns,
    grid: style ? style.gridTemplateColumns : '',
    card_width: Math.round(rect.width),
    body_font: getComputedStyle(document.body).fontSize,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1
  };
})()
"@
    } finally {
        $null = Invoke-CdpCommand -Connection $Cdp -Method "Emulation.clearDeviceMetricsOverride" -TimeoutMs 5000
    }
    if ($null -eq $measurement) { throw ("layout measurement failed at width " + $Width) }
    return $measurement
}

function Open-ShellRemoteView {
    param($Cdp)
    return (Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  document.getElementById('remote-entry').click();
  const remote = document.getElementById('remote-view');
  const home = document.getElementById('home-view');
  return { remote_visible: !!(remote && !remote.hidden), home_visible: !!(home && !home.hidden) };
})()
"@)
}

function Submit-ShellRemoteUrl {
    param($Cdp, [string]$Url)
    $escaped = $Url.Replace("\", "\\").Replace("'", "\'")
    return (Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const input = document.getElementById('remote-url');
  input.value = '$escaped';
  document.getElementById('remote-submit').click();
  return input.value;
})()
"@)
}

function Wait-ShellRemoteError {
    param($Cdp, [int]$TimeoutSeconds = 120)
    return (Wait-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const error = document.getElementById('remote-error');
  const title = document.getElementById('remote-error-title');
  return !!(error && !error.hidden && title && title.textContent.trim().length > 0);
})()
"@ -TimeoutSeconds $TimeoutSeconds)
}

function Get-ShellRemoteErrorSnapshot {
    param($Cdp)
    return (Invoke-CdpExpression -Connection $Cdp -Expression @"
(() => {
  const error = document.getElementById('remote-error');
  const retry = document.getElementById('remote-retry');
  return {
    title: document.getElementById('remote-error-title').textContent.trim(),
    reason: document.getElementById('remote-error-reason').textContent.trim(),
    next: document.getElementById('remote-error-next').textContent.trim(),
    error_visible: !!(error && !error.hidden),
    retry_visible: !!(retry && !retry.disabled && retry.offsetParent !== null)
  };
})()
"@)
}

function Start-FakeRemoteServer {
    param([string]$Address, [int]$Port, [string]$Label, [string]$ModePath)
    $ready = Join-Path $script:outputDirPath ($Label + ".port.txt")
    $fakeScript = Join-Path $script:outputDirPath ($Label + ".server.ps1")
    $code = @"
`$listener = New-Object System.Net.Sockets.TcpListener -ArgumentList ([System.Net.IPAddress]::Parse('$Address')), $Port
`$listener.Start()
[IO.File]::WriteAllText('$ready', [string]$Port)
while (`$true) {
    try {
        `$client = `$listener.AcceptTcpClient()
        `$stream = `$client.GetStream()
        `$reader = New-Object System.IO.StreamReader(`$stream, [System.Text.Encoding]::ASCII)
        `$requestLine = `$reader.ReadLine()
        while (`$true) { `$line = `$reader.ReadLine(); if (`$null -eq `$line -or `$line -eq '') { break } }
        `$path = '/'
        if (`$requestLine -match '^[A-Z]+ ([^ ?]+)') { `$path = `$Matches[1] }
        `$mode = 'no-store'
        try { `$mode = ([IO.File]::ReadAllText('$ModePath')).Trim() } catch { }
        `$status = '200 OK'
        `$extraHeaders = ''
        `$body = '{}'
        if (`$path -eq '/healthz') {
            `$body = '{"status":"ok","mode":"cleanroom","auth_mode":"local","frozen_contracts":true,"oidc_ready":false,"release_authorized":true}'
        } elseif (`$path -eq '/openapi.json') {
            `$body = '{"info":{"title":"Gods-Workbench Cleanroom API","version":"0.1.0"},"paths":{"/api/asset-auth/status":{"get":{}},"/api/asset-auth/local/login":{"post":{}},"/api/asset-auth/local/setup":{"post":{}},"/api/asset-registry/projects":{"get":{},"post":{}},"/api/asset-registry/projects/{project_id}":{"patch":{}},"/api/canvases":{"get":{},"post":{}},"/api/canvases/{canvas_id}":{"patch":{}},"/healthz":{"get":{}}}}'
        } elseif (`$path -eq '/api/asset-auth/status') {
            `$body = '{"auth_mode":"local","authenticated":false,"login_available":true,"logout_available":false,"oidc_ready":false,"reason":"","release_authorized":true}'
        } elseif (`$path -eq '/api/asset-registry/projects') {
            `$status = '401 Unauthorized'
            `$body = '{"detail":{"code":"UNAUTHORIZED","message":"unauthenticated"}}'
        }
        if (`$mode -eq 'no-version') {
            `$extraHeaders = "Cache-Control: no-store, no-cache, must-revalidate`r`nPragma: no-cache`r`nExpires: 0`r`n"
        }
        `$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes(`$body)
        `$head = "HTTP/1.1 `$status`r`nContent-Type: application/json; charset=utf-8`r`nContent-Length: " + `$bodyBytes.Length + "`r`n" + `$extraHeaders + "Connection: close`r`n`r`n"
        `$headBytes = [System.Text.Encoding]::ASCII.GetBytes(`$head)
        `$stream.Write(`$headBytes, 0, `$headBytes.Length)
        `$stream.Write(`$bodyBytes, 0, `$bodyBytes.Length)
        `$stream.Flush()
        `$client.Close()
    } catch {
        Start-Sleep -Milliseconds 200
    }
}
"@
    Write-Utf8NoBom -Path $fakeScript -Text $code
    $started = Start-Process -FilePath $script:powershellPath -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ('"' + $fakeScript + '"')) -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $ready) {
            $holder = [pscustomobject]@{ process = $started; port = $Port; script = $fakeScript; ready = $ready }
            $null = $script:holders.Add($holder)
            return $holder
        }
        if ($started.HasExited) { break }
        Start-Sleep -Milliseconds 300
    }
    try { Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue } catch { }
    throw ("fake remote server failed to bind " + $Address + ":" + $Port)
}

function Set-FakeRemoteMode {
    param([string]$ModePath, [string]$Mode)
    Write-Utf8NoBom -Path $ModePath -Text ($Mode + "`r`n")
}

function Start-RemoteBackend {
    param([string]$Label, [string]$Address, [int]$Port)
    $python = Join-Path $script:installDirPath "backend\python\python.exe"
    Require-Check "remote-backend.python" (Test-Path -LiteralPath $python) "bundled python missing"
    $environment = @{
        LOCALAPPDATA = $script:remoteLocalAppData
        GW_BACKEND_ROOT = ""
        GW_ROOT_DIR = ""
        GW_PYTHON = ""
        GW_BUNDLE_MANIFEST = ""
        GW_PRODUCT_VERSION = ""
        GW_HOST = ""
        GW_ALLOWED_HOSTS = $Address
        PYTHONDONTWRITEBYTECODE = "1"
        GW_RUNTIME_MODE = "prod"
    }
    $launch = Start-GwProcess -FilePath $python -Arguments @($script:installedRunPy, "--prod", "--host", $Address, "--port", [string]$Port) -Label $Label -Environment $environment -WorkingDirectory (Join-Path $script:installDirPath "backend")
    $null = $script:remoteBackends.Add($launch)
    $null = $script:remoteBackendPids.Add([int]$launch.process.Id)
    $baseUrl = "http://$Address`:$Port"
    $health = Wait-Health -TimeoutSeconds 150 -BaseUrl $baseUrl
    Require-Check "remote-backend.health" ([bool]($health.ok -and $health.status -eq 200)) ("remote health status " + [string]$health.status)
    Require-Check "remote-backend.no-store" (Test-NoStoreHeaders -Result $health) ("Cache-Control=" + [string](Get-PropertyValue -Object $health.headers -Name "Cache-Control"))
    $version = Get-OpenApiVersion -BaseUrl $baseUrl
    Require-Check "remote-backend.version" (Test-ExpectedOpenApiVersion $version) ("remote openapi version " + $version)
    return [pscustomobject]@{
        launch = $launch
        base_url = $baseUrl
        version = $version
        cache_control = [string](Get-PropertyValue -Object $health.headers -Name "Cache-Control")
    }
}

function Stop-RemoteBackends {
    foreach ($launch in @($script:remoteBackends)) {
        try {
            $launch.process.Refresh()
            if (-not $launch.process.HasExited) {
                Stop-Process -Id $launch.process.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {
        }
        try { $launch.process.WaitForExit(15000) | Out-Null } catch { }
        Complete-GwOutput -Launch $launch
    }
    $script:remoteBackends.Clear()
    $script:remoteBackendPids.Clear()
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
        ui = ConvertTo-SafeObject -Value $script:ui
        remote_backend = ConvertTo-SafeObject -Value $script:remoteBackendEvidence
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
    $null = $md.Add("## UI behavior")
    $uiSafe = ConvertTo-SafeObject -Value $script:ui
    if ($uiSafe.Keys.Count -eq 0) {
        $null = $md.Add("- (no UI scenario recorded)")
    } else {
        foreach ($key in $uiSafe.Keys) {
            $uiValue = $uiSafe[$key]
            if ($uiValue -is [string] -or $uiValue -is [bool] -or $uiValue -is [ValueType]) {
                $null = $md.Add(("- {0}: {1}" -f $key, [string]$uiValue))
            } else {
                $null = $md.Add(("- {0}: {1}" -f $key, (($uiValue | ConvertTo-Json -Compress -Depth 8))))
            }
        }
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
    if ($script:remoteLocalAppData.Length -gt 0 -and (Test-Path -LiteralPath $script:remoteLocalAppData)) {
        $remoteName = Split-Path -Leaf $script:remoteLocalAppData
        if ($remoteName.StartsWith("gw-qin626-accept-remote-")) {
            try {
                Remove-Item -LiteralPath $script:remoteLocalAppData -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Host ("[WARN] could not remove isolated remote LocalAppData: " + (Format-SafeText $_.Exception.Message))
                $ok = $false
            }
        } else {
            Write-Host ("[WARN] refusing to remove non-acceptance remote LocalAppData " + $remoteName)
            $ok = $false
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
using System.Collections.Generic;

public static class GwNative {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int maxCount);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, System.Text.StringBuilder text, int maxCount);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindowEx(IntPtr hwndParent, IntPtr hwndChildAfter, string lpszClass, string lpszWindow);

    public static List<IntPtr> GetProcessWindows(int pid) {
        var list = new List<IntPtr>();
        EnumWindows((hWnd, lParam) => {
            uint p;
            GetWindowThreadProcessId(hWnd, out p);
            if (p == (uint)pid) {
                list.Add(hWnd);
            }
            return true;
        }, IntPtr.Zero);
        return list;
    }

    public static bool DismissProcessDialog(int pid) {
        var windows = GetProcessWindows(pid);
        bool any = false;
        foreach (var hWnd in windows) {
            any = true;
            IntPtr btn = FindWindowEx(hWnd, IntPtr.Zero, "Button", null);
            if (btn != IntPtr.Zero) {
                PostMessage(btn, 0x00F5, IntPtr.Zero, IntPtr.Zero); // BM_CLICK
            }
            PostMessage(hWnd, 0x0111, (IntPtr)1, IntPtr.Zero); // WM_COMMAND, IDOK
            PostMessage(hWnd, 0x0010, IntPtr.Zero, IntPtr.Zero); // WM_CLOSE
        }
        return any;
    }
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
        $script:upgradeRoot = Join-Path $script:processLocalAppData "ai.godsworkbench.desktop\upgrade"
        $script:journalPath = Join-Path $script:upgradeRoot "journal.txt"
        $script:backupProgram = Join-Path $script:upgradeRoot "backup-last\program"
        if ($Reset -and (Test-Path -LiteralPath $script:processLocalAppData)) {
            $overrideName = Split-Path -Leaf $script:processLocalAppData
            if (-not $overrideName.StartsWith("gw-qin626-accept-")) {
                throw "refusing to reset a LocalAppDataOverride that is not named gw-qin626-accept-*"
            }
            Remove-Item -LiteralPath $script:processLocalAppData -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $script:processLocalAppData | Out-Null
        $script:savedProcessEnvLocalAppData = $env:LOCALAPPDATA
        $env:LOCALAPPDATA = $script:processLocalAppData
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
        $lockPid = Get-PropertyValue -Object $lock -Name "pid"
        if ($null -ne $lock -and $lockPid -and (Get-Process -Id ([int]$lockPid) -ErrorAction SilentlyContinue)) {
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
        if (-not $LocalAppDataOverride -and ($upgradeItems.Count -gt 0 -or $clientItems.Count -gt 0) -and -not (Test-Path -LiteralPath $script:markerPath)) {
            throw "desktop upgrade/client state exists without the acceptance marker; refusing to reset"
        }
        if (Test-Path -LiteralPath $script:upgradeRoot) {
            Remove-Item -LiteralPath $script:upgradeRoot -Recurse -Force
        }
        $realUpgradeRoot = Join-Path $script:realLocalAppData "ai.godsworkbench.desktop\upgrade"
        if (Test-Path -LiteralPath $realUpgradeRoot) {
            Remove-Item -LiteralPath $realUpgradeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $script:serverConfig) {
            Remove-Item -LiteralPath $script:serverConfig -Force -ErrorAction SilentlyContinue
        }
        if ($clientItems.Count -gt 0 -and (Test-Path -LiteralPath $script:markerPath)) {
            Remove-Item -LiteralPath $script:clientConfigDir -Recurse -Force
        }
    } else {
        if (-not $LocalAppDataOverride -and ($upgradeItems.Count -gt 0 -or $clientItems.Count -gt 0)) {
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
    $script:lanAddress = Get-PrivateLanAddress
    Require-Check "preflight.lan-address" ($script:lanAddress.Length -gt 0) "no private LAN IPv4 address available for the remote scenarios"
    $script:remoteLocalAppData = Join-Path $env:TEMP ("gw-qin626-accept-remote-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    if (Test-Path -LiteralPath $script:remoteLocalAppData) {
        Remove-Item -LiteralPath $script:remoteLocalAppData -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $script:remoteLocalAppData | Out-Null
    $script:ui["environment"] = [ordered]@{
        lan_address = $script:lanAddress
        remote_data_mode = "isolated-remote-localappdata"
    }

    Add-Step "fresh-install"
    $fresh = Invoke-Installer -Label "01-fresh-install"
    Require-Check "fresh.installer-exit" ($fresh.exit_code -eq 0) ("installer exit " + [string]$fresh.exit_code)
    Require-Check "fresh.journal-verified" ($fresh.journal_after -eq "verified") ("journal " + $fresh.journal_after)
    Require-Check "fresh.desktop-exe" (Test-Path -LiteralPath $script:desktopExe) "desktop exe missing"
    Require-Check "fresh.backend-run" (Test-Path -LiteralPath $script:installedRunPy) "backend run.py missing"
    Require-Check "fresh.uninstaller" (Test-Path -LiteralPath $script:uninstaller) "uninstaller missing"
    Set-Generation -RunPy $script:installedRunPy -Generation "G0"

    Add-Step "first-run"
    # 不再写入任何首启服务器偏好：首启必须由本地优先流程自己启动内置后端。
    Add-Check "first-run.no-server-pin" (-not (Test-Path -LiteralPath $script:serverConfig)) "server preference must not exist before the first launch"
    $firstCdpPort = Get-FreeTcpPort
    $first = Start-DesktopPrimary -Label "02-first-run" -CdpPort $firstCdpPort
    $firstLocalUrl = Wait-CdpTargetUrl -Port $firstCdpPort -Prefix "http://127.0.0.1:2077/static/pages/index.html" -TimeoutSeconds 300
    Require-Check "first-run.local-navigation" ($firstLocalUrl.StartsWith("http://127.0.0.1:2077/static/pages/index.html", [StringComparison]::OrdinalIgnoreCase)) ("installer shell did not enter the local workbench: " + $firstLocalUrl)
    $firstFragment = ""
    if ($firstLocalUrl.Contains("#")) { $firstFragment = $firstLocalUrl.Substring($firstLocalUrl.IndexOf("#") + 1) }
    $firstFragmentOk = $firstFragment.Length -eq 0 -or ($firstFragment.StartsWith("gw_trusted=") -and $firstFragment.Substring(11) -match '^[A-Za-z0-9_.-]+$')
    Add-Check "first-run.destination-shape" ($firstFragmentOk -and -not $firstLocalUrl.Contains("?")) ("unexpected local destination: " + $firstLocalUrl)
    $script:ui["first_run_navigation"] = $firstLocalUrl
    $script:ui["first_run_trusted_fragment"] = ($firstFragment.Length -gt 0)
    Assert-DesktopReady -Label "02-first-run"
    $shot = Save-Screenshot -Path (Join-Path $script:outputDirPath "02-first-run.png")
    Add-Check "first-run.window-visible" ([GwNative]::IsWindowVisible($first.window.handle)) "main window not visible"
    Add-Check "first-run.screenshot" $shot "screenshot failed"
    $firstCdp = New-CdpConnection -Port $firstCdpPort
    try {
        Add-Check "first-run.page-screenshot" (Save-CdpScreenshot -Connection $firstCdp -Path (Join-Path $script:outputDirPath "02-first-run.page.png")) "page screenshot failed"
    } finally {
        Close-CdpConnection -Connection $firstCdp
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $script:processLocalAppData "GodsWorkbench") | Out-Null
    Write-Utf8NoBom -Path $script:sentinelPath -Text ("{""kind"":""qin626-acceptance-sentinel"",""version"":""" + $script:expectedVersionValue + """}`r`n")
    Add-Check "first-run.data-sentinel" (Test-Path -LiteralPath $script:sentinelPath) "data sentinel not created"
    $null = Wait-ForFile -Path $script:serverConfig -TimeoutSeconds 20
    $serverConfigData = Read-JsonFile -Path $script:serverConfig
    $savedServerUrl = Get-PropertyValue -Object $serverConfigData -Name "server_url"
    Add-Check "first-run.server-config" ($savedServerUrl -eq "http://127.0.0.1:2077") ("server.json not normalized to the local backend: " + [string]$savedServerUrl)
    $clientSettingsFixture = [ordered]@{ upload_source_dir = "<acceptance-upload-source>" } | ConvertTo-Json -Compress
    Write-Utf8NoBom -Path $script:clientConfig -Text ($clientSettingsFixture + "`r`n")
    Add-Check "first-run.client-settings-fixture" (Test-Path -LiteralPath $script:clientConfig) "client.json fixture not created"

    Add-Step "tray-find-nas"
    $trayCdp = New-CdpConnection -Port $firstCdpPort
    try {
        $trayNavExpr = "window.location.href = 'http://127.0.0.1:2077/static/pages/settings.html?section=general&action=scan-nas'"
        $null = Invoke-CdpExpression -Connection $trayCdp -Expression $trayNavExpr
        $trayTargetUrl = Wait-CdpTargetUrl -Port $firstCdpPort -Prefix "http://127.0.0.1:2077/static/pages/settings.html" -TimeoutSeconds 30
        Require-Check "tray-find-nas.navigation-ok" ($trayTargetUrl.StartsWith("http://127.0.0.1:2077/static/pages/settings.html", [StringComparison]::OrdinalIgnoreCase)) ("failed to navigate to settings url: " + $trayTargetUrl)
        Start-Sleep -Seconds 2
        $pageCheckExpr = @"
(() => {
    const card = document.querySelector('#remoteWorkspaceCard');
    const panel = document.querySelector('[data-panel="general"]');
    const scanBtn = document.querySelector('#btnScanLan');
    return {
        is404: document.title.includes('404') || (document.body && document.body.innerText.includes('Not Found')),
        generalActive: !!(panel && panel.classList.contains('active')),
        remoteCardExists: !!card,
        scanBtnExists: !!scanBtn
    };
})()
"@
        $trayPageStatus = Invoke-CdpExpression -Connection $trayCdp -Expression $pageCheckExpr
        Require-Check "tray-find-nas.no-404" (-not $trayPageStatus.is404) "settings page returned 404 or not found"
        Require-Check "tray-find-nas.general-active" ($trayPageStatus.generalActive) "general settings panel not active"
        Require-Check "tray-find-nas.remote-card" ($trayPageStatus.remoteCardExists) "remoteWorkspaceCard not present in DOM"
        Require-Check "tray-find-nas.scan-button" ($trayPageStatus.scanBtnExists) "btnScanLan not present in DOM"
        Add-Check "tray-find-nas.page-screenshot" (Save-CdpScreenshot -Connection $trayCdp -Path (Join-Path $script:outputDirPath "02-tray-find-nas.page.png")) "tray find nas screenshot failed"

        $null = Invoke-CdpExpression -Connection $trayCdp -Expression "window.location.href = 'http://127.0.0.1:2077/static/pages/index.html'"
        $null = Wait-CdpTargetUrl -Port $firstCdpPort -Prefix "http://127.0.0.1:2077/static/pages/index.html" -TimeoutSeconds 30
        Start-Sleep -Seconds 1
    } finally {
        Close-CdpConnection -Connection $trayCdp
    }

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
    $hotLockPid = Get-PropertyValue -Object $lock -Name "pid"
    Require-Check "hot-start.lock-unchanged" ($null -ne $lock -and [int]$hotLockPid -eq [int]$script:desktop.Id) ("lock pid " + [string]$hotLockPid)
    $script:lifecycle["03-hot-start"] = [ordered]@{
        helper_exit = $hotCode
        desktop_pids = $desktopPids
        backend_pids = $backendPids
        listener_pids = $listenerPids
        lock_pid = if ($null -ne $lock) { [int]$hotLockPid } else { 0 }
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
            $null = Close-Window -Handle $loserInfo.handle -ProcessId ([int]$loser.Id)
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
        $null = Close-Window -Handle $dialog.handle -ProcessId ([int]$blockedLaunch.process.Id)
    }
    $blockedExited = $false
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $blockedExited = Wait-ProcessExit -Process $blockedLaunch.process -TimeoutSeconds 2
        if ($blockedExited) { break }
        $null = Close-Window -Handle $dialog.handle -ProcessId ([int]$blockedLaunch.process.Id)
    }
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

    Add-Step "local-failure-and-retry"
    $portHolder = Start-TcpHolder -Port 2077 -Label "12-port-holder"
    $portOwnerBefore = @(Get-ListeningPids -Port 2077)
    Require-Check "port-conflict.holder" ($portOwnerBefore.Count -eq 1) ("pre-holder listeners " + ($portOwnerBefore -join ","))
    $conflictCdpPort = Get-FreeTcpPort
    $conflictLaunch = Start-DesktopPrimary -Label "12-port-conflict" -CdpPort $conflictCdpPort
    $conflictCdp = New-CdpConnection -Port $conflictCdpPort
    try {
        Add-Check "port-conflict.shell-origin" ([string]$conflictCdp.target_url -like "http://tauri.localhost*") ("unexpected shell target: " + [string]$conflictCdp.target_url)
        Require-Check "port-conflict.ui-failure" (Wait-ShellFailure -Cdp $conflictCdp -TimeoutSeconds 180) "installer shell did not show a retryable local failure"
        $failureSnapshot = Get-ShellFailureSnapshot -Cdp $conflictCdp
        $script:ui["local_failure"] = $failureSnapshot
        Require-Check "port-conflict.ui-title" ([string]$failureSnapshot.title -eq "本机端口被占用") ("local failure title: " + [string]$failureSnapshot.title)
        Require-Check "port-conflict.ui-reason" ([string]$failureSnapshot.reason -match "2077") ("local failure reason: " + [string]$failureSnapshot.reason)
        Require-Check "port-conflict.ui-next" ([string]$failureSnapshot.next.Length -gt 0) "local failure next step is empty"
        Require-Check "port-conflict.ui-retry" ([bool]$failureSnapshot.retry_visible) "local retry button is not visible"
        Require-Check "port-conflict.ui-home" ([bool]$failureSnapshot.home_visible -and -not [bool]$failureSnapshot.remote_visible) "failure did not stay on the home view"
        $conflictDiag = Get-DesktopDiagnosticsSnapshot -Label "12-port-conflict.diagnostics" -WaitSeconds 30
        $conflictError = ""
        if ($null -ne $conflictDiag -and $null -ne $conflictDiag.data) {
            $conflictError = [string]$conflictDiag.data.diagnostics.last_error
        }
        Require-Check "port-conflict.last-error" ($conflictError -match "2077") ("desktop last_error " + $conflictError)
        Add-Check "port-conflict.screenshot" (Save-Screenshot -Path (Join-Path $script:outputDirPath "12-port-conflict.png")) "screenshot failed"
        Add-Check "port-conflict.page-screenshot" (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath "12-port-conflict.page.png")) "page screenshot failed"

        $darkTheme = Set-ShellTheme -Cdp $conflictCdp -Theme "dark"
        Require-Check "port-conflict.theme-dark" ([string]$darkTheme.theme -eq "dark" -and [string]$darkTheme.surface -eq "#08090c" -and [string]$darkTheme.surface_card -eq "#111216" -and [string]$darkTheme.text_main -eq "#e8e8ea" -and [string]$darkTheme.gold -eq "#dfc384") ("dark tokens: " + ($darkTheme | ConvertTo-Json -Compress))
        $darkShot = Save-Screenshot -Path (Join-Path $script:outputDirPath "12-theme-dark.png")
        Add-Check "port-conflict.theme-dark-screenshot" $darkShot "dark screenshot failed"
        Add-Check "port-conflict.theme-dark-page-screenshot" (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath "12-theme-dark.page.png")) "dark page screenshot failed"
        $lightTheme = Set-ShellTheme -Cdp $conflictCdp -Theme "light"
        Require-Check "port-conflict.theme-light" ([string]$lightTheme.theme -eq "light" -and [string]$lightTheme.surface -eq "rgb(241, 242, 245)" -and [string]$lightTheme.surface_card -eq "rgb(255, 255, 255)" -and [string]$lightTheme.text_main -eq "rgb(17, 20, 26)" -and [string]$lightTheme.gold -eq "#dfc384") ("light tokens: " + ($lightTheme | ConvertTo-Json -Compress))
        Require-Check "port-conflict.theme-light-persist" ([string]$lightTheme.stored -eq "light") ("stored theme: " + [string]$lightTheme.stored)
        $lightShot = Save-Screenshot -Path (Join-Path $script:outputDirPath "12-theme-light.png")
        Add-Check "port-conflict.theme-light-screenshot" $lightShot "light screenshot failed"
        Add-Check "port-conflict.theme-light-page-screenshot" (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath "12-theme-light.page.png")) "light page screenshot failed"
        $darkThemeRestored = Set-ShellTheme -Cdp $conflictCdp -Theme "dark"
        Require-Check "port-conflict.theme-dark-persist" ([string]$darkThemeRestored.theme -eq "dark" -and [string]$darkThemeRestored.stored -eq "dark") ("stored theme: " + [string]$darkThemeRestored.stored)
        $script:ui["themes"] = [ordered]@{ dark = $darkTheme; light = $lightTheme; dark_restored = $darkThemeRestored }

        $layouts = @()
        foreach ($layoutWidth in @(724, 900, 1440, 1920)) {
            $measurement = Measure-ShellLayout -Cdp $conflictCdp -Width $layoutWidth
            $expectedColumns = 3
            if ($layoutWidth -le 724) { $expectedColumns = 1 }
            $expectedCard = 720
            if ($layoutWidth -le 724) { $expectedCard = 696 }
            if ($layoutWidth -eq 1440) { $expectedCard = 760 }
            if ($layoutWidth -eq 1920) { $expectedCard = 800 }
            $expectedFont = "14px"
            if ($layoutWidth -eq 1920) { $expectedFont = "15px" }
            $layoutOk = ([Math]::Abs([int]$measurement.width - $layoutWidth) -le 2) -and ([int]$measurement.columns -eq $expectedColumns) -and ([Math]::Abs([int]$measurement.card_width - $expectedCard) -le 16) -and ([string]$measurement.body_font -eq $expectedFont) -and (-not [bool]$measurement.overflow)
            Require-Check ("port-conflict.layout-" + $layoutWidth) $layoutOk ("layout " + ($measurement | ConvertTo-Json -Compress))
            Add-Check ("port-conflict.layout-" + $layoutWidth + "-page-screenshot") (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath ("12-layout-" + $layoutWidth + ".page.png"))) "layout page screenshot failed"
            $layouts += $measurement
        }
        $script:ui["layouts"] = $layouts

        $fakePort = Get-FreeAddressPort -Address $script:lanAddress
        $fakeModePath = Join-Path $script:outputDirPath "12-fake-remote.mode.txt"
        Set-FakeRemoteMode -ModePath $fakeModePath -Mode "no-store"
        $fake = Start-FakeRemoteServer -Address $script:lanAddress -Port $fakePort -Label "12-fake-remote" -ModePath $fakeModePath
        $remoteAttemptUrl = "http://$($script:lanAddress):$fakePort"
        Add-Check "remote-fail-closed.view" ([bool](Open-ShellRemoteView -Cdp $conflictCdp).remote_visible) "remote view did not open"
        $null = Submit-ShellRemoteUrl -Cdp $conflictCdp -Url $remoteAttemptUrl
        Require-Check "remote-fail-closed.no-store-visible" (Wait-ShellRemoteError -Cdp $conflictCdp -TimeoutSeconds 180) "remote no-store rejection did not reach the shell"
        $noStoreSnapshot = Get-ShellRemoteErrorSnapshot -Cdp $conflictCdp
        $script:ui["remote_fail_closed_no_store"] = $noStoreSnapshot
        Require-Check "remote-fail-closed.no-store-title" ([string]$noStoreSnapshot.title -eq "远端服务版本无法可信验证") ("remote no-store title: " + [string]$noStoreSnapshot.title)
        Require-Check "remote-fail-closed.no-store-reason" ([string]$noStoreSnapshot.reason -match "no-store") ("remote no-store reason: " + [string]$noStoreSnapshot.reason)
        Require-Check "remote-fail-closed.no-store-retry" ([bool]$noStoreSnapshot.retry_visible) "remote retry button is not visible"
        Add-Check "remote-fail-closed.no-store-screenshot" (Save-Screenshot -Path (Join-Path $script:outputDirPath "12-remote-no-store.png")) "screenshot failed"
        Add-Check "remote-fail-closed.no-store-page-screenshot" (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath "12-remote-no-store.page.png")) "page screenshot failed"

        Set-FakeRemoteMode -ModePath $fakeModePath -Mode "no-version"
        $null = Invoke-CdpExpression -Connection $conflictCdp -Expression "(() => { document.getElementById('remote-error').hidden = true; document.getElementById('remote-retry').click(); return true; })()"
        $noVersionShown = Wait-CdpExpression -Connection $conflictCdp -Expression @"
(() => {
  const error = document.getElementById('remote-error');
  const title = document.getElementById('remote-error-title');
  const reason = document.getElementById('remote-error-reason');
  return !!(error && !error.hidden && title && title.textContent.trim() === '远端服务版本无法可信验证' && reason && reason.textContent.indexOf('+gw.') >= 0);
})()
"@ -TimeoutSeconds 180
        Require-Check "remote-fail-closed.no-version-visible" $noVersionShown "remote missing +gw. rejection did not reach the shell"
        $noVersionSnapshot = Get-ShellRemoteErrorSnapshot -Cdp $conflictCdp
        $script:ui["remote_fail_closed_no_version"] = $noVersionSnapshot
        Require-Check "remote-fail-closed.no-version-reason" ([string]$noVersionSnapshot.reason -match "\+gw\.") ("remote no-version reason: " + [string]$noVersionSnapshot.reason)
        Add-Check "remote-fail-closed.no-version-screenshot" (Save-Screenshot -Path (Join-Path $script:outputDirPath "12-remote-no-version.png")) "screenshot failed"
        Add-Check "remote-fail-closed.no-version-page-screenshot" (Save-CdpScreenshot -Connection $conflictCdp -Path (Join-Path $script:outputDirPath "12-remote-no-version.page.png")) "page screenshot failed"

        Stop-TcpHolder -Holder $fake
        Require-Check "remote-fail-closed.port-freed" (Wait-ForAddressPortFree -Address $script:lanAddress -Port $fakePort -TimeoutSeconds 30) "fake remote port did not free"

        Require-Check "remote-fail-closed.back-home" ([bool](Invoke-CdpExpression -Connection $conflictCdp -Expression "(() => { document.getElementById('remote-back').click(); return !document.getElementById('home-view').hidden; })()")) "could not return to the home view"
        Stop-TcpHolder -Holder $portHolder
        Require-Check "port-conflict.port-freed" (Wait-ForPortFree -Port 2077 -TimeoutSeconds 30) "port 2077 did not free after holder stop"
        $null = Invoke-CdpExpression -Connection $conflictCdp -Expression "(() => { document.getElementById('local-retry').click(); return true; })()"
        $retryLocalUrl = Wait-CdpTargetUrl -Port $conflictCdpPort -Prefix "http://127.0.0.1:2077/static/pages/index.html" -TimeoutSeconds 300
        Require-Check "port-conflict.retry-navigation" ($retryLocalUrl.StartsWith("http://127.0.0.1:2077/static/pages/index.html", [StringComparison]::OrdinalIgnoreCase)) ("local retry did not enter the workbench: " + $retryLocalUrl)
        $script:ui["local_retry_navigation"] = $retryLocalUrl
        Close-CdpConnection -Connection $conflictCdp
        $conflictCdp = $null
        Assert-DesktopReady -Label "12-port-conflict-retry"
        $retryServer = Read-JsonFile -Path $script:serverConfig
        Require-Check "port-conflict.retry-server-config" ([string](Get-PropertyValue -Object $retryServer -Name "server_url") -eq "http://127.0.0.1:2077") ("retry server.json: " + [string](Get-PropertyValue -Object $retryServer -Name "server_url"))
        $backendAfterRetry = @(Get-BackendPids)
        Require-Check "port-conflict.retry-backend" ($backendAfterRetry.Count -eq 1) ("backend PIDs after retry " + ($backendAfterRetry -join ","))
        $script:lifecycle["12-port-conflict"] = [ordered]@{
            error = $conflictError
            holder_pid = [int]$portHolder.process.Id
            listener_pids_before = $portOwnerBefore
            backend_pids_after_retry = $backendAfterRetry
        }
    } finally {
        Close-CdpConnection -Connection $conflictCdp
    }
    Stop-DesktopGracefully -Label "12-port-conflict-quit"

    Add-Step "remote-backend-prepare"
    $remotePort = $fakePort
    $remote = Start-RemoteBackend -Label "12b-remote-backend" -Address $script:lanAddress -Port $remotePort
    $script:remoteBackendEvidence = [ordered]@{
        base_url = $remote.base_url
        openapi_version = $remote.version
        cache_control = $remote.cache_control
        local_app_data = "<REMOTE_LOCALAPPDATA>"
    }
    $script:ui["remote_backend"] = $script:remoteBackendEvidence

    Add-Step "remote-entry"
    $remoteHolder = Start-TcpHolder -Port 2077 -Label "13-remote-holder"
    $remoteCdpPort = Get-FreeTcpPort
    $remoteLaunch = Start-DesktopPrimary -Label "13-remote-entry" -CdpPort $remoteCdpPort
    $remoteCdp = New-CdpConnection -Port $remoteCdpPort
    try {
        Require-Check "remote-entry.ui-failure" (Wait-ShellFailure -Cdp $remoteCdp -TimeoutSeconds 180) "installer shell did not show the local failure before the remote test"
        Require-Check "remote-entry.remote-view" ([bool](Open-ShellRemoteView -Cdp $remoteCdp).remote_visible) "remote view did not open"
        $null = Submit-ShellRemoteUrl -Cdp $remoteCdp -Url $remoteAttemptUrl
        $remoteExpected = "$remoteAttemptUrl/static/pages/index.html"
        $remoteTargetUrl = Wait-CdpTargetUrl -Port $remoteCdpPort -Prefix $remoteExpected -TimeoutSeconds 300
        Require-Check "remote-entry.navigation" ($remoteTargetUrl.StartsWith($remoteExpected, [StringComparison]::OrdinalIgnoreCase)) ("remote connection did not enter the workbench: " + $remoteTargetUrl)
        Require-Check "remote-entry.no-trusted-ticket" (-not $remoteTargetUrl.Contains("gw_trusted") -and -not $remoteTargetUrl.Contains("?")) ("remote destination carries a local ticket: " + $remoteTargetUrl)
        $script:ui["remote_navigation"] = $remoteTargetUrl
        $savedRemote = Read-JsonFile -Path $script:serverConfig
        $savedRemoteUrl = [string](Get-PropertyValue -Object $savedRemote -Name "server_url")
        Require-Check "remote-entry.server-config" ($savedRemoteUrl -eq $remoteAttemptUrl) ("server.json remote value: " + $savedRemoteUrl)
        $remoteBackendPids = @($script:remoteBackendPids | Where-Object { Get-Process -Id ([int]$_) -ErrorAction SilentlyContinue })
        Require-Check "remote-entry.remote-backend" ($remoteBackendPids.Count -eq 1) ("remote backend PIDs: " + ($remoteBackendPids -join ","))
        $localBackendPids = @(Get-BackendPids)
        Require-Check "remote-entry.no-local-backend" ($localBackendPids.Count -eq 0) ("local backend PIDs: " + ($localBackendPids -join ","))
        $remoteListeners = @(Get-ListeningPids -Port $remotePort)
        Require-Check "remote-entry.remote-listener" ($remoteListeners.Count -ge 1 -and $remoteListeners -contains [int]$remoteBackendPids[0]) ("remote listeners: " + ($remoteListeners -join ",") + "; remote backend PIDs: " + ($remoteBackendPids -join ","))
        $remoteListener2077 = @(Get-ListeningPids -Port 2077)
        Require-Check "remote-entry.local-port-owner" ($remoteListener2077.Count -eq 1 -and $remoteListener2077[0] -eq [int]$remoteHolder.process.Id) ("port 2077 listeners: " + ($remoteListener2077 -join ",") + "; holder pid: " + [string]$remoteHolder.process.Id)
        $script:ui["remote_entry_snapshot"] = [ordered]@{
            remote_navigation = $remoteTargetUrl
            server_config = $savedRemoteUrl
            remote_backend_pids = $remoteBackendPids
            local_backend_pids = $localBackendPids
            remote_listener_pids = $remoteListeners
        }
        Add-Check "remote-entry.screenshot" (Save-Screenshot -Path (Join-Path $script:outputDirPath "13-remote-workbench.png")) "screenshot failed"
        $remoteShotCdp = New-CdpConnection -Port $remoteCdpPort
        try {
            Add-Check "remote-entry.page-screenshot" (Save-CdpScreenshot -Connection $remoteShotCdp -Path (Join-Path $script:outputDirPath "13-remote-workbench.page.png")) "page screenshot failed"
        } finally {
            Close-CdpConnection -Connection $remoteShotCdp
        }
    } finally {
        Close-CdpConnection -Connection $remoteCdp
    }
    Stop-TcpHolder -Holder $remoteHolder
    Require-Check "remote-entry.port-freed" (Wait-ForPortFree -Port 2077 -TimeoutSeconds 30) "port 2077 did not free after the remote holder stop"
    Stop-DesktopGracefully -Label "13-remote-quit"
    Stop-RemoteBackends
    Require-Check "remote-entry.backend-stopped" (Wait-ForAddressPortFree -Address $script:lanAddress -Port $remotePort -TimeoutSeconds 30) "remote backend port stayed open after stop"
    $script:lifecycle["13-remote-entry"] = [ordered]@{
        remote_url = $remoteAttemptUrl
        remote_openapi_version = $remote.version
        cache_control = $remote.cache_control
    }

    Add-Step "upgrade-rotation"
    Start-Sleep -Milliseconds 600
    Set-Generation -RunPy $script:installedRunPy -Generation "G0"
    $upgradeOne = Invoke-Installer -Label "13-upgrade-g1"
    Require-Check "upgrade-g1.exit" ($upgradeOne.exit_code -eq 0) ("installer exit " + [string]$upgradeOne.exit_code + "; self-check: " + (Get-SelfCheckTail))
    Require-Check "upgrade-g1.journal" ($upgradeOne.journal_after -eq "verified") ("journal " + $upgradeOne.journal_after)
    Require-Check "upgrade-g1.backup" ($upgradeOne.backup_generation_after -eq "G0") ("backup generation " + $upgradeOne.backup_generation_after)
    Set-Generation -RunPy $script:installedRunPy -Generation "G1"
    $upgradeTwo = Invoke-Installer -Label "14-upgrade-g2"
    Require-Check "upgrade-g2.exit" ($upgradeTwo.exit_code -eq 0) ("installer exit " + [string]$upgradeTwo.exit_code + "; self-check: " + (Get-SelfCheckTail))
    Require-Check "upgrade-g2.journal" ($upgradeTwo.journal_after -eq "verified") ("journal " + $upgradeTwo.journal_after)
    Require-Check "upgrade-g2.backup" ($upgradeTwo.backup_generation_after -eq "G1") ("backup generation " + $upgradeTwo.backup_generation_after)
    Set-Generation -RunPy $script:installedRunPy -Generation "G2"

    Add-Step "failed-upgrade-rollback"
    Start-Sleep -Milliseconds 600
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
    $uninstallEnv = @{
        LOCALAPPDATA = $script:processLocalAppData
    }
    $uninstallLaunch = Start-GwProcess -FilePath $script:uninstaller -Arguments @("/S") -Label "18-uninstall" -Environment $uninstallEnv -WorkingDirectory $script:outputDirPath
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
    Stop-RemoteBackends
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
    foreach ($launch in @($script:processLaunches.Values)) {
        try { Complete-GwOutput -Launch $launch } catch { }
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
    if ($script:savedProcessEnvLocalAppData) { $env:LOCALAPPDATA = $script:savedProcessEnvLocalAppData }
}
exit $exitCode
