# 在大陆本机上请求竞彩网赛果，把所有分页打成一个文件，供研究机导入。
# 部署时优先在 YPC（即 YuePC）上运行。实测目录是 C:\Users\Administrator\fat-sporttery\。
# YPC 上使用 Windows PowerShell 5.1。YPC 上的 pwsh 只是 WindowsApps 占位程序，不能使用。
# YPC 不在线，或者在 YPC 上取不到数据时，才改在 MSI 上用 pwsh 运行。MSI 的备用目录是 C:\Users\win10\fat-sporttery\。
# D:\Projects\fat-sporttery\ 不是主路径。该盘写入曾经被拒绝，只可在文件写成功后再存一份可选副本。
# 用法：在 YPC 上用 powershell -NoProfile -File refresh_sporttery.ps1 [-Days 4]；在 MSI 上把 powershell 换成 pwsh。
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
