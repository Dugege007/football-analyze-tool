# 在 YuePC / MSI 上检查本机文件锁、安装/推送入口交错，以及 SSH 失败时不写回执。
# 不连接生产服务器，不安装功能，不开端口。成功时只打印「辅助锁检查通过」。
param(
  [Parameter(Mandatory = $true)][string]$DropDir,
  [Parameter(Mandatory = $true)][string]$Python
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'windows_drop_lock.ps1')

function Fail([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 21
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
& $exe $pyFile $DropDir
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
$pushOut = Join-Path $DropDir 'push-out.txt'
$pushErr = Join-Path $DropDir 'push-err.txt'
$installOut = Join-Path $DropDir 'install-out.txt'
$installErr = Join-Path $DropDir 'install-err.txt'
$pushArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Dir "{1}" -SshTarget dummy -SshExe "{2}"' -f (
  $pushScript -replace '"', '\"'
), (
  $DropDir -replace '"', '\"'
), (
  $fake -replace '"', '\"'
)
$push = Start-Process -FilePath 'powershell.exe' -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $pushOut -RedirectStandardError $pushErr -ArgumentList $pushArgs
$install = $null
try {
  Start-Sleep -Seconds 1
  if ($push.HasExited) { Fail 'push exited before the lock could be contested' }
  $installArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Dir "{1}"' -f (
    ($installScript -replace '"', '\"')
  ), (
    ($DropDir -replace '"', '\"')
  )
  $install = Start-Process -FilePath 'powershell.exe' -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $installOut -RedirectStandardError $installErr -ArgumentList $installArgs
  if (-not $install.WaitForExit(20000)) {
    try { $install.Kill() } catch {}
    Fail 'install did not finish while push held the lock'
  }
  $installText = ''
  if (Test-Path -LiteralPath $installErr) { $installText = [System.IO.File]::ReadAllText($installErr) }
  if ($install.ExitCode -ne 11 -or $installText -notmatch 'DROP_LOCK_HELD') {
    Fail "install was not blocked by the push lock: exit=$($install.ExitCode) $installText"
  }
  Write-Output '推送持锁时安装被拒绝'
} finally {
  if ($push -and -not $push.HasExited) {
    try { $push.WaitForExit(20000) } catch {}
  }
  if ($push -and -not $push.HasExited) {
    try { $push.Kill() } catch {}
    try { $push.WaitForExit(5000) } catch {}
  }
  Remove-Item -LiteralPath $fake -Force -ErrorAction SilentlyContinue
}
if ($push.ExitCode -eq 0) { Fail 'ssh failure was reported as success' }
if (Test-Path -LiteralPath (Join-Path $DropDir 'LAST_PUSHED.json')) { Fail 'ssh failure wrote a receipt' }
$readyAfter = [System.IO.File]::ReadAllBytes($readyPath)
if ([Convert]::ToBase64String($readyBefore) -ne [Convert]::ToBase64String($readyAfter)) {
  Fail 'ssh failure removed or changed ready'
}
Write-Output 'SSH失败未写回执且ready仍在'
$reacquired = Open-FatDropLock -Dir $DropDir
if (-not $reacquired) { Fail 'lock was not released after push exited' }
$reacquired.Dispose()
Remove-Item -LiteralPath $fake -Force -ErrorAction SilentlyContinue
Write-Output '推送结束后锁可再取得'
Write-Output '辅助锁检查通过'
exit 0
