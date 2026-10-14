# 在用户的 MSI 电脑（国内出口）上请求竞彩网赛果，把所有分页打成一个文件，供本机导入。
# 用法：pwsh -NoProfile -File refresh_sporttery.ps1 [-Days 4]
# 输出：$HOME\fat-sporttery\latest_bundle.json（每次覆盖）。只读公开页面，不需要任何密钥。
param([int]$Days = 4)
$ErrorActionPreference = 'Stop'
$outDir = Join-Path $HOME 'fat-sporttery'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$tz = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$today = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz).Date
$b = $today.AddDays(-$Days).ToString('yyyy-MM-dd'); $e = $today.ToString('yyyy-MM-dd')
$ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
$pages = @(); $ok = $true; $err = $null; $p = 1
try {
  while ($true) {
    $u = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry?matchBeginDate=$b&matchEndDate=$e&leagueId=&pageSize=100&pageNo=$p&isFix=0&matchPage=1&pcOrWap=1"
    $r = Invoke-WebRequest -Uri $u -Headers @{ 'User-Agent' = $ua; 'Referer' = 'https://www.sporttery.cn/jc/zqsgkj/' } -TimeoutSec 30
    $j = $r.Content | ConvertFrom-Json
    if ("$($j.errorCode)" -ne '0') { throw "竞彩网返回 errorCode=$($j.errorCode)" }
    $pages += $r.Content
    if ($p -ge [int]$j.value.pages) { break }
    $p++; Start-Sleep -Milliseconds 3500
  }
} catch { $ok = $false; $err = "$($_.Exception.Message)" }
$bundle = [ordered]@{ fetched_at = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz).ToString('yyyy-MM-ddTHH:mm:ss+08:00'); begin = $b; end = $e; ok = $ok; error = $err; pages = $pages }
$path = Join-Path $outDir 'latest_bundle.json'
[IO.File]::WriteAllText($path, ($bundle | ConvertTo-Json -Depth 3 -Compress), [Text.UTF8Encoding]::new($false))
Write-Output "ok=$ok pages=$($pages.Count) range=$b..$e path=$path"
