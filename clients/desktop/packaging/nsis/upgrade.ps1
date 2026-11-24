param(
  [ValidateSet("PostInstall")]
  [string]$Mode = "PostInstall",
  [string]$UserLocalAppData = "",
  [string]$UserAppData = "",
  [string]$InstallDir = "",
  [string]$SelectedDataDir = "",
  [ValidateSet("FreshInstall", "UpgradeKeep")]
  [string]$InstallAction = "UpgradeKeep"
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$effectiveInstallDir = if ($InstallDir -and $InstallDir.Trim()) { $InstallDir.Trim() } else { Split-Path -Parent $scriptDir }
$backend = Join-Path $effectiveInstallDir "backend"

$effectiveLocalAppData = if ($UserLocalAppData -and $UserLocalAppData.Trim()) { $UserLocalAppData.Trim() } else { $env:LOCALAPPDATA }
$effectiveAppData = if ($UserAppData -and $UserAppData.Trim()) { $UserAppData.Trim() } else { $env:APPDATA }

if (-not $effectiveLocalAppData -or -not [IO.Path]::IsPathRooted($effectiveLocalAppData)) {
  Write-Error "无法确定原本登录用户的 LOCALAPPDATA"
  exit 1
}
if ($effectiveLocalAppData -match '\\Windows\\System32\\config\\systemprofile\\') {
  Write-Error "拒绝将用户数据写入 SYSTEM profile"
  exit 1
}

$upgradeRoot = Join-Path $effectiveLocalAppData "ai.godsworkbench.desktop\upgrade"
$journal = Join-Path $upgradeRoot "journal.txt"
$stdoutLog = Join-Path $upgradeRoot "self-check.log"
$stderrLog = Join-Path $upgradeRoot "self-check.err.log"
New-Item -ItemType Directory -Force -Path $upgradeRoot | Out-Null

function Set-JournalState([string]$state) {
  Set-Content -LiteralPath $journal -Value $state -NoNewline -Encoding ascii
}

function Fail([string]$message) {
  # 保留 installed，交由 NSIS 还原后再写 rolled_back；提前落盘会让中断的安装器覆盖唯一备份。
  try {
    Add-Content -LiteralPath $stderrLog -Value ("PostInstall: " + $message) -Encoding UTF8
  } catch {
  }
  Write-Error $message
  exit 1
}

if ($Mode -ne "PostInstall") {
  Fail "不支持的升级钩子模式"
}

$manifestPath = Join-Path $backend "bundle-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
  Fail "安装缺少随包清单"
}

try {
  $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
  Fail "随包清单不可解析"
}

$version = [string]$manifest.product_version
if ($version -notmatch '^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$') {
  Fail "随包清单版本非法"
}
if ([string]$manifest.capability_digest -notmatch '^[0-9a-f]{64}$') {
  Fail "随包清单能力摘要非法"
}

$python = Join-Path $backend "python\python.exe"
$runPy = Join-Path $backend "run.py"
if (-not (Test-Path -LiteralPath $python)) { Fail "安装缺少私有 Python 运行时" }
if (-not (Test-Path -LiteralPath $runPy)) { Fail "安装缺少后端入口" }

$newBootstrapDir = Join-Path $effectiveLocalAppData "Gods Workbench"
$legacyBootstrapDir = Join-Path $effectiveLocalAppData "GodsWorkbench"
$bootstrapDir = $newBootstrapDir
if (-not (Test-Path -LiteralPath (Join-Path $newBootstrapDir "runtime-root.json"))) {
  $legacyPtr = Join-Path $legacyBootstrapDir "runtime-root.json"
  if (Test-Path -LiteralPath $legacyPtr) {
    $bootstrapDir = $legacyBootstrapDir
  }
}
$pointerPath = Join-Path $bootstrapDir "runtime-root.json"
$pointerExistedBefore = Test-Path -LiteralPath $pointerPath
$dataDirExistedBefore = ($SelectedDataDir -and (Test-Path -LiteralPath $SelectedDataDir))
$pointerCreatedByInstall = $false

function Invoke-PythonWithUserEnv([string]$arguments) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $python
  $psi.Arguments = $arguments
  $psi.WorkingDirectory = $backend
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.EnvironmentVariables["GW_RUNTIME_MODE"] = "prod"
  $psi.EnvironmentVariables["GW_HOST"] = "127.0.0.1"
  $psi.EnvironmentVariables["GW_PORT"] = "2077"
  $psi.EnvironmentVariables["GW_INSTALL_DIR"] = $effectiveInstallDir
  $psi.EnvironmentVariables["LOCALAPPDATA"] = $effectiveLocalAppData
  if ($effectiveAppData) {
    $psi.EnvironmentVariables["APPDATA"] = $effectiveAppData
  }
  $psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $psi
  if (-not $process.Start()) {
    throw "进程未启动"
  }
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  if (-not $process.WaitForExit(60000)) {
    try { $process.Kill() } catch { }
    $process.WaitForExit()
    throw "子进程执行超时"
  }
  return [pscustomobject]@{
    ExitCode = $process.ExitCode
    Stdout = $stdoutTask.Result
    Stderr = $stderrTask.Result
  }
}

function Cleanup-FreshInstallPointer {
  if ($pointerCreatedByInstall -and (-not $pointerExistedBefore)) {
    try {
      if (Test-Path -LiteralPath $pointerPath) {
        Remove-Item -LiteralPath $pointerPath -Force
      }
    } catch { }
    if ($SelectedDataDir -and (-not $dataDirExistedBefore) -and (Test-Path -LiteralPath $SelectedDataDir)) {
      try {
        if (@(Get-ChildItem -LiteralPath $SelectedDataDir -Force -ErrorAction SilentlyContinue).Count -eq 0) {
          Remove-Item -LiteralPath $SelectedDataDir -Force
        }
      } catch { }
    }
  }
}

if ($InstallAction -eq "FreshInstall" -and $SelectedDataDir -and (-not $pointerExistedBefore)) {
  try {
    $initArgs = "-m gw.settings.data_location --installer-init --target `"$SelectedDataDir`" --install-dir `"$effectiveInstallDir`""
    $initRes = Invoke-PythonWithUserEnv $initArgs
    if ($initRes.ExitCode -ne 0) {
      Cleanup-FreshInstallPointer
      Fail ("初始化用户数据目录指针失败: " + $initRes.Stdout + " " + $initRes.Stderr)
    }
    $pointerCreatedByInstall = $true
  } catch {
    Cleanup-FreshInstallPointer
    Fail ("初始化用户数据目录异常: " + $_.Exception.Message)
  }
}

try {
  $checkArgs = "`"$runPy`" --prod --self-check --expect-version `"$version`""
  $checkRes = Invoke-PythonWithUserEnv $checkArgs
} catch {
  Cleanup-FreshInstallPointer
  Fail ("无法启动安装自检: " + $_.Exception.Message)
}

Set-Content -LiteralPath $stdoutLog -Value $checkRes.Stdout -NoNewline -Encoding UTF8
Set-Content -LiteralPath $stderrLog -Value $checkRes.Stderr -NoNewline -Encoding UTF8
if ($checkRes.ExitCode -ne 0) {
  Cleanup-FreshInstallPointer
  Fail "安装自检未通过"
}

Set-JournalState "verified"
exit 0
