# Poll drop dir; push only when app_latest.ready exists and package hashes match.
# Consecutive mismatch / push failure → ALERT_NEED_ANALYST.json for analyst routine.
param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_YPC_DROP) { $env:AUTO_CN_PUSH_YPC_DROP } else { 'C:\Users\Administrator\football-cn-auto-push' }),
  [int]$MismatchAlertAfter = 3
)
$ErrorActionPreference = 'Stop'
$EventLog = Join-Path $Dir 'auto-push-events.log'
$AlertState = Join-Path $Dir 'ALERT_STATE.json'
$AlertFlag = Join-Path $Dir 'ALERT_NEED_ANALYST.json'
$ReadyPath = Join-Path $Dir 'app_latest.ready'

function Write-FatEvent([string]$Event, [string]$Detail = '') {
  $line = "{0}`t{1}`t{2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Event, $Detail
  Add-Content -LiteralPath $EventLog -Value $line -Encoding UTF8
}

function Read-AlertState {
  if (-not (Test-Path -LiteralPath $AlertState)) {
    return @{ consecutive_mismatch = 0; consecutive_fail = 0; last_error = $null; updated_at = $null }
  }
  try { return (Get-Content -LiteralPath $AlertState -Raw -Encoding UTF8 | ConvertFrom-Json) }
  catch { return @{ consecutive_mismatch = 0; consecutive_fail = 0; last_error = $null; updated_at = $null } }
}

function Write-AlertState($obj) {
  $obj.updated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
  ($obj | ConvertTo-Json) | Set-Content -LiteralPath $AlertState -Encoding UTF8
}

function Raise-AnalystAlert([string]$Kind, [string]$Detail, $state) {
  $payload = [ordered]@{
    kind = $Kind
    detail = $Detail
    consecutive_mismatch = [int]$state.consecutive_mismatch
    consecutive_fail = [int]$state.consecutive_fail
    raised_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    note = 'Notify analyst private chat (ypc-cn routine). Do not mark-pushed.'
  }
  ($payload | ConvertTo-Json) | Set-Content -LiteralPath $AlertFlag -Encoding UTF8
  Write-FatEvent 'alert_raised' ("kind=$Kind $Detail")
}

function Clear-AnalystAlertIfOk {
  if (Test-Path -LiteralPath $AlertFlag) { Remove-Item -LiteralPath $AlertFlag -Force }
  Write-AlertState @{ consecutive_mismatch = 0; consecutive_fail = 0; last_error = $null; updated_at = $null }
}

$push = Join-Path $Dir 'push-formal-db.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $Dir 'app_latest.db.gz'))) {
  Write-FatEvent 'skip' 'no package yet'
  exit 0
}
if (-not (Test-Path -LiteralPath $ReadyPath)) {
  Write-FatEvent 'skip_no_ready' 'missing app_latest.ready — refuse push until atomic install'
  exit 0
}
if (-not (Test-Path -LiteralPath $push)) {
  Write-FatEvent 'error' "missing $push - copy scripts into drop dir first"
  exit 0
}

Write-FatEvent 'start' "pid=$PID"
try {
  & $push -Dir $Dir
  $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
  if ($code -eq 0) {
    Clear-AnalystAlertIfOk
    Write-FatEvent 'done' "exit=$code"
  } else {
    $st = Read-AlertState
    $st.consecutive_fail = [int]$st.consecutive_fail + 1
    $st.last_error = "push exit=$code"
    Write-AlertState $st
    if ([int]$st.consecutive_fail -ge $MismatchAlertAfter) {
      Raise-AnalystAlert 'push_fail' "exit=$code" $st
    }
    Write-FatEvent 'done' "exit=$code"
  }
  exit $code
} catch {
  $msg = ($_.Exception.Message -replace '[\r\n]+', ' ').Trim()
  Write-FatEvent 'error' $msg
  $st = Read-AlertState
  if ($msg -match 'gz sha mismatch|sha mismatch|missing app_latest.ready|ready gz mismatch') {
    $st.consecutive_mismatch = [int]$st.consecutive_mismatch + 1
    $st.last_error = $msg
    Write-AlertState $st
    if ([int]$st.consecutive_mismatch -ge $MismatchAlertAfter) {
      Raise-AnalystAlert 'mismatch' $msg $st
    }
  } else {
    $st.consecutive_fail = [int]$st.consecutive_fail + 1
    $st.last_error = $msg
    Write-AlertState $st
    if ([int]$st.consecutive_fail -ge $MismatchAlertAfter) {
      Raise-AnalystAlert 'push_fail' $msg $st
    }
  }
  throw
}
