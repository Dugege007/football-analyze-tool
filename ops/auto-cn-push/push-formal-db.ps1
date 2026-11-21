# YuePC 优先。MSI 备用脚本只是换投放目录后调用本文件。
# 本文件不读取包。读取、锁和服务器事务都在 invoke_cn_push.sh。
param(
  [string]$Dir = $(if ($env:AUTO_CN_PUSH_YPC_DROP) { $env:AUTO_CN_PUSH_YPC_DROP } else { 'C:\Users\Administrator\football-cn-auto-push' }),
  [string]$Key = $env:FAT_CN_SSH_KEY,
  [string]$SshTarget = $env:FAT_CN_SSH_TARGET,
  [switch]$SkipReadyCheck
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bash = if ($env:FAT_CN_BASH) { $env:FAT_CN_BASH } else { 'bash' }
$args = @((Join-Path $here 'invoke_cn_push.sh'), '--dir', $Dir)
if ($SshTarget) { $args += @('--ssh-target', $SshTarget) }
if ($Key) { $args += @('--ssh-key', $Key) }
if ($SkipReadyCheck) { $args += '--skip-ready' }
& $bash @args
exit $LASTEXITCODE
