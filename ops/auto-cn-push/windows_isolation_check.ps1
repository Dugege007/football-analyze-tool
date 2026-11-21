# 在 YuePC / MSI 上验收本机文件锁和明确的 Python。不连接服务器，不安装功能，不开端口。
param(
  [Parameter(Mandatory = $true)][string]$DropDir,
  [Parameter(Mandatory = $true)][string]$Python,
  [switch]$Probe
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'windows_drop_lock.ps1')

if ($Probe) {
  $held = Open-FatDropLock -Dir $DropDir
  if (-not $held) {
    [Console]::Error.WriteLine('DROP_LOCK_HELD')
    exit 11
  }
  $held.Dispose()
  Write-Output 'DROP_LOCK_ACQUIRED'
  exit 0
}

function Fail-Dep([string]$Name) {
  [Console]::Error.WriteLine("DEPENDENCY_MISSING $Name")
  exit 20
}

if (-not (Test-Path -LiteralPath $Python)) { Fail-Dep "python $Python" }
$exe = (Resolve-Path -LiteralPath $Python).Path
if ($exe -match '\\WindowsApps\\') { Fail-Dep 'python_stub' }
& $exe -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { Fail-Dep "python_exit $LASTEXITCODE" }

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { Fail-Dep 'ssh.exe' }

$mine = Open-FatDropLock -Dir $DropDir
if (-not $mine) {
  [Console]::Error.WriteLine('DROP_LOCK_HELD')
  exit 11
}
try {
  $childArgs = '-NoProfile -ExecutionPolicy Bypass -File {0} -DropDir {1} -Python {2} -Probe' -f (
    '"{0}"' -f ($PSCommandPath -replace '"', '\"')
  ), (
    '"{0}"' -f ($DropDir -replace '"', '\"')
  ), (
    '"{0}"' -f ($Python -replace '"', '\"')
  )
  $probe = Start-Process -FilePath 'powershell.exe' -Wait -PassThru -WindowStyle Hidden -ArgumentList $childArgs
  if ($probe.ExitCode -ne 11) {
    [Console]::Error.WriteLine("probe exit $($probe.ExitCode)")
    exit 21
  }
} finally {
  $mine.Dispose()
}
Write-Output 'ISOLATION_OK'
exit 0
