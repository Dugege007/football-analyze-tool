# 大陆站一键发布的确认与环境检查。只调用仓库里的 Node 脚本，不在这里拼 SSH 命令。
# 目标主机必须是 124.221.56.38。取消时退出码为 0。
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$yes = $false
$help = $false
$printPlanOnly = $false
$forward = New-Object System.Collections.Generic.List[string]

for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = [string]$args[$i]
    switch ($arg) {
        '--yes' { $yes = $true; $forward.Add('--yes') }
        '--help' { $help = $true }
        '-h' { $help = $true }
        '--print-plan' { $printPlanOnly = $true }
        '--dry-run' { $forward.Add('--dry-run') }
        '--skip-build' { $forward.Add('--skip-build') }
        '--modes' {
            $i++
            if ($i -ge $args.Count) { Write-Host '--modes 需要值，例如 static,api'; exit 1 }
            $forward.Add('--modes')
            $forward.Add([string]$args[$i])
        }
        '--env-file' {
            $i++
            if ($i -ge $args.Count) { Write-Host '--env-file 需要路径'; exit 1 }
            $forward.Add('--env-file')
            $forward.Add([string]$args[$i])
        }
        default {
            if ($arg.StartsWith('--modes=')) { $forward.Add($arg) }
            elseif ($arg.StartsWith('--env-file=')) { $forward.Add($arg) }
            else { Write-Host "未知参数 $arg"; exit 1 }
        }
    }
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host '没有找到 Node.js。请先安装 Node.js 20 或更高版本。'
    exit 1
}
$versionText = & node -v
if ($versionText -match 'v(\d+)') {
    if ([int]$Matches[1] -lt 20) {
        Write-Host "需要 Node.js 20 或更高版本，当前为 $versionText。"
        exit 1
    }
}

$scriptPath = Join-Path $PSScriptRoot 'scripts\cn-deploy.mjs'
if ($help) {
    & node $scriptPath --help
    exit $LASTEXITCODE
}

$hasEnvArg = $false
foreach ($item in $forward) {
    if ($item -eq '--env-file' -or $item.StartsWith('--env-file=')) { $hasEnvArg = $true }
}
if (-not $hasEnvArg) {
    $envFile = Join-Path $PSScriptRoot '.env.deploy'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        Write-Host '没有找到 .env.deploy。'
        Write-Host '请先在仓库根目录执行： copy .env.deploy.example .env.deploy'
        Write-Host '然后填写 DEPLOY_SSH_KEY。YuePC 一般为 C:\Users\Administrator\.ssh\id_ed25519，MSI 一般为 C:\Users\win10\.ssh\id_ed25519。'
        exit 1
    }
}

function Invoke-CnDeploy([string[]]$DeployArgs) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        & npm run deploy:cn -- @DeployArgs
    } else {
        & node $scriptPath @DeployArgs
    }
    if ($null -eq $LASTEXITCODE) { return 0 }
    return $LASTEXITCODE
}

if ($printPlanOnly) {
    $planArgs = New-Object System.Collections.Generic.List[string]
    $planArgs.Add('--print-plan')
    foreach ($item in $forward) {
        if ($item -ne '--yes') { $planArgs.Add($item) }
    }
    $planCode = Invoke-CnDeploy $planArgs.ToArray()
    exit $planCode
}

$planArgs = New-Object System.Collections.Generic.List[string]
$planArgs.Add('--print-plan')
foreach ($item in $forward) {
    if ($item -ne '--yes') { $planArgs.Add($item) }
}
$planCode = Invoke-CnDeploy $planArgs.ToArray()
if ($planCode -ne 0) { exit $planCode }

if (-not $yes) {
    Write-Host ''
    Write-Host '请核对上面的目标主机与模式。只有目标主机为 124.221.56.38 时才能继续。'
    $answer = Read-Host '输入 Y 继续，其他键取消'
    if ($answer -cne 'Y' -and $answer -cne 'y') {
        Write-Host '已取消，未发布。'
        exit 0
    }
}

if (-not $forward.Contains('--yes')) { $forward.Add('--yes') }
$runCode = Invoke-CnDeploy $forward.ToArray()
exit $runCode
