# YuePC 优先。MSI 备用脚本只换投放目录后调用本文件。
# 本机锁是 Windows FileShare.None，从第一次读包之前持有到结束。
# 服务器事务仍由 ssh.exe 把原始字节送进远端 bash/flock。不用本机 flock，也不把载荷送进文本管道。
param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_YPC_DROP) { $env:AUTO_CN_PUSH_YPC_DROP } else { 'C:\Users\Administrator\football-cn-auto-push' }),
  [string]$Key = $env:FAT_CN_SSH_KEY,
  [string]$SshTarget = $env:FAT_CN_SSH_TARGET,
  [string]$SshExe = $(if ($env:FAT_CN_SSH_EXE) { $env:FAT_CN_SSH_EXE } else { 'ssh.exe' }),
  [switch]$SkipReadyCheck
)
$ErrorActionPreference = 'Stop'

function Exit-Fat([int]$Code, [string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit $Code
}

if (-not $SshTarget -and $env:FAT_CN_ALLOW_TEST_LOCAL -ne '1') {
  Exit-Fat 2 'SSH_TARGET_REQUIRED'
}
$sshCmd = Get-Command $SshExe -ErrorAction SilentlyContinue
if (-not $sshCmd) {
  Exit-Fat 20 "DEPENDENCY_MISSING ssh $($SshExe)"
}
$sshPath = $sshCmd.Source

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'windows_drop_lock.ps1')
. (Join-Path $here 'windows_argv.ps1')
$dropLock = Open-FatDropLock -Dir $Dir
if (-not $dropLock) {
  Exit-Fat 11 'DROP_LOCK_HELD'
}

function Quote-Bash([string]$Text) {
  return "'" + ($Text -replace "'", "'\''") + "'"
}

function Get-FatSha256([byte[]]$Bytes) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Get-FatGzipCommitId([byte[]]$Blob) {
  if ($Blob.Length -lt 10 -or $Blob[0] -ne 0x1F -or $Blob[1] -ne 0x8B) { return '' }
  $flag = $Blob[3]
  $pos = 10
  if (($flag -band 4) -ne 0) {
    $extra = [BitConverter]::ToUInt16($Blob, $pos)
    $pos += 2 + $extra
  }
  if (($flag -band 8) -ne 0) {
    while ($pos -lt $Blob.Length -and $Blob[$pos] -ne 0) { $pos++ }
    $pos++
  }
  if (($flag -band 16) -ne 0) {
    $end = $pos
    while ($end -lt $Blob.Length -and $Blob[$end] -ne 0) { $end++ }
    return [System.Text.Encoding]::ASCII.GetString($Blob, $pos, $end - $pos)
  }
  return ''
}

function Read-FatText([string]$Path) {
  return [System.IO.File]::ReadAllText($Path)
}

try {
  $gzPath = Join-Path $Dir 'app_latest.db.gz'
  $shaPath = Join-Path $Dir 'app_latest.db.sha256'
  $gzShaPath = Join-Path $Dir 'app_latest.db.gz.sha256'
  $readyPath = Join-Path $Dir 'app_latest.ready'
  $metaPath = Join-Path $Dir 'meta.json'
  $receiptPath = Join-Path $Dir 'LAST_PUSHED.json'
  foreach ($need in @($gzPath, $shaPath)) {
    if (-not (Test-Path -LiteralPath $need)) { Exit-Fat 3 "missing $need" }
  }
  $gzBytes = [System.IO.File]::ReadAllBytes($gzPath)
  $dbSha = (Read-FatText $shaPath).Split()[0].Trim().ToLowerInvariant()
  $gzSha = Get-FatSha256 $gzBytes
  if (Test-Path -LiteralPath $gzShaPath) {
    $want = (Read-FatText $gzShaPath).Split()[0].Trim().ToLowerInvariant()
    if ($want -ne $gzSha) { Exit-Fat 4 'gz sha mismatch' }
  }
  if (-not $SkipReadyCheck) {
    if (-not (Test-Path -LiteralPath $readyPath)) { Exit-Fat 3 'missing ready' }
    $ready = (Read-FatText $readyPath) | ConvertFrom-Json
    $readyDb = [string]$ready.db_sha256
    $readyGz = [string]$ready.gz_sha256
    if ($readyDb -and $readyDb.ToLowerInvariant() -ne $dbSha) { Exit-Fat 4 'ready db mismatch' }
    if ($readyGz -and $readyGz.ToLowerInvariant() -ne $gzSha) { Exit-Fat 4 'ready gz mismatch' }
  }
  $jc = 'unknown'
  $metaId = ''
  if (Test-Path -LiteralPath $metaPath) {
    $meta = (Read-FatText $metaPath) | ConvertFrom-Json
    if ($meta.jc_day) { $jc = [string]$meta.jc_day }
    $metaId = [string]$meta.expected_commit_id
    if ($meta.db_sha256 -and ([string]$meta.db_sha256).ToLowerInvariant() -ne $dbSha) { Exit-Fat 18 'PACKAGE_UNBOUND' }
    if ($meta.gz_sha256 -and ([string]$meta.gz_sha256).ToLowerInvariant() -ne $gzSha) { Exit-Fat 18 'PACKAGE_UNBOUND' }
  }
  $bound = Get-FatGzipCommitId $gzBytes
  if ($metaId -ne $bound) { Exit-Fat 18 'PACKAGE_UNBOUND' }

  $scriptPath = Join-Path $here 'cn_push_transaction.sh'
  if (-not (Test-Path -LiteralPath $scriptPath)) { Exit-Fat 20 'DEPENDENCY_MISSING cn_push_transaction.sh' }
  $scriptText = [System.IO.File]::ReadAllText($scriptPath)
  $scriptB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($scriptText))
  $root = if ($env:FAT_CN_ROOT) { $env:FAT_CN_ROOT } else { '/opt/football-data' }
  $lock = if ($env:FAT_CN_LOCK) { $env:FAT_CN_LOCK } else { '/run/fat-cn-push.lock' }
  $pause = if ($env:FAT_CN_PAUSE_SEC) { $env:FAT_CN_PAUSE_SEC } else { '' }
  $activate = if ($env:FAT_CN_ACTIVATE_CMD) { $env:FAT_CN_ACTIVATE_CMD } else { '' }
  $rollback = if ($env:FAT_CN_ROLLBACK_CMD) { $env:FAT_CN_ROLLBACK_CMD } else { '' }
  $inner = @"
set -euo pipefail
script=`$(mktemp)
printf '%s' $scriptB64 | base64 -d > "`$script"
LOCK=$(Quote-Bash $lock)
mkdir -p "`$(dirname "`$LOCK")"
exec 8>"`$LOCK"
if ! flock -n 8; then echo REMOTE_PUSH_LOCK_HELD >&2; exit 9; fi
bash "`$script"
rc=`$?
rm -f "`$script"
exit `$rc
"@
  $remote = @(
    "FAT_CN_ROOT=$(Quote-Bash $root)",
    "FAT_CN_PAUSE_SEC=$(Quote-Bash $pause)",
    "FAT_CN_ACTIVATE_CMD=$(Quote-Bash $activate)",
    "FAT_CN_ROLLBACK_CMD=$(Quote-Bash $rollback)",
    "bash -c $(Quote-Bash $inner)"
  ) -join ' '

  function Send-FatSsh([string]$Op) {
    $headerObj = [ordered]@{
      op = $Op
      jc_day = $jc
      db_sha256 = $dbSha
      gz_sha256 = $gzSha
      expected_commit_id = $bound
    }
    $header = ($headerObj | ConvertTo-Json -Compress) + "`n"
    $payload = Join-Path $Dir (".payload-" + [guid]::NewGuid().ToString('n'))
    $proc = $null
    $started = $false
    $inputStream = $null
    $stdoutTask = $null
    $stderrTask = $null
    $failure = $null
    $code = 1
    $out = ''
    $errText = ''
    try {
      $outFile = [System.IO.File]::Open($payload, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
      try {
        $headerBytes = New-Object System.Text.UTF8Encoding $false
        $hb = $headerBytes.GetBytes($header)
        $outFile.Write($hb, 0, $hb.Length)
        $outFile.Write($gzBytes, 0, $gzBytes.Length)
      } finally {
        $outFile.Dispose()
      }
      $psi = New-Object System.Diagnostics.ProcessStartInfo
      $psi.FileName = $sshPath
      $psi.UseShellExecute = $false
      $psi.RedirectStandardInput = $true
      $psi.RedirectStandardOutput = $true
      $psi.RedirectStandardError = $true
      $psi.CreateNoWindow = $true
      $psi.Arguments = Format-FatWinCommandLine -Argument ($(
        $sshArg = @('-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=30')
        if ($Key) { $sshArg += @('-i', $Key) }
        $sshArg += @($SshTarget, $remote)
        $sshArg
      ))
      $proc = New-Object System.Diagnostics.Process
      $proc.StartInfo = $psi
      [void]$proc.Start()
      $started = $true
      $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
      $stderrTask = $proc.StandardError.ReadToEndAsync()
      $inputStream = [System.IO.File]::OpenRead($payload)
      $inputStream.CopyTo($proc.StandardInput.BaseStream)
    } catch {
      $failure = $_
    } finally {
      if ($inputStream) {
        try { $inputStream.Dispose() } catch {}
      }
      if ($started -and $proc) {
        try { $proc.StandardInput.Close() } catch {}
        if ($failure) {
          if (-not $proc.HasExited) { try { $proc.Kill() } catch {} }
          try { [void]$proc.WaitForExit(15000) } catch {}
        } else {
          try { [void]$proc.WaitForExit() } catch { if (-not $failure) { $failure = $_ } }
        }
        if (-not $proc.HasExited) {
          try { $proc.Kill() } catch {}
          try { [void]$proc.WaitForExit(5000) } catch {}
        }
        try { $code = $proc.ExitCode } catch {}
      }
      if ($stdoutTask) { try { $out = $stdoutTask.Result } catch {} }
      if ($stderrTask) { try { $errText = $stderrTask.Result } catch {} }
      if ($proc) {
        try { $proc.Dispose() } catch {}
      }
      if ($payload -and (Test-Path -LiteralPath $payload)) {
        Remove-Item -LiteralPath $payload -Force -ErrorAction SilentlyContinue
      }
    }
    if ($failure) { throw $failure }
    return @{ Code = $code; Out = $out; Err = $errText }
  }

  $authorized = Send-FatSsh 'authorize'
  if ($authorized.Err) { [Console]::Error.Write($authorized.Err) }
  if ($authorized.Code -ne 0) { exit $authorized.Code }
  if ($authorized.Out -notmatch 'BASELINE_JSON ') { Exit-Fat 5 'missing server baseline' }
  $committed = Send-FatSsh 'commit'
  if ($committed.Err) { [Console]::Error.Write($committed.Err) }
  if ($committed.Code -ne 0) { exit $committed.Code }
  $line = ''
  foreach ($rawLine in ($committed.Out -split "`n")) {
    if ($rawLine.StartsWith('COMMIT_JSON ')) { $line = $rawLine.Substring(12) }
  }
  if (-not $line) { Exit-Fat 5 'missing server commit' }
  $commit = $line | ConvertFrom-Json
  if ([string]$commit.db_sha256 -ne $dbSha -or [string]$commit.gz_sha256 -ne $gzSha) { Exit-Fat 5 'server commit does not match this package' }
  if ($commit.health_ok -ne $true -or $commit.activated -ne $true) { Exit-Fat 5 'server commit does not match this package' }
  if ([string]$commit.served_db_sha256 -ne $dbSha) { Exit-Fat 5 'server commit does not match this package' }
  if ([string]$commit.predecessor_commit_id -ne $bound) { Exit-Fat 5 'server commit does not match this package' }
  $tmpReceipt = $receiptPath + '.tmp'
  [System.IO.File]::WriteAllText($tmpReceipt, ($commit | ConvertTo-Json -Depth 6) + "`n", (New-Object System.Text.UTF8Encoding $false))
  if (Test-Path -LiteralPath $receiptPath) {
    Copy-Item -LiteralPath $receiptPath -Destination ($receiptPath + '.previous') -Force
  }
  Move-Item -LiteralPath $tmpReceipt -Destination $receiptPath -Force
  if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
  Write-Output 'PUSH_COMMITTED'
} finally {
  if ($dropLock) { $dropLock.Dispose() }
}
