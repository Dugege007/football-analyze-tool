# Push staging formal DB gz to CN. Prefer YuePC. SSH target/key come from env (see MANIFEST).
# Expects in $Dir: app_latest.db.gz, *.sha256, meta.json, AND app_latest.ready
# (written only by install_package_atomic.ps1 after verified swap).
# Does NOT touch /opt/football-data/env (LIVE_CAPTURE stays off).
param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_YPC_DROP) { $env:AUTO_CN_PUSH_YPC_DROP } else { 'C:\Users\Administrator\football-cn-auto-push' }),
  [string]$Key = $env:FAT_CN_SSH_KEY,  # redacted: default key path removed; set FAT_CN_SSH_KEY at runtime (do not record the value here)
  [string]$SshTarget = $env:FAT_CN_SSH_TARGET,  # redacted: default SSH target removed; set FAT_CN_SSH_TARGET at runtime (do not record the value here)
  [switch]$SkipReadyCheck
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Key)) { throw "missing ssh key $Key" }
$LocalGz = Join-Path $Dir 'app_latest.db.gz'
$ShaFile = Join-Path $Dir 'app_latest.db.sha256'
$GzShaFile = Join-Path $Dir 'app_latest.db.gz.sha256'
$MetaFile = Join-Path $Dir 'meta.json'
$ReadyFile = Join-Path $Dir 'app_latest.ready'
$MarkerDone = Join-Path $Dir 'LAST_PUSHED.json'
if (-not (Test-Path $LocalGz)) { throw "missing $LocalGz" }
if (-not (Test-Path $ShaFile)) { throw "missing $ShaFile" }
$ExpectedSha = ((Get-Content $ShaFile -Raw).Split()[0]).Trim().ToLower()
$ExpectedGzSha = $null
if (Test-Path $GzShaFile) {
  $ExpectedGzSha = ((Get-Content $GzShaFile -Raw).Split()[0]).Trim().ToLower()
}
$gotGz = (Get-FileHash $LocalGz -Algorithm SHA256).Hash.ToLower()
Write-Host ("gz sha=$gotGz size=" + (Get-Item $LocalGz).Length)
if ($ExpectedGzSha -and $gotGz -ne $ExpectedGzSha) { throw "gz sha mismatch want=$ExpectedGzSha" }

if (-not $SkipReadyCheck) {
  if (-not (Test-Path $ReadyFile)) { throw "missing app_latest.ready — run install_package_atomic.ps1 first" }
  try {
    $ready = Get-Content $ReadyFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($ready.gz_sha256 -and ($ready.gz_sha256.ToString().ToLower() -ne $gotGz)) {
      throw "ready gz mismatch ready=$($ready.gz_sha256) got=$gotGz"
    }
    if ($ready.db_sha256 -and ($ready.db_sha256.ToString().ToLower() -ne $ExpectedSha)) {
      throw "ready db mismatch ready=$($ready.db_sha256) want=$ExpectedSha"
    }
  } catch {
    if ($_.Exception.Message -match 'ready |missing app_latest') { throw }
    throw "app_latest.ready unreadable: $($_.Exception.Message)"
  }
}

# 有 pending_expect 时，磁盘上的包必须就是这次 pending。旧包即使自洽也拒绝。
$ExpectFile = Join-Path $Dir 'pending_expect.json'
if (Test-Path $ExpectFile) {
  try {
    $exp = Get-Content $ExpectFile -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    throw "pending_expect.json unreadable: $($_.Exception.Message)"
  }
  if ($exp.gz_sha256 -and ($exp.gz_sha256.ToString().ToLower() -ne $gotGz)) {
    throw "pending_expect gz mismatch expect=$($exp.gz_sha256) got=$gotGz (refuse old package)"
  }
  if ($exp.db_sha256 -and ($exp.db_sha256.ToString().ToLower() -ne $ExpectedSha)) {
    throw "pending_expect db mismatch expect=$($exp.db_sha256) want=$ExpectedSha (refuse old package)"
  }
}

$dropLockPath = Join-Path $Dir 'drop.operation.lock'
$dropLock = $null
try {
  $dropLock = [System.IO.File]::Open($dropLockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
  throw "drop lock held"
}

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = "/root/fat-deploy-backups/db-auto-close-$Stamp"
$RemoteTmp = "/opt/football-data/app_latest.db.incoming-$Stamp"
$RemoteGz = "/opt/football-data/app_latest.db.gz.incoming-$Stamp"
$RemoteFinal = '/opt/football-data/app_latest.db'
$JcDay = 'unknown'
if (Test-Path $MetaFile) {
  try { $JcDay = (Get-Content $MetaFile -Raw | ConvertFrom-Json).jc_day } catch {}
}

function Ssh([string]$RemoteCmd) {
  Write-Host ("SSH> " + $RemoteCmd)
  & ssh.exe -o BatchMode=yes -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new -i $Key $SshTarget $RemoteCmd
  if ($LASTEXITCODE -ne 0) { throw ("ssh exit " + $LASTEXITCODE) }
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($Path, $Text, $utf8)
}

Write-Host '=== pre: remote db sha ==='
$remoteText = Ssh "flock -n /run/fat-cn-push.lock -c 'sha256sum /opt/football-data/app_latest.db'"
$remoteDb = (($remoteText -split '\s+')[0]).ToLower()
if ($remoteDb -eq $ExpectedSha) {
  $health = Ssh "flock -n /run/fat-cn-push.lock -c 'curl -fsS http://127.0.0.1:8787/health'"
  $healthObj = $health | ConvertFrom-Json
  if ($healthObj.ok -ne $true) { throw "remote health failed" }
  $prevGz = $null
  if (Test-Path $MarkerDone) {
    try {
      $prev = Get-Content $MarkerDone -Raw -Encoding UTF8 | ConvertFrom-Json
      $prevGz = [string]$prev.gz_sha256
    } catch {}
  }
  if ($prevGz -and $prevGz.ToLower() -eq $gotGz) {
    if (Test-Path $ReadyFile) { Remove-Item $ReadyFile -Force }
    Write-Host "SKIP_SAME_DB_AND_GZ"
    if ($dropLock) { $dropLock.Dispose() }
    exit 0
  }
  if ($dropLock) { $dropLock.Dispose() }
  throw "GZ_NOT_THE_DELIVERED_PACKAGE remote db matches but this gz was not the delivered package; LAST_PUSHED was not modified"
}

Write-Host '=== backup remote ==='
Ssh ("mkdir -p " + $BackupDir)
Ssh ("cp -a " + $RemoteFinal + " " + $BackupDir + "/app_latest.db")
Ssh ("sha256sum " + $BackupDir + "/app_latest.db; ls -la " + $BackupDir)

Write-Host '=== scp gz + expand ==='
& scp.exe -o BatchMode=yes -o ConnectTimeout=300 -o StrictHostKeyChecking=accept-new -i $Key $LocalGz ($SshTarget + ':' + $RemoteGz)
if ($LASTEXITCODE -ne 0) { throw ('scp gz exit ' + $LASTEXITCODE) }
Ssh ("gzip -dc " + $RemoteGz + " > " + $RemoteTmp)
Ssh ("rm -f " + $RemoteGz + "; ls -la " + $RemoteTmp)

Write-Host '=== atomic replace + restart ==='
$remoteScript = @"
set -e
exec 8>/run/fat-cn-push.lock
flock -n 8 || { echo REMOTE_PUSH_LOCK_HELD; exit 9; }
IN=$RemoteTmp
FINAL=$RemoteFinal
EXPECTED=$ExpectedSha
test -f "`$IN"
GOT=`$(sha256sum "`$IN" | cut -d' ' -f1)
echo GOT=`$GOT
test "`$GOT" = "`$EXPECTED"
mv -f "`$IN" "`$FINAL"
chown football:football "`$FINAL"
chmod 600 "`$FINAL"
sha256sum "`$FINAL"
ls -la "`$FINAL"
"@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
Ssh ("echo $b64 | base64 -d > /tmp/fat-finish-replace.sh && bash /tmp/fat-finish-replace.sh && rm -f /tmp/fat-finish-replace.sh")
Ssh 'systemctl restart football-analyze-api'
Start-Sleep -Seconds 6
Ssh 'systemctl is-active football-analyze-api'
$health = Ssh 'curl -fsS http://127.0.0.1:8787/health'
$healthObj = $health | ConvertFrom-Json
if ($healthObj.ok -ne $true) { throw "remote health failed after replace" }
$postText = Ssh 'sha256sum /opt/football-data/app_latest.db'
$postDb = (($postText -split '\s+')[0]).ToLower()
if ($postDb -ne $ExpectedSha) { throw "remote db after replace is $postDb" }

# 健康且远端哈希一致之后才写回执。不用会写 BOM 的 Set-Content -Encoding UTF8。
$done = [ordered]@{
  jc_day = $JcDay
  pushed_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
  db_sha256 = $ExpectedSha
  gz_sha256 = $gotGz
  backup = $BackupDir
  health_ok = $true
}
Write-Utf8NoBom $MarkerDone (($done | ConvertTo-Json) + "`n")
if (Test-Path $ReadyFile) { Remove-Item $ReadyFile -Force }
Write-Host ("=== PUSH OK jc_day=$JcDay db_sha=$ExpectedSha backup=$BackupDir ===")
Write-Host 'NOTE: also run on box: python3 run_auto_cn_push.py --mark-pushed <jc_day> --receipt LAST_PUSHED.json'

Write-Host '=== LIVE_CAPTURE still off? (non-fatal) ==='
try {
  & ssh.exe -o BatchMode=yes -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new -i $Key $SshTarget 'grep -i LIVE_CAPTURE /opt/football-data/env 2>/dev/null || echo no-LIVE_CAPTURE-in-env'
} catch {
  Write-Host ("LIVE_CAPTURE probe warning: " + $_.Exception.Message)
}
if ($dropLock) { $dropLock.Dispose() }
