# Sync the formal SQLite snapshot before start.bat launches the API.
# Use D:\Du_Test\football\sync when app_latest.db.gz and app_latest.db.sha256
# are both in that folder; otherwise use the fallback folder
# C:\Users\win10\football-sync. The script picks a folder by which package
# is present. It does not pick a folder by computer name. A confirmed sync
# path for YPC (that is, YuePC) is not written here until that path is known.
# Compare SHA256 of the uncompressed db. Same hash skips decompress.
# A different hash, or a missing local db, gzip-decompresses and replaces
# D:\Du_Test\football\current\app_latest.db atomically.
# This script does not download anything. It always exits 0.
# Console text is ASCII only so Windows PowerShell 5.1 can parse this file.
param(
    [string]$PreferredSyncDir = 'D:\Du_Test\football\sync',
    [string]$FallbackSyncDir = 'C:\Users\win10\football-sync',
    [string]$CurrentDb = 'D:\Du_Test\football\current\app_latest.db'
)

$ErrorActionPreference = 'Stop'
$script:PartialPath = $null

function Test-SyncPackage([string]$Dir) {
    if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return $false }
    $hashPath = Join-Path $Dir 'app_latest.db.sha256'
    $gzPath = Join-Path $Dir 'app_latest.db.gz'
    $hasHash = Test-Path -LiteralPath $hashPath -PathType Leaf
    $hasGz = Test-Path -LiteralPath $gzPath -PathType Leaf
    return ($hasHash -and $hasGz)
}

function Read-ExpectedHash([string]$Path) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $buffer = New-Object byte[] 4096
        $count = $stream.Read($buffer, 0, $buffer.Length)
    } finally {
        $stream.Dispose()
    }
    if ($count -le 0) { return $null }
    $text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $count)
    $found = [regex]::Match($text, '[0-9A-Fa-f]{64}')
    if (-not $found.Success) { return $null }
    return $found.Value.ToLowerInvariant()
}

function Get-FileSha256([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $hash = $sha.ComputeHash($stream)
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
}

function Clear-Partial {
    if ([string]::IsNullOrWhiteSpace($script:PartialPath)) { return }
    if (Test-Path -LiteralPath $script:PartialPath) {
        Remove-Item -LiteralPath $script:PartialPath -Force -ErrorAction SilentlyContinue
    }
}

function Expand-GzipFile([string]$GzPath, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression -ErrorAction SilentlyContinue
    $gzStream = [System.IO.File]::Open($GzPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $gzip = New-Object System.IO.Compression.GzipStream($gzStream, [System.IO.Compression.CompressionMode]::Decompress)
        try {
            $output = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try {
                $gzip.CopyTo($output)
            } finally {
                $output.Dispose()
            }
        } finally {
            $gzip.Dispose()
        }
    } finally {
        $gzStream.Dispose()
    }
}

function Install-AtomicFile([string]$Partial, [string]$Target) {
    $directory = Split-Path -Parent $Target
    if ([string]::IsNullOrWhiteSpace($directory)) { $directory = '.' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    }
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
        [System.IO.File]::Move($Partial, $Target)
        return
    }
    # ReplaceFile is atomic on the same volume. Partial already sits beside the target.
    [System.IO.File]::Replace($Partial, $Target, [NullString]::Value)
}

function Remove-SqliteSidecars([string]$Target) {
    foreach ($suffix in @('-wal', '-shm', '-journal')) {
        $side = $Target + $suffix
        if (-not (Test-Path -LiteralPath $side)) { continue }
        try {
            Remove-Item -LiteralPath $side -Force
        } catch {
            Write-Output ('[sync] warn: could not remove ' + $side)
        }
    }
}

try {
    $source = $null
    if (Test-SyncPackage $PreferredSyncDir) {
        $source = $PreferredSyncDir
    } elseif (Test-SyncPackage $FallbackSyncDir) {
        $source = $FallbackSyncDir
    }

    if ([string]::IsNullOrWhiteSpace($source)) {
        $preferredExists = Test-Path -LiteralPath $PreferredSyncDir -PathType Container
        $fallbackExists = Test-Path -LiteralPath $FallbackSyncDir -PathType Container
        if ((-not $preferredExists) -and (-not $fallbackExists)) {
            Write-Output '[sync] warn: sync folder missing; keep current db'
        } else {
            Write-Output '[sync] warn: hash or gz missing; keep current db'
        }
        exit 0
    }

    Write-Output ('[sync] source ' + $source)
    $expected = Read-ExpectedHash (Join-Path $source 'app_latest.db.sha256')
    if ([string]::IsNullOrWhiteSpace($expected)) {
        Write-Output '[sync] warn: hash file unreadable; keep current db'
        exit 0
    }

    if (Test-Path -LiteralPath $CurrentDb -PathType Leaf) {
        $localHash = Get-FileSha256 $CurrentDb
        if ($localHash -eq $expected) {
            Write-Output '[sync] unchanged; skip'
            exit 0
        }
    }

    $directory = Split-Path -Parent $CurrentDb
    if ([string]::IsNullOrWhiteSpace($directory)) { $directory = '.' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    }
    $script:PartialPath = $CurrentDb + '.partial'
    Clear-Partial
    Expand-GzipFile (Join-Path $source 'app_latest.db.gz') $script:PartialPath

    $expandedHash = Get-FileSha256 $script:PartialPath
    if ($expandedHash -ne $expected) {
        Clear-Partial
        Write-Output '[sync] warn: decompressed hash mismatch; keep current db'
        exit 0
    }

    Install-AtomicFile $script:PartialPath $CurrentDb
    $script:PartialPath = $null
    Remove-SqliteSidecars $CurrentDb
    Write-Output ('[sync] updated ' + $CurrentDb)
    exit 0
} catch {
    Clear-Partial
    Write-Output ('[sync] warn: ' + $_.Exception.Message)
    exit 0
}
