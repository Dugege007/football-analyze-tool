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
