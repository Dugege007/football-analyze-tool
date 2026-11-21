# PS 5.1 UTF-8 host and a separate powershell.exe -File process.
# Both must deliver the payload bytes unchanged: 7B 22, same length, gz intact.
param(
  [Parameter(Mandatory = $true)][string]$Python,
  [string]$Result = '',
  [switch]$Child
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'windows_argv.ps1')

function Fail([string]$Message) {
  if ($Result) {
    [System.IO.File]::WriteAllText($Result, "FAIL $Message", (New-Object System.Text.UTF8Encoding $false))
  }
  [Console]::Error.WriteLine($Message)
  exit 21
}

function Invoke-FrameCheck([string]$Label) {
  $utf8 = New-Object System.Text.UTF8Encoding $true
  $oldIn = $null
  $oldOut = $null
  try { $oldIn = [Console]::InputEncoding } catch {}
  try { $oldOut = [Console]::OutputEncoding } catch {}
  $work = Join-Path ([System.IO.Path]::GetTempPath()) ("fat-frame-" + [guid]::NewGuid().ToString('n'))
  [System.IO.Directory]::CreateDirectory($work) | Out-Null
  $proc = $null
  try {
    try { chcp 65001 | Out-Null } catch {}
    try { [Console]::InputEncoding = $utf8 } catch {}
    try { [Console]::OutputEncoding = $utf8 } catch {}
    $gz = [byte[]](0x1F, 0x8B, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x01, 0x02, 0x03, 0x04)
    $header = '{"op":"authorize","jc_day":"2026-11-20","db_sha256":"aa","gz_sha256":"bb","expected_commit_id":"baseline-isolation"}' + "`n"
    $headerBytes = (New-Object System.Text.UTF8Encoding $false).GetBytes($header)
    $payload = Join-Path $work 'payload.bin'
    $stream = [System.IO.File]::Open($payload, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try {
      $stream.Write($headerBytes, 0, $headerBytes.Length)
      $stream.Write($gz, 0, $gz.Length)
    } finally { $stream.Dispose() }
    $expect = [System.IO.File]::ReadAllBytes($payload)
    if ($expect.Length -lt 16 -or $expect[0] -ne 0x7B -or $expect[1] -ne 0x22) {
      Fail "$Label payload does not start with 7B22"
    }
    $captured = Join-Path $work 'captured.bin'
    $receiver = Join-Path $work 'recv.py'
    [System.IO.File]::WriteAllText($receiver, "import sys`r`nopen(sys.argv[1],'wb').write(sys.stdin.buffer.read())`n", (New-Object System.Text.UTF8Encoding $false))
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python
    $psi.Arguments = Format-FatWinCommandLine -Argument @($receiver, $captured)
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    Set-FatProcessInputEncoding -StartInfo $psi
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $errTask = $proc.StandardError.ReadToEndAsync()
    Copy-FatRawStdin -Process $proc -PayloadPath $payload
    try { $proc.StandardInput.Close() } catch {}
    [void]$proc.WaitForExit()
    $errText = ''
    try { $errText = $errTask.Result } catch {}
    if ($proc.ExitCode -ne 0) { Fail "$Label receiver exit $($proc.ExitCode) $errText" }
    $got = [System.IO.File]::ReadAllBytes($captured)
    if ($got.Length -ne $expect.Length) { Fail "$Label length $($got.Length) != $($expect.Length)" }
    if ($got[0] -ne 0x7B -or $got[1] -ne 0x22) { Fail "$Label stdin starts with $('{0:X2}{1:X2}' -f $got[0], $got[1])" }
    $newline = [Array]::IndexOf($got, [byte]10)
    if ($newline -lt 0 -or ($got.Length - $newline - 1) -ne $gz.Length) { Fail "$Label gz length" }
    for ($i = 0; $i -lt $gz.Length; $i++) {
      if ($got[$newline + 1 + $i] -ne $gz[$i]) { Fail "$Label gz byte $i" }
    }
    for ($i = 0; $i -lt $expect.Length; $i++) {
      if ($got[$i] -ne $expect[$i]) { Fail "$Label byte $i" }
    }
    Write-Output "$Label first=7B22 len=$($got.Length) gz=OK"
  } finally {
    if ($proc) { try { $proc.Dispose() } catch {} }
    try { if ($oldIn) { [Console]::InputEncoding = $oldIn } } catch {}
    try { if ($oldOut) { [Console]::OutputEncoding = $oldOut } } catch {}
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
  }
}

if ($Child) {
  try {
    Invoke-FrameCheck 'SEPARATE_PROCESS_FRAME_OK'
    if ($Result) { [System.IO.File]::WriteAllText($Result, 'OK', (New-Object System.Text.UTF8Encoding $false)) }
    exit 0
  } catch {
    Fail $_.Exception.Message
  }
}

Invoke-FrameCheck 'UTF8_HOST_FRAME_OK'
$resultFile = Join-Path ([System.IO.Path]::GetTempPath()) ("fat-frame-result-" + [guid]::NewGuid().ToString('n') + '.txt')
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$psi.Arguments = Format-FatWinCommandLine -Argument @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', $PSCommandPath,
  '-Child',
  '-Python', $Python,
  '-Result', $resultFile
)
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$child = New-Object System.Diagnostics.Process
$child.StartInfo = $psi
[void]$child.Start()
$childOut = $child.StandardOutput.ReadToEndAsync()
$childErr = $child.StandardError.ReadToEndAsync()
[void]$child.WaitForExit()
$childText = ''
$childError = ''
try { $childText = $childOut.Result } catch {}
try { $childError = $childErr.Result } catch {}
if ($child.ExitCode -ne 0) { Fail "separate process exit $($child.ExitCode) $childError $childText" }
if (-not (Test-Path -LiteralPath $resultFile)) { Fail 'separate process wrote no result' }
$mark = [System.IO.File]::ReadAllText($resultFile).Trim()
Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
if ($mark -ne 'OK') { Fail "separate process result $mark" }
Write-Output $childText.Trim()
Write-Output 'STDIN_FRAME_OK'
exit 0
