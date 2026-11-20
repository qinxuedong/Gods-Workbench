param(
  [ValidateSet("PostInstall")]
  [string]$Mode = "PostInstall"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path (Split-Path -Parent $scriptDir) "backend"
$upgradeRoot = Join-Path $env:LOCALAPPDATA "ai.godsworkbench.desktop\upgrade"
$journal = Join-Path $upgradeRoot "journal.txt"
New-Item -ItemType Directory -Force -Path $upgradeRoot | Out-Null

function Set-JournalState([string]$state) {
  Set-Content -LiteralPath $journal -Value $state -NoNewline -Encoding ascii
}

function Fail([string]$message) {
  Set-JournalState "rolled_back"
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

$stdoutLog = Join-Path $upgradeRoot "self-check.log"
$stderrLog = Join-Path $upgradeRoot "self-check.err.log"
$env:GW_RUNTIME_MODE = "prod"
$env:GW_HOST = "127.0.0.1"
$env:GW_PORT = "2077"
$env:PYTHONDONTWRITEBYTECODE = "1"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python
# 默认安装目录含空格，run.py 路径必须带引号。
$psi.Arguments = "`"$runPy`" --prod --self-check --expect-version `"$version`""
$psi.WorkingDirectory = $backend
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $psi

try {
  if (-not $process.Start()) {
    throw "进程未启动"
  }
} catch {
  Fail "无法启动安装自检"
}

$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
if (-not $process.WaitForExit(60000)) {
  try { $process.Kill() } catch { }
  $process.WaitForExit()
  try { Set-Content -LiteralPath $stdoutLog -Value $stdoutTask.Result -NoNewline -Encoding UTF8 } catch { }
  try { Set-Content -LiteralPath $stderrLog -Value $stderrTask.Result -NoNewline -Encoding UTF8 } catch { }
  Fail "安装自检超时"
}

$stdout = $stdoutTask.Result
$stderr = $stderrTask.Result
Set-Content -LiteralPath $stdoutLog -Value $stdout -NoNewline -Encoding UTF8
Set-Content -LiteralPath $stderrLog -Value $stderr -NoNewline -Encoding UTF8
if ($process.ExitCode -ne 0) {
  Fail "安装自检未通过"
}

Set-JournalState "verified"
exit 0
