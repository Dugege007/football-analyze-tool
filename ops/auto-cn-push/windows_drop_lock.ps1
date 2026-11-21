# 安装和推送共用的 Windows 文件锁。FileShare.None 在 YuePC 与 MSI 上互相排斥。
# 不调用 flock，也不要求 Git Bash。
function Open-FatDropLock {
  param([Parameter(Mandatory = $true)][string]$Dir)
  if (-not [System.IO.Directory]::Exists($Dir)) {
    [System.IO.Directory]::CreateDirectory($Dir) | Out-Null
  }
  $path = Join-Path $Dir 'drop.operation.lock'
  try {
    return [System.IO.File]::Open(
      $path,
      [System.IO.FileMode]::OpenOrCreate,
      [System.IO.FileAccess]::ReadWrite,
      [System.IO.FileShare]::None
    )
  } catch [System.IO.IOException] {
    return $null
  }
}
