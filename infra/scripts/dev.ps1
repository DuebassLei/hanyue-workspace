#Requires -Version 5.1
<#
.SYNOPSIS
  本机开发一键启停：PostgreSQL + API + 前端（Windows PowerShell 原生版）。

.DESCRIPTION
  一条命令把「数据库 → 后端 → 前端」串起来，进程在后台运行，日志落在 infra\.dev\logs。
  数据库复用 infra\scripts\dev-pg.ps1（自建集群或复用系统服务都支持），
  因此不需要 Docker，也不影响系统里已有的库。

  运行中的 PID 记在 infra\.dev\pids\*.json；stop 会连子进程一起结束
  （uvicorn --reload、npm → node 都是进程树）。

.PARAMETER Command
  up（默认动作）| stop | restart | status | logs | help

.PARAMETER Target
  all（默认）| api | web | db

.EXAMPLE
  cd infra
  .\scripts\dev.ps1 up                 # 起库 + 后端 + 前端，等就绪后打印地址
  .\scripts\dev.ps1 status             # 三个组件状态 + 端口 + 日志位置
  .\scripts\dev.ps1 restart api        # 只重启后端（改 .env 后用）
  .\scripts\dev.ps1 restart            # 全部重启（含数据库）
  .\scripts\dev.ps1 logs web -Tail 80  # 看前端日志
  .\scripts\dev.ps1 logs api -Follow   # 持续跟踪（Ctrl+C 退出）
  .\scripts\dev.ps1 stop               # 全部停止
  .\scripts\dev.ps1 stop -KeepDb       # 停应用，数据库留着

.NOTES
  本文件必须存为 UTF-8 with BOM：PowerShell 5.1 会把无 BOM 的脚本按 ANSI(GBK) 读取，
  中文提示会变乱码并导致语法错误。

  若提示「禁止运行脚本」，用：
    powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 up
  或在当前会话：Set-ExecutionPolicy -Scope Process Bypass
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = 'up',

    [Parameter(Position = 1)]
    [string]$Target = 'all',

    [Parameter(Position = 2, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @(),

    # stop 时保留数据库
    [switch]$KeepDb,

    # 后端不带 --reload（排查 reload 干扰时用）
    [switch]$NoReload,

    # 端口被别的进程占着时，结束它再起
    [switch]$Force,

    # logs 的尾部行数 / 是否持续跟踪
    [int]$Tail = 40,
    [switch]$Follow,

    # 启动后等待就绪的秒数
    [int]$TimeoutSec = 180
)

# 本脚本调用原生 exe（node/npm/python/taskkill），PowerShell 5.1 在 Stop 下会把
# 原生 stderr 当终止性错误，所以统一用 Continue + $LASTEXITCODE 判断。
$ErrorActionPreference = 'Continue'

# ── 路径 ──────────────────────────────────────────────────────
$ScriptDir = $PSScriptRoot                       # infra\scripts
$InfraDir  = Split-Path -Parent $ScriptDir       # infra
$RepoDir   = Split-Path -Parent $InfraDir        # 仓库根
$ApiDir    = Join-Path $RepoDir 'apps\api'
$WebDir    = Join-Path $RepoDir 'apps\web'
$DevDir    = Join-Path $InfraDir '.dev'
$PidDir    = Join-Path $DevDir 'pids'
$LogDir    = Join-Path $DevDir 'logs'
$DevPg     = Join-Path $ScriptDir 'dev-pg.ps1'

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Write-Info([string]$Message) { Write-Host "[dev] $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host "[dev] $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "[dev] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host "[dev] $Message" -ForegroundColor Red
    exit 1
}

# 环境变量优先，其次 infra\.env，最后默认值——与 dev-pg.ps1 / compose 一致
function Get-EnvValue([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and (Test-Path -LiteralPath (Join-Path $InfraDir '.env'))) {
        $pattern = '^' + [regex]::Escape($Name) + '='
        $line = Get-Content -LiteralPath (Join-Path $InfraDir '.env') -Encoding UTF8 |
            Where-Object { $_ -match $pattern } | Select-Object -Last 1
        if ($line) {
            $value = $line.Substring($Name.Length + 1).Trim()
            $value = $value.Trim('"').Trim("'")
        }
    }
    if ($value) { return $value }
    return $Default
}

$ApiPort = [int](Get-EnvValue 'DEV_API_PORT' '8000')
$WebPort = [int](Get-EnvValue 'DEV_WEB_PORT' '5173')
$ApiHost = '127.0.0.1'

# ── 进程工具 ──────────────────────────────────────────────────

function Get-PortOwner([int]$Port) {
    $conn = $null
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    catch { $conn = $null }
    if ($conn) { return [int]$conn.OwningProcess }
    # 老系统没有 Get-NetTCPConnection：退回 netstat
    $netstat = & netstat -ano -p tcp 2>$null | Select-String -Pattern (":$Port\s")
    foreach ($row in $netstat) {
        $parts = ($row.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5 -and $parts[3] -eq 'LISTENING') { return [int]$parts[4] }
    }
    return 0
}

function Test-Port([int]$Port, [int]$TimeoutMs = 800) {
    # 必须按 IPv4 字面量连接。传字符串 '127.0.0.1' 会走 Dns.GetHostAddresses，
    # 本机先返回 ::1；Vite 只绑 IPv4，于是明明已就绪的端口被判成没起来。
    $client = New-Object System.Net.Sockets.TcpClient([System.Net.Sockets.AddressFamily]::InterNetwork)
    try {
        $async = $client.BeginConnect([System.Net.IPAddress]::Loopback, $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    }
    catch { return $false }
    finally { $client.Close() }
}

function Get-RecordPath([string]$Name) { Join-Path $PidDir "$Name.json" }

function Get-Record([string]$Name) {
    $path = Get-RecordPath $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
        if (-not $raw) { return $null }
        return $raw | ConvertFrom-Json
    }
    catch { return $null }
}

function Save-Record([string]$Name, [int]$ProcessId, [int]$Port) {
    if (-not (Test-Path -LiteralPath $PidDir)) { New-Item -ItemType Directory -Path $PidDir -Force | Out-Null }
    $record = [ordered]@{
        name      = $Name
        pid       = $ProcessId
        port      = $Port
        startedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Get-RecordPath $Name) -Encoding UTF8
}

function Test-RecordAlive($Record) {
    if (-not $Record) { return $false }
    $proc = Get-Process -Id $Record.pid -ErrorAction SilentlyContinue
    return [bool]$proc
}

function Stop-ProcessTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    # /T 连子进程一起结束：uvicorn --reload 的 reloader 子进程、npm.cmd 起的 node 都要带走
    & taskkill.exe /PID $ProcessId /T /F *> $null
    return
}

function Start-Detached {
    # 后台启动 + 输出重定向到日志文件，不继承本控制台的管道（否则常驻进程会把
    # 脚本卡在「等待流结束」上，pg_ctl 那次就是这么挂的）。
    #
    # 这里刻意绕过 Start-Process：PowerShell 5.1 会把当前环境变量导进一个不区分
    # 大小写的字典，环境里同时存在 HTTPS_PROXY 与 https_proxy 这类重复键时直接抛
    # 「已添加项」；而按大小写去删又会把 SystemRoot 整个删掉（Windows 环境变量本就
    # 不区分大小写），导致子进程 Winsock 初始化失败（WinError 10106）。
    # 直接用 ProcessStartInfo + cmd 重定向，不碰 EnvironmentVariables，环境原样继承。
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$OutLog,
        [string]$ErrLog
    )
    $quoted = @()
    foreach ($arg in $ArgumentList) {
        if ($arg -match '[\s"]') { $quoted += '"' + ($arg -replace '"', '\"') + '"' }
        else { $quoted += $arg }
    }
    $commandLine = '/c ""{0}" {1} > "{2}" 2> "{3}""' -f $FilePath, ($quoted -join ' '), $OutLog, $ErrLog

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = $commandLine
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    return [System.Diagnostics.Process]::Start($psi)
}

function Stop-App([string]$Name) {
    $record = Get-Record $Name
    $path = Get-RecordPath $Name
    if (-not (Test-RecordAlive $record)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        return $false
    }
    Stop-ProcessTree $record.pid
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    # 等进程真的退出，避免紧接着 up 时端口还没释放
    for ($i = 0; $i -lt 25; $i++) {
        if (-not (Get-Process -Id $record.pid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
    Write-Ok "已停止 $Name（pid $($record.pid)）"
    return $true
}

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
        }
        catch {
            # 连不上或 5xx：还没起来，继续等
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-Tcp([int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Show-Tail([string]$Path, [int]$Lines) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $content = Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $content) { return }
    Write-Host "---- 最近 $Lines 行：$Path ----" -ForegroundColor DarkGray
    $content | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

# ── 依赖探测 ──────────────────────────────────────────────────

function Get-PythonExe {
    $candidates = @(
        (Join-Path $ApiDir '.venv\Scripts\python.exe'),
        (Join-Path $ApiDir '.venv\bin\python')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    Fail "找不到 apps\api\.venv。先建虚拟环境：cd apps\api; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-prod.txt"
}

function Get-NpmCmd {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) { Fail '找不到 node。装 Node.js ≥ 18 并确保在 PATH 里。' }
    $npmCmd = Join-Path (Split-Path -Parent $nodeCmd.Source) 'npm.cmd'
    if (Test-Path -LiteralPath $npmCmd) { return $npmCmd }
    $alt = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($alt) { return $alt.Source }
    Fail "找不到 npm.cmd（node 在 $($nodeCmd.Source)）。修复 Node.js 安装或 PATH。"
}

# ── 各组件 ────────────────────────────────────────────────────

function Get-DatabaseUrl {
    # 以 API 真正会读的 apps\api\.env 为准；没有就按 infra\.env 的 PG 变量拼一个
    $apiEnv = Join-Path $ApiDir '.env'
    if (Test-Path -LiteralPath $apiEnv) {
        $line = Get-Content -LiteralPath $apiEnv -Encoding UTF8 |
            Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -Last 1
        if ($line) { return ($line -replace '^\s*DATABASE_URL\s*=', '').Trim().Trim('"').Trim("'") }
    }
    $mode = Get-EnvValue 'PG_MODE' 'cluster'
    $defaultPort = '5433'
    if ($mode -eq 'service') { $defaultPort = '5432' }
    $port = Get-EnvValue 'DEV_PG_PORT' $defaultPort
    $user = Get-EnvValue 'DEV_POSTGRES_USER' 'hanyue'
    $pass = Get-EnvValue 'DEV_POSTGRES_PASSWORD' 'hanyue_dev_pwd'
    $db = Get-EnvValue 'DEV_POSTGRES_DB' 'hanyue'
    return "postgresql+psycopg://$user`:$pass@127.0.0.1`:$port/$db"
}

function Get-DbTableCount {
    # 用应用自己的 venv + 连接串探一次：能查到表数量才算真的可用（只看端口会把
    # 「服务在但角色/库没建」误判为就绪）。失败返回 -1。
    $python = Get-PythonExe
    $databaseUrl = Get-DatabaseUrl
    if ($databaseUrl -notmatch '^postgresql') {
        Fail "apps\api\.env 里的 DATABASE_URL 不是 PostgreSQL：$databaseUrl`n  本机开发请用 postgresql+psycopg://hanyue:hanyue_dev_pwd@127.0.0.1:5433/hanyue（.\scripts\dev-pg.ps1 write-env 可自动写回）"
    }

    # 探针写成文件再执行：PowerShell 5.1 把 -c 的字符串交给原生 exe 时会吃掉里面的双引号，
    # 内联多行 Python 会直接 SyntaxError。
    if (-not (Test-Path -LiteralPath $DevDir)) { New-Item -ItemType Directory -Path $DevDir -Force | Out-Null }
    $probePath = Join-Path $DevDir 'db_probe.py'
    $code = @'
import os, sys

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
try:
    import psycopg
    with psycopg.connect(url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            print(cur.fetchone()[0])
except Exception as exc:
    print(str(exc).splitlines()[0] if str(exc) else repr(exc), file=sys.stderr)
    sys.exit(1)
'@
    [System.IO.File]::WriteAllText($probePath, $code, (New-Object System.Text.UTF8Encoding($false)))

    $oldUrl = $env:DATABASE_URL
    $env:DATABASE_URL = $databaseUrl
    try {
        $output = & $python $probePath
        if ($LASTEXITCODE -ne 0) {
            $reason = ($output | Out-String).Trim()
            if ($reason) { Write-Host "  连库探测失败：$reason" -ForegroundColor DarkGray }
            return -1
        }
        $text = ($output | Out-String).Trim()
        $count = 0
        if ([int]::TryParse($text, [ref]$count)) { return $count }
        return -1
    }
    finally { $env:DATABASE_URL = $oldUrl }
}

function Start-Db {
    if (-not (Test-Path -LiteralPath $DevPg)) { Fail "缺少 $DevPg" }
    Write-Info '启动数据库（dev-pg.ps1 up）'
    & $DevPg up
    if ($LASTEXITCODE -ne 0) { Fail '数据库启动失败，先单独跑 .\scripts\dev-pg.ps1 up 看详细输出' }
    $tables = Get-DbTableCount
    if ($tables -lt 0) {
        Fail "数据库起来了但应用账号连不上。`n  跑 .\scripts\dev-pg.ps1 status 看细节；半初始化的集群用 .\scripts\dev-pg.ps1 reset 重建。"
    }
    Write-Ok "数据库就绪（public 下 $tables 张表$(if ($tables -eq 0) { '，启 API 时自动建表' })）"
}

function Stop-Db {
    if (-not (Test-Path -LiteralPath $DevPg)) { return }
    & $DevPg down
}

function Start-Api {
    $python = Get-PythonExe
    if (-not (Test-Path -LiteralPath (Join-Path $ApiDir '.env'))) {
        Write-Warn2 "apps\api\.env 不存在，将使用默认配置；需要改连接串/密钥时 cp .env.example .env"
    }

    $owner = Get-PortOwner $ApiPort
    if ($owner -gt 0) {
        $record = Get-Record 'api'
        if ((Test-RecordAlive $record) -and ($record.port -eq $ApiPort)) {
            Write-Ok "API 已在运行（pid $($record.pid)，端口 $ApiPort）"
            return
        }
        if ($Force) {
            Write-Warn2 "端口 $ApiPort 被 pid $owner 占用，-Force 已结束它"
            Stop-ProcessTree $owner
            Start-Sleep -Milliseconds 800
        }
        else {
            $holder = Get-Process -Id $owner -ErrorAction SilentlyContinue
            $name = 'unknown'
            if ($holder) { $name = $holder.ProcessName }
            Fail "端口 $ApiPort 被 $name (pid $owner) 占用。\n  结束它：.\scripts\dev.ps1 up api -Force\n  或换端口：`$env:DEV_API_PORT='8001'; .\scripts\dev.ps1 up api"
        }
    }

    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $outLog = Join-Path $LogDir 'api.out.log'
    $errLog = Join-Path $LogDir 'api.err.log'

    $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', $ApiHost, '--port', "$ApiPort")
    $reload = -not $NoReload
    if ($env:DEV_API_RELOAD -eq '0') { $reload = $false }
    if ($reload) { $arguments += '--reload' }

    Write-Info "启动 API（端口 $ApiPort$(if ($reload) { '，热重载' })）"
    $proc = Start-Detached -FilePath $python -ArgumentList $arguments -WorkingDirectory $ApiDir `
        -OutLog $outLog -ErrLog $errLog
    if (-not $proc) { Fail 'API 进程启动失败' }

    if (-not (Wait-Http "http://$ApiHost`:$ApiPort/health" $TimeoutSec)) {
        $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($alive) { Stop-ProcessTree $proc.Id }
        Remove-Item -LiteralPath (Get-RecordPath 'api') -Force -ErrorAction SilentlyContinue
        Show-Tail $errLog 30
        Show-Tail $outLog 15
        Fail "API 在 $TimeoutSec 秒内没有就绪，已停止。日志：$errLog"
    }
    # 记录真正的监听进程而不是 cmd 包装层：cmd 把输出重定向完就退出，
    # 记它的 pid 会让 status 在服务正常时也显示「未运行」。
    $listener = Get-PortOwner $ApiPort
    if ($listener -le 0) { $listener = $proc.Id }
    Save-Record 'api' $listener $ApiPort
    Write-Ok "API 就绪 pid=$listener  http://$ApiHost`:$ApiPort"
}

function Stop-Api {
    if (-not (Stop-App 'api')) { Write-Info 'API 未在运行' }
    # 兜底：进程树被强杀后 reloader 有时还会占着端口
    $owner = Get-PortOwner $ApiPort
    if ($owner -gt 0) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc -and ($proc.ProcessName -like 'python*')) {
            Stop-ProcessTree $owner
            Write-Info "清理残留的 python 进程 pid=$owner（占着 $ApiPort）"
        }
    }
}

function Start-Web {
    if (-not (Test-Path -LiteralPath (Join-Path $WebDir 'node_modules'))) {
        Fail "apps\web\node_modules 不存在。先装依赖：cd apps\web; npm install"
    }
    $npm = Get-NpmCmd

    $owner = Get-PortOwner $WebPort
    if ($owner -gt 0) {
        $record = Get-Record 'web'
        if ((Test-RecordAlive $record) -and ($record.port -eq $WebPort)) {
            Write-Ok "前端已在运行（pid $($record.pid)，端口 $WebPort）"
            return
        }
        if ($Force) {
            Write-Warn2 "端口 $WebPort 被 pid $owner 占用，-Force 已结束它"
            Stop-ProcessTree $owner
            Start-Sleep -Milliseconds 800
        }
        else {
            $holder = Get-Process -Id $owner -ErrorAction SilentlyContinue
            $name = 'unknown'
            if ($holder) { $name = $holder.ProcessName }
            Fail "端口 $WebPort 被 $name (pid $owner) 占用。\n  结束它：.\scripts\dev.ps1 up web -Force\n  或换端口：`$env:DEV_WEB_PORT='5174'; .\scripts\dev.ps1 up web"
        }
    }

    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $outLog = Join-Path $LogDir 'web.out.log'
    $errLog = Join-Path $LogDir 'web.err.log'

    $arguments = @('run', 'dev', '--', '--port', "$WebPort", '--strictPort', '--host', '127.0.0.1')
    Write-Info "启动前端（端口 $WebPort）"
    # npm.cmd 会再拉 node：Start-Detached 内部就是 cmd /c，.cmd 能直接执行。
    # 必须显式 --host 127.0.0.1：本机 localhost 解析到 ::1 优先，Vite 默认只绑 IPv6，
    # 而就绪探测与后端代理都走 IPv4，结果是「日志写着 ready 但端口探测永远失败」。
    $proc = Start-Detached -FilePath $npm `
        -ArgumentList @('run', 'dev', '--', '--port', "$WebPort", '--strictPort', '--host', '127.0.0.1') `
        -WorkingDirectory $WebDir -OutLog $outLog -ErrLog $errLog
    if (-not $proc) { Fail '前端进程启动失败' }

    if (-not (Wait-Tcp $WebPort $TimeoutSec)) {
        $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($alive) { Stop-ProcessTree $proc.Id }
        Show-Tail $errLog 30
        Show-Tail $outLog 15
        Fail "前端在 $TimeoutSec 秒内没有监听 $WebPort，已停止。日志：$errLog"
    }
    # npm.cmd 经 cmd 拉起后，真正监听端口的是 node；cmd 本身很快退出
    $listener = Get-PortOwner $WebPort
    if ($listener -le 0) { $listener = $proc.Id }
    Save-Record 'web' $listener $WebPort
    Write-Ok "前端就绪 pid=$listener  http://localhost:$WebPort"
}

function Stop-Web {
    if (-not (Stop-App 'web')) { Write-Info '前端未在运行' }
    $owner = Get-PortOwner $WebPort
    if ($owner -gt 0) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc -and ($proc.ProcessName -like 'node*')) {
            Stop-ProcessTree $owner
            Write-Info "清理残留的 node 进程 pid=$owner（占着 $WebPort）"
        }
    }
}

# ── 子命令 ────────────────────────────────────────────────────

function Resolve-Targets([string]$Which) {
    switch ($Which.ToLower()) {
        'all' { return @('db', 'api', 'web') }
        'api' { return @('api') }
        'web' { return @('web') }
        'db'  { return @('db') }
        default { Fail "未知目标 '$Which'，可选：all / api / web / db" }
    }
}

function Invoke-Up([string]$Which) {
    $targets = Resolve-Targets $Which
    if ($targets -contains 'db') { Start-Db }
    if ($targets -contains 'api') { Start-Api }
    if ($targets -contains 'web') { Start-Web }

    Write-Host ''
    Write-Host '  ┌──────────────────────────────────────────────┐' -ForegroundColor DarkGray
    if ($targets -contains 'web') {
        Write-Host "  前端    http://localhost:$WebPort" -ForegroundColor White
    }
    if ($targets -contains 'api') {
        Write-Host "  后端    http://$ApiHost`:$ApiPort        （/docs 是 Swagger）" -ForegroundColor White
    }
    Write-Host "  日志    $LogDir" -ForegroundColor DarkGray
    Write-Host '  └──────────────────────────────────────────────┘' -ForegroundColor DarkGray
    Write-Host "  看日志：.\scripts\dev.ps1 logs api -Follow     停止：.\scripts\dev.ps1 stop" -ForegroundColor DarkGray
}

function Invoke-Stop([string]$Which) {
    $targets = Resolve-Targets $Which
    # 先停应用再停库：避免 API 在库消失的瞬间刷一屏连接错误
    if ($targets -contains 'web') { Stop-Web }
    if ($targets -contains 'api') { Stop-Api }
    if (($targets -contains 'db') -and (-not $KeepDb)) {
        Write-Info '停止数据库（数据保留）'
        Stop-Db
    }
    Write-Ok '已停止'
}

function Invoke-Restart([string]$Which) {
    $targets = Resolve-Targets $Which
    Write-Info "重启：$($targets -join ', ')"
    if ($targets -contains 'web') { Stop-Web }
    if ($targets -contains 'api') { Stop-Api }
    if (($targets -contains 'db') -and (-not $KeepDb)) { Stop-Db }
    Start-Sleep -Milliseconds 500
    Invoke-Up $Which
}

function Invoke-Status {
    Write-Info '数据库'
    if (Test-Path -LiteralPath $DevPg) { & $DevPg status }
    else { Write-Warn2 "缺少 $DevPg" }

    Write-Host ''
    Write-Info '应用'

    $apiRecord = Get-Record 'api'
    $apiAlive = Test-RecordAlive $apiRecord
    $apiText = '未运行'
    if ($apiAlive) {
        $healthy = Wait-Http "http://$ApiHost`:$ApiPort/health" 1
        $state = '健康'
        if (-not $healthy) { $state = '进程在但 /health 不通' }
        $apiText = "运行中 pid=$($apiRecord.pid)  启动于 $($apiRecord.startedAt)  $state"
    }
    Write-Host ("  {0,-6} {1,-6} {2}" -f 'api', "$ApiPort", $apiText) -ForegroundColor $(if ($apiAlive) { 'Green' } else { 'DarkGray' })

    $webRecord = Get-Record 'web'
    $webAlive = Test-RecordAlive $webRecord
    $webText = '未运行'
    if ($webAlive) {
        $state = '端口未监听'
        if (Test-Port $WebPort) { $state = '健康' }
        $webText = "运行中 pid=$($webRecord.pid)  启动于 $($webRecord.startedAt)  $state"
    }
    Write-Host ("  {0,-6} {1,-6} {2}" -f 'web', "$WebPort", $webText) -ForegroundColor $(if ($webAlive) { 'Green' } else { 'DarkGray' })

    Write-Host ''
    Write-Host "  日志目录 $LogDir" -ForegroundColor DarkGray
    Write-Host "  数据库日志 $(Join-Path $InfraDir '.pgdev\server.log')" -ForegroundColor DarkGray
}

function Invoke-Logs([string]$Which) {
    $map = @{
        api = @((Join-Path $LogDir 'api.out.log'), (Join-Path $LogDir 'api.err.log'))
        web = @((Join-Path $LogDir 'web.out.log'), (Join-Path $LogDir 'web.err.log'))
        db  = @((Join-Path $InfraDir '.pgdev\server.log'))
    }
    $key = $Which.ToLower()
    if ($key -eq 'all') { $key = 'api' }
    if (-not $map.ContainsKey($key)) { Fail "未知日志 '$Which'，可选：api / web / db" }

    foreach ($file in $map[$key]) {
        if (-not (Test-Path -LiteralPath $file)) { continue }
        if ($file -like '*server.log') {
            # PG 在 Windows 上按 ANSI(GBK) 写本地化消息
            if ($Follow) { Get-Content -LiteralPath $file -Tail $Tail -Wait }
            else { Get-Content -LiteralPath $file -Tail $Tail }
        }
        else {
            if ($Follow) { Get-Content -LiteralPath $file -Tail $Tail -Wait -Encoding UTF8 }
            else { Get-Content -LiteralPath $file -Tail $Tail -Encoding UTF8 }
        }
    }
}

function Show-Usage {
    $text = @'

本机开发一键启停（Windows PowerShell）

  cd infra
  .\scripts\dev.ps1 up                 起库 + 后端 + 前端（已在跑的会跳过）
  .\scripts\dev.ps1 status             三个组件的状态、端口、日志位置
  .\scripts\dev.ps1 restart            全部重启（数据库也重启）
  .\scripts\dev.ps1 restart api        只重启后端
  .\scripts\dev.ps1 logs api -Follow   跟踪后端日志（Ctrl+C 退出）
  .\scripts\dev.ps1 stop               全部停止
  .\scripts\dev.ps1 stop -KeepDb       停应用，数据库留在后台
  .\scripts\dev.ps1 down               等同于 stop -KeepDb

目标（第二个参数）：all（默认）/ api / web / db

可选开关：
  -KeepDb         stop / restart 时不碰数据库
  -NoReload       后端不带 --reload
  -Force          端口被别的进程占用时直接结束它
  -Tail <n>       logs 显示尾部 n 行（默认 40）
  -Follow         logs 持续跟踪
  -TimeoutSec <n> 等待就绪的秒数（默认 180）

端口与数据库由环境变量覆盖（也可写进 infra\.env）：
  DEV_API_PORT（默认 8000）、DEV_WEB_PORT（默认 5173）
  其余 PG 相关变量见 .\scripts\dev-pg.ps1 help

日志：infra\.dev\logs\{api,web}.{out,err}.log、infra\.pgdev\server.log
PID： infra\.dev\pids\{api,web}.json
'@
    $text -split "`n" | ForEach-Object { Write-Host $_ }
}

switch ($Command.ToLower()) {
    'up'      { Invoke-Up $Target }
    'start'   { Invoke-Up $Target }
    'stop'    { Invoke-Stop $Target }
    'down'    { $KeepDb = $true; Invoke-Stop $Target }
    'restart' { Invoke-Restart $Target }
    'status'  { Invoke-Status }
    'ps'      { Invoke-Status }
    'logs'    { Invoke-Logs $Target }
    'help'    { Show-Usage }
    '-h'      { Show-Usage }
    '--help'  { Show-Usage }
    default   {
        Write-Warn2 "未知命令 '$Command'"
        Show-Usage
        exit 1
    }
}
