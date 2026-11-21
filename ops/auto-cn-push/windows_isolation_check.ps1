# PS 5.1 on gb2312: this file is UTF-8 with BOM so ParseFile keeps every character.
# Checks: ParseFile, child arguments that contain spaces, a missing directory with spaces,
# install blocked while push holds the lock, SSH failure leaves ready and writes no receipt.
# It does not claim the remote transaction succeeded.
param(
  [Parameter(Mandatory = $true)][string]$DropDir,
  [Parameter(Mandatory = $true)][string]$Python,
  [switch]$EchoArgs
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'windows_drop_lock.ps1')
. (Join-Path $here 'windows_argv.ps1')

if ($EchoArgs) {
  Write-Output "ECHO_FILE=$PSCommandPath"
  Write-Output "ECHO_DROP=$DropDir"
  Write-Output "ECHO_PY=$Python"
  exit 0
}

function Fail([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 21
}

function Fail-Dep([string]$Name) {
  [Console]::Error.WriteLine("DEPENDENCY_MISSING $Name")
  exit 20
}

function Start-FatChild {
  param(
    [Parameter(Mandatory = $true)][string[]]$Argument
  )
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $psi.Arguments = Format-FatWinCommandLine -Argument $Argument
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()
  return @{
    Proc = $proc
    OutTask = $proc.StandardOutput.ReadToEndAsync()
    ErrTask = $proc.StandardError.ReadToEndAsync()
  }
}

function Finish-FatChild($Child) {
  [void]$Child.Proc.WaitForExit()
  $out = ''
  $err = ''
  try { $out = $Child.OutTask.Result } catch {}
  try { $err = $Child.ErrTask.Result } catch {}
  return @{ Code = $Child.Proc.ExitCode; Out = $out; Err = $err }
}

$tokens = $null
$parseErrors = $null
Get-ChildItem -LiteralPath $here -Filter '*.ps1' | ForEach-Object {
  $tokens = $null
  $parseErrors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors)
  if ($parseErrors -and @($parseErrors).Count -gt 0) {
    $first = @($parseErrors)[0]
    Fail ("PARSE_FAIL " + $_.Name + " line " + $first.Extent.StartLineNumber + " " + $first.Message)
  }
}
Write-Output 'PS51_PARSE_OK'

$echo = Start-FatChild -Argument @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', $PSCommandPath,
  '-EchoArgs',
  '-DropDir', $DropDir,
  '-Python', $Python
)
$echoResult = Finish-FatChild $echo
if ($echoResult.Code -ne 0) { Fail ("arg echo exit " + $echoResult.Code + " " + $echoResult.Err) }
foreach ($pair in @(
  @('ECHO_FILE=', $PSCommandPath),
  @('ECHO_DROP=', $DropDir),
  @('ECHO_PY=', $Python)
)) {
  $expected = $pair[0] + $pair[1]
  if ($echoResult.Out -notmatch [regex]::Escape($expected)) {
    Fail ("arg split: " + $echoResult.Out)
  }
}
Write-Output '参数回显通过'

if (-not (Test-Path -LiteralPath $Python)) { Fail-Dep "python $Python" }
$pythonExe = (Resolve-Path -LiteralPath $Python).Path
if ($pythonExe -match '\\WindowsApps\\') { Fail-Dep 'python_stub' }
& $pythonExe -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { Fail-Dep "python_exit $LASTEXITCODE" }
$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { Fail-Dep 'ssh.exe' }

$missing = Join-Path ([System.IO.Path]::GetTempPath()) ("fat missing " + [guid]::NewGuid().ToString('n') + "\inner drop")
if ([System.IO.Directory]::Exists($missing)) { Fail "fixture already exists $missing" }
$created = Open-FatDropLock -Dir $missing
if (-not $created) { Fail "cannot create $missing" }
if (-not [System.IO.Directory]::Exists($missing)) { Fail "spaced directory was not created" }
$created.Dispose()
$again = Open-FatDropLock -Dir $missing
if (-not $again) { Fail "lock was not released for reacquire" }
$again.Dispose()
Write-Output '空格目录已创建'
Write-Output '锁释放后可再取得'

$py = @'
import hashlib, pathlib, struct, sys, zlib
dest = pathlib.Path(sys.argv[1])
raw = b"windows-isolation-db"
commit = "baseline-isolation"
compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
body = compressor.compress(raw) + compressor.flush()
comment = commit.encode("ascii") + b"\0"
header = struct.pack("<BBBBLBB", 0x1F, 0x8B, 8, 16, 0, 0, 255)
crc = zlib.crc32(raw) & 0xFFFFFFFF
blob = header + comment + body + struct.pack("<II", crc, len(raw) & 0xFFFFFFFF)
dest.mkdir(parents=True, exist_ok=True)
(dest / "app_latest.db.gz").write_bytes(blob)
db_sha = hashlib.sha256(raw).hexdigest()
gz_sha = hashlib.sha256(blob).hexdigest()
(dest / "app_latest.db.sha256").write_text(db_sha + "\n", encoding="ascii")
(dest / "app_latest.db.gz.sha256").write_text(gz_sha + "\n", encoding="ascii")
(dest / "meta.json").write_text(
    '{"jc_day":"2026-11-20","db_sha256":"%s","gz_sha256":"%s","expected_commit_id":"%s"}' % (db_sha, gz_sha, commit),
    encoding="utf-8",
)
(dest / "app_latest.ready").write_text(
    '{"db_sha256":"%s","gz_sha256":"%s"}' % (db_sha, gz_sha),
    encoding="utf-8",
)
'@
$pyFile = Join-Path ([System.IO.Path]::GetTempPath()) ("fat-iso-" + [guid]::NewGuid().ToString('n') + ".py")
[System.IO.File]::WriteAllText($pyFile, $py, (New-Object System.Text.UTF8Encoding $false))
& $pythonExe $pyFile $DropDir
Remove-Item -LiteralPath $pyFile -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { Fail-Dep "python_fixture $LASTEXITCODE" }
$readyPath = Join-Path $DropDir 'app_latest.ready'
$readyBefore = [System.IO.File]::ReadAllBytes($readyPath)

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) {
  $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $csc)) { Fail-Dep 'csc.exe' }
$cs = Join-Path ([System.IO.Path]::GetTempPath()) ("fat-ssh-" + [guid]::NewGuid().ToString('n') + ".cs")
$fake = Join-Path $env:WINDIR ("Temp\fat-ssh-" + [guid]::NewGuid().ToString('n') + ".exe")
[System.IO.File]::WriteAllText($cs, "class P { static int Main() { System.Threading.Thread.Sleep(7000); return 255; } }`n", (New-Object System.Text.UTF8Encoding $false))
& $csc @('/nologo', "/out:$fake", $cs)
Remove-Item -LiteralPath $cs -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $fake)) { Fail-Dep 'csc.exe' }

$pushScript = Join-Path $here 'push-formal-db.ps1'
$installScript = Join-Path $here 'install_package_atomic.ps1'
$push = Start-FatChild -Argument @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', $pushScript,
  '-Dir', $DropDir,
  '-SshTarget', 'dummy',
  '-SshExe', $fake
)
$install = $null
try {
  Start-Sleep -Seconds 1
  if ($push.Proc.HasExited) { Fail 'push exited before the lock could be contested' }
  $install = Start-FatChild -Argument @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', $installScript,
    '-Dir', $DropDir
  )
  if (-not $install.Proc.WaitForExit(20000)) {
    try { $install.Proc.Kill() } catch {}
    Fail 'install did not finish while push held the lock'
  }
  $installResult = Finish-FatChild $install
  $install = $null
  if ($installResult.Code -ne 11 -or $installResult.Err -notmatch 'DROP_LOCK_HELD') {
    Fail ("install was not blocked by the push lock: exit=" + $installResult.Code + " " + $installResult.Err)
  }
  Write-Output '推送持锁时安装被拒绝'
} finally {
  if ($push -and -not $push.Proc.HasExited) {
    try { [void]$push.Proc.WaitForExit(20000) } catch {}
  }
  if ($push -and -not $push.Proc.HasExited) {
    try { $push.Proc.Kill() } catch {}
    try { [void]$push.Proc.WaitForExit(5000) } catch {}
  }
  Remove-Item -LiteralPath $fake -Force -ErrorAction SilentlyContinue
}
$pushResult = Finish-FatChild $push
if ($pushResult.Code -eq 0) { Fail 'ssh failure was reported as success' }
if (Test-Path -LiteralPath (Join-Path $DropDir 'LAST_PUSHED.json')) { Fail 'ssh failure wrote a receipt' }
$readyAfter = [System.IO.File]::ReadAllBytes($readyPath)
if ([Convert]::ToBase64String($readyBefore) -ne [Convert]::ToBase64String($readyAfter)) {
  Fail 'ssh failure removed or changed ready'
}
Write-Output 'SSH失败未写回执且ready仍在'
$reacquired = Open-FatDropLock -Dir $DropDir
if (-not $reacquired) { Fail 'lock was not released after push exited' }
$reacquired.Dispose()
Write-Output '推送结束后锁可再取得'
Write-Output '辅助锁检查通过'
exit 0
