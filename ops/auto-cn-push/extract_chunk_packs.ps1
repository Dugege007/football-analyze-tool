param(
  [Parameter(Mandatory = $true)][string]$PacksDir,
  [Parameter(Mandatory = $true)][string]$ChunksDir
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PacksDir)) { throw "missing PacksDir $PacksDir" }
if (-not (Test-Path -LiteralPath $ChunksDir)) { New-Item -ItemType Directory -Path $ChunksDir | Out-Null }

$sums = Join-Path $PacksDir 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sums)) { throw "missing $sums" }
Get-Content -LiteralPath $sums | ForEach-Object {
  $line = $_.Trim()
  if (-not $line) { return }
  $parts = $line -split '\s+', 2
  if ($parts.Count -lt 2) { throw "bad SHA256SUMS line: $line" }
  $want = $parts[0].ToLower()
  $name = $parts[1].Trim()
  $path = Join-Path $PacksDir $name
  if (-not (Test-Path -LiteralPath $path)) { throw "missing pack $path" }
  $got = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $want) { throw "pack sha mismatch $name got=$got want=$want" }
  Write-Output "OK hash $name"
}

Get-ChildItem -LiteralPath $PacksDir -Filter '*.tar' | Sort-Object Length | ForEach-Object {
  Write-Output ("EXTRACT {0} bytes={1}" -f $_.Name, $_.Length)
  & tar -xf $_.FullName -C $ChunksDir
  if ($LASTEXITCODE -ne 0) { throw "tar failed on $($_.Name)" }
}

$need = @('manifest.json', 'meta.json', 'app_latest.db.gz.sha256', 'app_latest.db.sha256')
foreach ($f in $need) {
  $p = Join-Path $ChunksDir $f
  if (-not (Test-Path -LiteralPath $p)) { throw "extract missing $p" }
}
$man = Get-Content -LiteralPath (Join-Path $ChunksDir 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($part in $man.parts) {
  $p = Join-Path $ChunksDir $part
  if (-not (Test-Path -LiteralPath $p)) { throw "extract missing chunk $p" }
}
Write-Output ("EXTRACT_OK parts={0} gz={1} db={2}" -f $man.n, $man.gz_sha256, $man.db_sha256)
