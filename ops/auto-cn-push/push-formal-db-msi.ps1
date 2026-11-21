param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_MSI_DROP) { $env:AUTO_CN_PUSH_MSI_DROP } else { 'C:\Users\win10\football-cn-auto-push' }),
  [string]$Key = $env:FAT_CN_SSH_KEY,  # redacted: default key path removed; set FAT_CN_SSH_KEY at runtime (do not record the value here)
  [string]$SshTarget = $env:FAT_CN_SSH_TARGET  # redacted: default SSH target removed; set FAT_CN_SSH_TARGET at runtime (do not record the value here)
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'push-formal-db.ps1') -Dir $Dir -Key $Key -SshTarget $SshTarget
