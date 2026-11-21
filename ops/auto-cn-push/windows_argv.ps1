function Format-FatWinArg {
  param([AllowEmptyString()][string]$Argument)
  if ($null -eq $Argument -or $Argument.Length -eq 0) { return '""' }
  $special = $Argument.IndexOfAny([char[]]@(' ', "`t", "`n", "`v", '"'))
  if ($special -lt 0) { return $Argument }
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append('"')
  $i = 0
  while ($i -lt $Argument.Length) {
    $slashes = 0
    while ($i -lt $Argument.Length -and $Argument[$i] -eq '\') {
      $slashes++
      $i++
    }
    if ($i -ge $Argument.Length) {
      [void]$sb.Append(('\' * ($slashes * 2)))
      break
    }
    if ($Argument[$i] -eq '"') {
      [void]$sb.Append(('\' * ($slashes * 2 + 1)))
      [void]$sb.Append('"')
    } else {
      if ($slashes -gt 0) { [void]$sb.Append(('\' * $slashes)) }
      [void]$sb.Append($Argument[$i])
    }
    $i++
  }
  [void]$sb.Append('"')
  return $sb.ToString()
}

function Format-FatWinCommandLine {
  param([Parameter(Mandatory = $true)][string[]]$Argument)
  $parts = @()
  foreach ($item in $Argument) {
    $parts += (Format-FatWinArg -Argument $item)
  }
  return ($parts -join ' ')
}

function Set-FatProcessInputEncoding {
  param($StartInfo)
  $prop = $StartInfo.GetType().GetProperty('StandardInputEncoding')
  if ($prop -and $prop.CanWrite) {
    $prop.SetValue($StartInfo, (New-Object System.Text.UTF8Encoding $false), $null)
  }
}

function Invoke-FatWithNoBomInputEncoding {
  param([Parameter(Mandatory = $true)][scriptblock]$Action)
  $saved = $null
  $changed = $false
  try {
    try { $saved = [Console]::InputEncoding } catch {
      throw 'cannot read Console.InputEncoding'
    }
    $noBom = New-Object System.Text.UTF8Encoding $false
    if ($noBom.GetPreamble().Length -ne 0) {
      throw 'UTF8Encoding(false) still emits a BOM'
    }
    try { [Console]::InputEncoding = $noBom } catch {
      throw 'cannot set Console.InputEncoding'
    }
    $changed = $true
    $now = [Console]::InputEncoding
    if ($null -eq $now -or $now.GetPreamble().Length -ne 0) {
      throw 'Console.InputEncoding still emits a BOM'
    }
    & $Action
  } finally {
    if ($changed -and $null -ne $saved) {
      try { [Console]::InputEncoding = $saved } catch {}
    }
  }
}

function Copy-FatRawStdin {
  param(
    [Parameter(Mandatory = $true)]$Process,
    [Parameter(Mandatory = $true)][string]$PayloadPath
  )
  $raw = $Process.StandardInput.BaseStream
  $input = [System.IO.File]::OpenRead($PayloadPath)
  try {
    $input.CopyTo($raw)
  } finally {
    $input.Dispose()
  }
}

