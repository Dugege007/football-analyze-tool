# Atomic install of formal-DB package into drop dir.
# Prevents "meta new / gz old" by staging into incoming, verifying, then swapping.
# Does NOT push to CN. Writes app_latest.ready only after verified swap.
param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_YPC_DROP) { $env:AUTO_CN_PUSH_YPC_DROP } else { 'C:\Users\Administrator\football-cn-auto-push' }),
  [string]$IncomingDir = '',
  [string]$ChunksDir = '',
  [switch]$DisableTaskDuringInstall,
  [string]$TaskName = 'FootballAutoCnPushAfterClose'
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bash = if ($env:FAT_CN_BASH) { $env:FAT_CN_BASH } else { 'bash' }
$hold = Join-Path $here 'hold_drop_lock.sh'
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $bash
$psi.Arguments = '"' + $hold + '" "' + $Dir + '"'
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$lockProc = New-Object System.Diagnostics.Process
$lockProc.StartInfo = $psi
if (-not $lockProc.Start()) { throw 'cannot start drop lock' }
$got = $lockProc.StandardOutput.ReadLine()
if ($got -ne 'DROP_LOCK_HELD_OK') {
  try { $lockProc.StandardInput.Close() } catch {}
  $err = ''
  try { $err = $lockProc.StandardError.ReadToEnd() } catch {}
  throw "drop lock held: $err"
}

function Write-FatEvent([string]$Event, [string]$Detail = '') {
  $log = Join-Path $Dir 'auto-push-events.log'
  $line = "{0}`t{1}`t{2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Event, $Detail
  Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

function Get-ShaFile([string]$Path) {
  return ((Get-Content -LiteralPath $Path -Raw).Split()[0]).Trim().ToLower()
}

if (-not (Test-Path -LiteralPath $Dir)) {
  New-Item -ItemType Directory -Path $Dir | Out-Null
}

$taskWasDisabled = $false
if ($DisableTaskDuringInstall) {
  try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
      Disable-ScheduledTask -TaskName $TaskName | Out-Null
      $taskWasDisabled = $true
      Write-FatEvent 'task_disable' $TaskName
      $deadline = (Get-Date).AddSeconds(120)
      do {
        $task = Get-ScheduledTask -TaskName $TaskName
        if ($task.State -ne 'Running') { break }
        Start-Sleep -Seconds 2
      } while ((Get-Date) -lt $deadline)
      $task = Get-ScheduledTask -TaskName $TaskName
      if ($task.State -eq 'Running') {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        try { $lockProc.StandardInput.Close() } catch {}
        throw 'task still running after disable; refused to swap'
      }
    }
  } catch {
    if ($_.Exception.Message -match 'task still running') { throw }
  }
}

try {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  if (-not $IncomingDir) {
    $IncomingDir = Join-Path $Dir ("incoming-" + $stamp)
  }
  if (-not (Test-Path -LiteralPath $IncomingDir)) {
    New-Item -ItemType Directory -Path $IncomingDir | Out-Null
  }

  if ($ChunksDir) {
    $manifestPath = Join-Path $ChunksDir 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "missing $manifestPath" }
    $man = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $outGz = Join-Path $IncomingDir 'app_latest.db.gz'
    if (Test-Path -LiteralPath $outGz) { Remove-Item -LiteralPath $outGz -Force }
    $fs = [System.IO.File]::Open($outGz, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try {
      foreach ($part in $man.parts) {
        $p = Join-Path $ChunksDir $part
        if (-not (Test-Path -LiteralPath $p)) { throw "missing chunk $p" }
        $bytes = [System.IO.File]::ReadAllBytes($p)
        $fs.Write($bytes, 0, $bytes.Length)
      }
    } finally { $fs.Close() }
    Copy-Item -LiteralPath (Join-Path $ChunksDir 'app_latest.db.gz.sha256') -Destination (Join-Path $IncomingDir 'app_latest.db.gz.sha256') -Force
    Copy-Item -LiteralPath (Join-Path $ChunksDir 'app_latest.db.sha256') -Destination (Join-Path $IncomingDir 'app_latest.db.sha256') -Force
    Copy-Item -LiteralPath (Join-Path $ChunksDir 'meta.json') -Destination (Join-Path $IncomingDir 'meta.json') -Force
    Write-FatEvent 'assemble_ok' ("parts=" + $man.n + " gz=" + (Get-Item $outGz).Length)
  }

  $inGz = Join-Path $IncomingDir 'app_latest.db.gz'
  $inGzSha = Join-Path $IncomingDir 'app_latest.db.gz.sha256'
  $inDbSha = Join-Path $IncomingDir 'app_latest.db.sha256'
  $inMeta = Join-Path $IncomingDir 'meta.json'
  foreach ($f in @($inGz, $inGzSha, $inDbSha, $inMeta)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "incoming incomplete missing $f" }
  }

  $wantGz = Get-ShaFile $inGzSha
  $wantDb = Get-ShaFile $inDbSha
  $gotGz = (Get-FileHash -LiteralPath $inGz -Algorithm SHA256).Hash.ToLower()
  if ($gotGz -ne $wantGz) { throw "incoming gz sha mismatch got=$gotGz want=$wantGz" }

  $meta = Get-Content -LiteralPath $inMeta -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($meta.gz_sha256 -and ($meta.gz_sha256.ToString().ToLower() -ne $wantGz)) {
    throw "meta.gz_sha256 != sidecar want_meta=$($meta.gz_sha256) sidecar=$wantGz"
  }
  if ($meta.db_sha256 -and ($meta.db_sha256.ToString().ToLower() -ne $wantDb)) {
    throw "meta.db_sha256 != sidecar"
  }

  # Remove any prior .ready / expect before swap so watch cannot push mid-swap or an old pair.
  $readyPath = Join-Path $Dir 'app_latest.ready'
  $expectPath = Join-Path $Dir 'pending_expect.json'
  if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
  if (Test-Path -LiteralPath $expectPath) { Remove-Item -LiteralPath $expectPath -Force }

  $destGzNew = Join-Path $Dir 'app_latest.db.gz.new'
  Copy-Item -LiteralPath $inGz -Destination $destGzNew -Force
  Copy-Item -LiteralPath $inGzSha -Destination (Join-Path $Dir 'app_latest.db.gz.sha256') -Force
  Copy-Item -LiteralPath $inDbSha -Destination (Join-Path $Dir 'app_latest.db.sha256') -Force
  Copy-Item -LiteralPath $inMeta -Destination (Join-Path $Dir 'meta.json') -Force
  Move-Item -LiteralPath $destGzNew -Destination (Join-Path $Dir 'app_latest.db.gz') -Force

  $finalGot = (Get-FileHash -LiteralPath (Join-Path $Dir 'app_latest.db.gz') -Algorithm SHA256).Hash.ToLower()
  if ($finalGot -ne $wantGz) { throw "post-swap gz sha mismatch got=$finalGot want=$wantGz" }

  $jc = if ($meta.jc_day) { $meta.jc_day } else { 'unknown' }
  $ready = [ordered]@{
    jc_day = $jc
    db_sha256 = $wantDb
    gz_sha256 = $wantGz
    ready_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    incoming = $IncomingDir
    gz_bytes = (Get-Item (Join-Path $Dir 'app_latest.db.gz')).Length
  }
  $expect = [ordered]@{
    jc_day = $jc
    db_sha256 = $wantDb
    gz_sha256 = $wantGz
  }
  ($expect | ConvertTo-Json) | Set-Content -LiteralPath $expectPath -Encoding UTF8
  ($ready | ConvertTo-Json) | Set-Content -LiteralPath $readyPath -Encoding UTF8
  Write-FatEvent 'atomic_install_ok' ("jc_day=$jc gz=$wantGz")
  Write-Host ("ATOMIC_INSTALL_OK jc_day=$jc gz_sha=$wantGz ready=$readyPath")
}
finally {
  if ($lockProc -and -not $lockProc.HasExited) {
    try { $lockProc.StandardInput.Close() } catch {}
    $lockProc.WaitForExit(5000) | Out-Null
  }
  if ($taskWasDisabled) {
    try {
      schtasks /Change /TN $TaskName /ENABLE | Out-Null
      Write-FatEvent 'task_enable' $TaskName
    } catch {}
  }
}
