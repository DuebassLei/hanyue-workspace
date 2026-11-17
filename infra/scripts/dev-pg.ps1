#Requires -Version 5.1
<#
.SYNOPSIS
  本机原生 PostgreSQL 管理（Windows PowerShell 版，无需 Git Bash / WSL）。

.DESCRIPTION
  与 infra/scripts/dev-pg.sh 子命令一一对应，两种来源：

    1) cluster（默认，推荐）：InitDB 一个只用 127.0.0.1:5433 的私有集群，
       数据在 infra\.pgdev\data，删掉即清空，不碰系统里别的库。
    2) service：复用系统已装的 PostgreSQL 服务（默认 5432），只建角色/库。

.EXAMPLE
  .\scripts\dev-pg.ps1 up
  .\scripts\dev-pg.ps1 status
  .\scripts\dev-pg.ps1 bootstrap
  $env:PG_MODE='service'; .\scripts\dev-pg.ps1 up

.NOTES
  本文件必须存为 UTF-8 with BOM：Windows PowerShell 5.1 会把无 BOM 的脚本按 ANSI(GBK)
  读取，中文提示会变乱码并导致语法错误。用编辑器改名/另存时注意保持 BOM。

  若提示「禁止运行脚本」，用：
    powershell -ExecutionPolicy Bypass -File .\scripts\dev-pg.ps1 up
  或在当前会话：Set-ExecutionPolicy -Scope Process Bypass
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

# 本脚本大量调用原生 exe（psql / pg_ctl / initdb），它们会把 NOTICE、连接错误写到 stderr。
# Windows PowerShell 5.1 在 ErrorActionPreference=Stop 下会把原生 stderr 变成终止性错误，
# 所以这里用 Continue，由 $LASTEXITCODE 与显式 Fail 判断成败。
$ErrorActionPreference = 'Continue'

# ── 路径 ──────────────────────────────────────────────────────
$ScriptDir = $PSScriptRoot                       # infra\scripts
$InfraDir  = Split-Path -Parent $ScriptDir       # infra
$RepoDir   = Split-Path -Parent $InfraDir        # 仓库根
$ApiDir    = Join-Path $RepoDir 'apps\api'
$EnvFile   = Join-Path $InfraDir '.env'

function Write-Info([string]$Message) { Write-Host "[dev-pg] $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host "[dev-pg] $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "[dev-pg] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host "[dev-pg] $Message" -ForegroundColor Red
    exit 1
}

# 环境变量优先，其次 infra\.env，最后默认值——与 compose / dev-db.sh 一致
function Get-EnvValue([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and (Test-Path -LiteralPath $EnvFile)) {
        $pattern = '^' + [regex]::Escape($Name) + '='
        $line = Get-Content -LiteralPath $EnvFile -Encoding UTF8 |
            Where-Object { $_ -match $pattern } | Select-Object -Last 1
        if ($line) {
            $value = $line.Substring($Name.Length + 1).Trim()
            $value = $value.Trim('"').Trim("'")
        }
    }
    if ($value) { return $value }
    return $Default
}

$PgMode = Get-EnvValue 'PG_MODE' 'cluster'
$PgUser = Get-EnvValue 'DEV_POSTGRES_USER' 'hanyue'
$PgPass = Get-EnvValue 'DEV_POSTGRES_PASSWORD' 'hanyue_dev_pwd'
$PgDb   = Get-EnvValue 'DEV_POSTGRES_DB' 'hanyue'
$PgHost = '127.0.0.1'

switch ($PgMode) {
    'cluster' {
        $Port    = Get-EnvValue 'DEV_PG_PORT' '5433'
        $DataDir = Get-EnvValue 'PG_DATA_DIR' (Join-Path $InfraDir '.pgdev\data')
    }
    'service' {
        $Port    = Get-EnvValue 'DEV_PG_PORT' '5432'
        $DataDir = ''
    }
    default { Fail "PG_MODE 只能是 cluster 或 service（当前：$PgMode）" }
}
$PgDevDir = Join-Path $InfraDir '.pgdev'
$LogFile  = Join-Path $PgDevDir 'server.log'
$DatabaseUrl = "postgresql+psycopg://${PgUser}:${PgPass}@${PgHost}:${Port}/${PgDb}"

# ── 定位 PostgreSQL 可执行文件 ─────────────────────────────────
function Resolve-PgBin {
    if ($env:PG_BIN) {
        $candidate = $env:PG_BIN.TrimEnd('\', '/')
        if ($candidate -notmatch '[\\/]bin$') { $candidate = Join-Path $candidate 'bin' }
        if (-not (Test-Path -LiteralPath (Join-Path $candidate 'psql.exe'))) {
            Fail "PG_BIN 里找不到 psql.exe：$candidate"
        }
        return $candidate
    }
    $cmd = Get-Command psql.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command psql -ErrorAction SilentlyContinue }
    if ($cmd) { return (Split-Path -Parent $cmd.Source) }

    $bases = @("$env:ProgramFiles\PostgreSQL", "${env:ProgramFiles(x86)}\PostgreSQL")
    $found = @()
    foreach ($base in $bases) {
        if (-not $base -or -not (Test-Path -LiteralPath $base)) { continue }
        Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+' } |
            ForEach-Object { $found += (Join-Path $_.FullName 'bin') }
    }
    if ($found.Count -eq 0) { return $null }
    # 版本号最大的排最后，取它
    $best = $found | Sort-Object { [int]([regex]::Match($_, '\\(\d+)').Groups[1].Value) } | Select-Object -Last 1
    if (Test-Path -LiteralPath (Join-Path $best 'psql.exe')) { return $best }
    return $null
}

function Initialize-PgBin {
    $script:PgBin = Resolve-PgBin
    if (-not $script:PgBin) {
        Fail @'
找不到 PostgreSQL（psql.exe）。安装方式：
  winget install PostgreSQL.PostgreSQL.17
  或官网安装包 https://www.postgresql.org/download/windows/
已装在非默认目录时用 PG_BIN 指定，例如：$env:PG_BIN='D:\PG\17\bin'
'@
    }
    $script:Psql      = Join-Path $script:PgBin 'psql.exe'
    $script:PgIsReady = Join-Path $script:PgBin 'pg_isready.exe'
    if ($PgMode -eq 'cluster') {
        $script:PgCtl  = Join-Path $script:PgBin 'pg_ctl.exe'
        $script:InitDb = Join-Path $script:PgBin 'initdb.exe'
        foreach ($exe in @($script:PgCtl, $script:InitDb)) {
            if (-not (Test-Path -LiteralPath $exe)) {
                Fail "缺少 $exe —— 需要完整的 PostgreSQL 服务端安装（不只是客户端）"
            }
        }
    }
    Write-Info "PostgreSQL 客户端：$script:PgBin"
}

# ── psql 封装 ────────────────────────────────────────────────
# Windows 上 psql 默认按本地代码页（GBK）解码输入，读 UTF-8 的 .sql 会报
# "character with byte sequence ... has no equivalent in encoding UTF8"，这里固定 UTF8。
function Invoke-PsqlInternal {
    param(
        [string]$AsUser,
        [string]$Password,
        [string]$Database,
        [string[]]$PsqlArgs = @()
    )
    $oldPassword = $env:PGPASSWORD
    $oldEncoding = $env:PGCLIENTENCODING
    $oldConsole = [Console]::OutputEncoding
    $env:PGPASSWORD = $Password
    $env:PGCLIENTENCODING = 'UTF8'
    try {
        # psql 的 NOTICE/中文提示是 UTF-8，控制台默认 GBK → 乱码，这里对齐
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $base = @('-h', $PgHost, '-p', $Port, '-U', $AsUser, '-d', $Database, '-v', 'ON_ERROR_STOP=1', '-P', 'pager=off')
        # 先收进变量：直接 2>&1 会让 PowerShell 把 stderr 当错误记录刷一屏红字
        $output = & $script:Psql @base @PsqlArgs 2>&1
        if ($LASTEXITCODE -ne 0) {
            $output | ForEach-Object { Write-Host $_ }
        }
        return $LASTEXITCODE
    }
    finally {
        [Console]::OutputEncoding = $oldConsole
        $env:PGPASSWORD = $oldPassword
        $env:PGCLIENTENCODING = $oldEncoding
    }
}

function Get-Scalar {
    param([string]$AsUser, [string]$Password, [string]$Database, [string]$Sql)
    $oldPassword = $env:PGPASSWORD
    $oldEncoding = $env:PGCLIENTENCODING
    $env:PGPASSWORD = $Password
    $env:PGCLIENTENCODING = 'UTF8'
    try {
        $out = & $script:Psql -h $PgHost -p $Port -U $AsUser -d $Database -tAc $Sql 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($out | Out-String).Trim()
    }
    finally {
        $env:PGPASSWORD = $oldPassword
        $env:PGCLIENTENCODING = $oldEncoding
    }
}

function Test-ServerReady {
    & $script:PgIsReady -h $PgHost -p $Port *> $null
    return ($LASTEXITCODE -eq 0)
}

# ── cluster 模式 ─────────────────────────────────────────────
function Test-ClusterInitialized { return (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION')) }

function Test-ClusterRunning {
    & $script:PgCtl -D $DataDir status *> $null
    return ($LASTEXITCODE -eq 0)
}

function Set-ClusterConf {
    # 幂等：initdb 只写默认配置，端口/监听地址由这里补；用注释当标记，重复执行不追加
    # 标记保持纯 ASCII，避免把中文写进 postgresql.conf 引出编码问题
    $conf = Join-Path $DataDir 'postgresql.conf'
    if (-not (Test-Path -LiteralPath $conf)) { return }
    $text = [System.IO.File]::ReadAllText($conf)
    if ($text -match 'hanyue dev cluster') { return }
    $block = @"

# ---- hanyue dev cluster (added by infra/scripts/dev-pg.ps1; run reset to regenerate) ----
port = $Port
listen_addresses = '127.0.0.1'
timezone = 'Asia/Shanghai'
log_timezone = 'Asia/Shanghai'
log_min_duration_statement = 500
"@
    $block = $block -replace "`r`n", "`n"
    [System.IO.File]::AppendAllText($conf, $block, (New-Object System.Text.UTF8Encoding($false)))
}

function Initialize-Cluster {
    if (Test-ClusterInitialized) {
        Set-ClusterConf          # 上次 initdb 成功但中途被打断时，配置块可能没写进去
        return
    }
    Write-Info "首次初始化集群：$DataDir（端口 $Port，仅监听 127.0.0.1）"
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $pwFile = Join-Path $PgDevDir '.initdb-pw'
    [System.IO.File]::WriteAllText($pwFile, $PgPass, (New-Object System.Text.UTF8Encoding($false)))
    try {
        # 与容器版一致：UTF8 + C 排序（--no-locale），避免本地/线上排序规则不同
        & $script:InitDb -D $DataDir -U $PgUser "--pwfile=$pwFile" '--encoding=UTF8' '--no-locale' '--auth-host=scram-sha-256' '--auth-local=trust' *> $null
        if ($LASTEXITCODE -ne 0) {
            Fail "initdb 失败（目录不可写或权限不足）：$DataDir`n可先删掉该目录重试：Remove-Item -Recurse -Force '$PgDevDir'"
        }
    }
    finally {
        Remove-Item -LiteralPath $pwFile -Force -ErrorAction SilentlyContinue
    }
    Set-ClusterConf
    Write-Ok '集群已创建'
}

function Start-Cluster {
    Initialize-Cluster
    if (Test-ClusterRunning) {
        if (Test-ServerReady) {
            Write-Ok "集群已在运行（端口 $Port）"
            return
        }
        # 上次被强杀会留下 postmaster.pid，pg_ctl status 仍报「在运行」，但端口没响应
        Write-Warn2 '发现残留的 postmaster.pid（上次异常退出），继续尝试启动'
    }
    if (Test-ServerReady) {
        Fail "端口 $Port 已被别的 PostgreSQL 占用（不是本脚本的集群）。`n换端口：`$env:DEV_PG_PORT='5434'; .\scripts\dev-pg.ps1 up"
    }
    # 端口没响应 = 确认是残留 pid 文件，清掉再起，免得 pg_ctl 反复提示
    $stalePid = Join-Path $DataDir 'postmaster.pid'
    if (Test-Path -LiteralPath $stalePid) {
        Remove-Item -LiteralPath $stalePid -Force -ErrorAction SilentlyContinue
    }
    Write-Info '启动集群'
    # 关键：不能直接 `& pg_ctl ... start`，也不能让它写进 PowerShell 管道。
    # pg_ctl 派生的 postmaster 常驻且会继承调用方的 stdout 管道句柄，于是
    # 「脚本 | Select-Object」「2>&1 | ...」这类管道永远等不到 EOF，表现为卡死。
    # Start-Process 走 ShellExecute，新进程不继承当前管道；再用轮询判断就绪。
    $pgArgs = @('-D', "`"$DataDir`"", '-l', "`"$LogFile`"", '-w', '-t', '60', 'start')
    try {
        Start-Process -FilePath $script:PgCtl -ArgumentList $pgArgs -WindowStyle Hidden | Out-Null
    }
    catch {
        Fail "调不起 pg_ctl：$($_.Exception.Message)"
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not (Test-ServerReady)) {
        Start-Sleep -Milliseconds 400
    }
    if (-not (Test-ServerReady)) {
        Write-Warn2 "启动失败，最近日志（$LogFile）："
        if (Test-Path -LiteralPath $LogFile) { Get-Content -LiteralPath $LogFile -Tail 30 | ForEach-Object { Write-Host "  $_" } }
        $tail = ''
        if (Test-Path -LiteralPath $LogFile) { $tail = Get-Content -LiteralPath $LogFile -Tail 40 -Raw }
        if ($tail -match 'could not create any TCP/IP sockets|could not bind') {
            Write-Warn2 @'
postgres 无法监听端口（Permission denied）。常见原因：安全软件/防火墙禁止 postgres.exe 监听，
或端口落在 Windows 保留区间（netsh int ipv4 show excludedportrange protocol=tcp 可查）。
两条出路：
  a) 换端口：$env:DEV_PG_PORT='5434'; .\scripts\dev-pg.ps1 up
  b) 直接用系统已装的 PG 服务：$env:PG_MODE='service'; .\scripts\dev-pg.ps1 up
'@
        }
        Fail 'pg_ctl start 失败'
    }
    Write-Ok "已启动（数据目录 $DataDir）"
}

function Stop-Cluster {
    if (-not (Test-ClusterRunning)) { Write-Ok '本来就没在运行'; return }
    & $script:PgCtl -D $DataDir -m fast -w stop *> $null
    Write-Ok "已停止（数据保留在 $DataDir；下次 up 接着用）"
}

# ── 库 / 角色 / 初始化 SQL ───────────────────────────────────
function Invoke-InitSql {
    $sql = Join-Path $InfraDir 'postgres\init\01-init.sql'
    if (-not (Test-Path -LiteralPath $sql)) { Fail "找不到 $sql" }
    Write-Info '执行 postgres\init\01-init.sql（时区 / 语句超时 / pg_trgm）'
    $code = Invoke-PsqlInternal -AsUser $PgUser -Password $PgPass -Database $PgDb -PsqlArgs @('-q', '-f', $sql)
    if ($code -ne 0) { Fail '01-init.sql 执行失败（见上面的 psql 报错）' }
    Write-Ok '库级设置已就绪'
}

function Initialize-Database {
    $exists = Get-Scalar -AsUser $PgUser -Password $PgPass -Database 'postgres' -Sql "SELECT 1 FROM pg_database WHERE datname = '$PgDb'"
    if ($exists -eq '1') {
        Write-Ok "库 $PgDb 已存在"
        return
    }
    Write-Info "创建库 $PgDb（owner=$PgUser）"
    $code = Invoke-PsqlInternal -AsUser $PgUser -Password $PgPass -Database 'postgres' -PsqlArgs @('-c', "CREATE DATABASE `"$PgDb`" OWNER `"$PgUser`"")
    if ($code -ne 0) { Fail '建库失败' }
    Write-Ok "库 $PgDb 已创建"
    Invoke-InitSql
}

# ── service 模式 ─────────────────────────────────────────────
function Get-SuperPassword {
    if ($env:PGSUPERUSER_PASSWORD) { return $env:PGSUPERUSER_PASSWORD }
    $secure = Read-Host -AsSecureString "PostgreSQL 超级用户 $PgSuperUser 的密码"
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $plain) { Fail '超级用户密码不能为空' }
    return $plain
}

function Initialize-ServiceDatabase {
    $script:PgSuperUser = Get-EnvValue 'PGSUPERUSER' 'postgres'
    $superPass = Get-SuperPassword
    $sql = Join-Path $InfraDir 'postgres\create-role-and-db.sql'
    if (-not (Test-Path -LiteralPath $sql)) { Fail "找不到 $sql" }
    Write-Info "在系统实例（$PgHost`:$Port）上确保角色 $PgUser / 库 $PgDb 存在"
    $code = Invoke-PsqlInternal -AsUser $script:PgSuperUser -Password $superPass -Database 'postgres' `
        -PsqlArgs @('-q', '-f', $sql, '-v', "app_user=$PgUser", '-v', "app_db=$PgDb", '-v', "app_password=$PgPass")
    if ($code -ne 0) { Fail "建角色/库失败：检查超级用户密码与端口 $Port" }
    Invoke-InitSql
}

# ── Python（bootstrap / write-env 用）─────────────────────────
function Get-PythonPath {
    foreach ($candidate in @((Join-Path $ApiDir '.venv\Scripts\python.exe'), (Join-Path $ApiDir '.venv\bin\python'))) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Initialize-Python {
    $script:Python = Get-PythonPath
    if (-not $script:Python) {
        Fail "找不到 $ApiDir\.venv —— 先建虚拟环境并装依赖（见 docs\setup.md 方式 A）"
    }
    & $script:Python -c "import psycopg" *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "当前 venv 缺 PG 驱动：`"$script:Python`" -m pip install -r requirements-prod.txt"
    }
}

# python 默认按 UTF-8 输出，控制台是 GBK，中文会变乱码——调用期间临时切控制台编码
function Invoke-Python {
    param([string[]]$PyArgs)
    $oldOut = [Console]::OutputEncoding
    $oldIo = $env:PYTHONIOENCODING
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $env:PYTHONIOENCODING = 'utf-8'
        & $script:Python @PyArgs
    }
    finally {
        [Console]::OutputEncoding = $oldOut
        $env:PYTHONIOENCODING = $oldIo
    }
}

# ── 子命令 ───────────────────────────────────────────────────
function Show-Urls {
    Write-Host ''
    Write-Host '写进 apps\api\.env（或直接用 .\scripts\dev-pg.ps1 write-env）：' -ForegroundColor White
    Write-Host "  DATABASE_URL=$DatabaseUrl"
    Write-Host 'Redis 可选：不配 REDIS_URL 时任务由 API 进程内线程池执行，无需启动 Worker。' -ForegroundColor DarkGray
    Write-Host 'psql：.\scripts\dev-pg.ps1 psql' -ForegroundColor DarkGray
    Write-Host ''
}

function Invoke-Up {
    Initialize-PgBin
    if ($PgMode -eq 'cluster') {
        Start-Cluster
    }
    else {
        if (-not (Test-ServerReady)) {
            Fail @"
系统 PostgreSQL 没在 $PgHost`:$Port 就绪。启动方式（管理员 PowerShell）：
  Start-Service postgresql-x64-17
  或 服务管理器 → postgresql-x64-17 → 启动
"@
        }
        Write-Ok "系统实例已就绪（$PgHost`:$Port）"
        Initialize-ServiceDatabase
    }
    Initialize-Database
    if ($Rest -contains '--write-env') { Invoke-WriteEnv -Quiet }
    Show-Urls
}

function Invoke-Down {
    Initialize-PgBin
    if ($PgMode -eq 'cluster') { Stop-Cluster; return }
    Write-Warn2 'service 模式不动系统服务。要停请用管理员 PowerShell：Stop-Service postgresql-x64-17'
}

function Invoke-Status {
    Initialize-PgBin
    Write-Host ("模式 {0}    端口 {1}" -f $PgMode, $Port)
    if ($PgMode -eq 'cluster') {
        Write-Host ("数据目录 {0}" -f $(if ($DataDir) { $DataDir } else { '<未初始化>' }))
        $pidFile = Join-Path $DataDir 'postmaster.pid'
        $state = '未运行'
        if ((Test-Path -LiteralPath $pidFile) -and (Test-ServerReady)) { $state = '运行中' }
        elseif (Test-Path -LiteralPath $pidFile) { $state = 'pid 文件残留（进程已退出或未响应）' }
        Write-Host "进程 $state"
    }
    $probe = Get-Scalar -AsUser $PgUser -Password $PgPass -Database $PgDb -Sql 'SELECT 1'
    if ($probe -eq '1') {
        $tables = Get-Scalar -AsUser $PgUser -Password $PgPass -Database $PgDb `
            -Sql "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        Write-Host "连接 正常（$PgUser/$PgDb，public 下 $tables 张表）"
        if ($tables -eq '0') { Write-Warn2 '库里还没有表：.\scripts\dev-pg.ps1 bootstrap 建表并创建首个账号' }
    }
    elseif (Test-ServerReady) {
        Write-Host "连接 服务在 $PgHost`:$Port 上，但应用账号 $PgUser 连不上"
        Write-Warn2 "多半是角色/库还没建，或密码不对：.\scripts\dev-pg.ps1 up"
    }
    else {
        Write-Host "连接 端口 $Port 上没有 PostgreSQL"
        Write-Warn2 'cluster 模式先 .\scripts\dev-pg.ps1 up；service 模式先启动系统服务（postgresql-x64-17）'
    }
    Write-Host ''
    Write-Host "DATABASE_URL=$DatabaseUrl"
}

function Invoke-Psql {
    Initialize-PgBin
    if (-not (Test-ServerReady)) { Fail "连接不上 $PgHost`:$Port，先 .\scripts\dev-pg.ps1 up" }
    Write-Info "psql $PgHost`:$Port/$PgDb（\q 退出）"
    # 直接调用而不是走封装：交互式 psql 需要真正的控制台直通
    $oldPassword = $env:PGPASSWORD
    $oldEncoding = $env:PGCLIENTENCODING
    $oldPager = $env:PAGER
    $oldConsole = [Console]::OutputEncoding
    $env:PGPASSWORD = $PgPass
    $env:PGCLIENTENCODING = 'UTF8'
    # Git Bash 常把 PAGER 设成 cat，Windows 的 psql 会去执行 cmd /c cat 并报错
    $env:PAGER = ''
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    try {
        & $script:Psql -h $PgHost -p $Port -U $PgUser -d $PgDb @Rest
    }
    finally {
        [Console]::OutputEncoding = $oldConsole
        $env:PGPASSWORD = $oldPassword
        $env:PGCLIENTENCODING = $oldEncoding
        $env:PAGER = $oldPager
    }
}

function Invoke-Reset {
    if ($PgMode -ne 'cluster') {
        Fail 'service 模式的库不属于本脚本，拒绝删除。要清空请手工 DROP DATABASE'
    }
    Initialize-PgBin
    if (-not (Test-ClusterInitialized)) { Write-Ok '集群还没创建，无需删除'; return }
    Write-Warn2 "这会永久删除 $PgDevDir（库 $PgDb 的全部数据）。其他 PG 实例不受影响。"
    $answer = Read-Host '确认请输入 yes'
    if ($answer -ne 'yes') { Fail '已取消' }
    if (Test-ClusterRunning) { & $script:PgCtl -D $DataDir -m fast -w stop *> $null }
    if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) {
        Fail "$DataDir 不像 PG 数据目录，拒绝删除"
    }
    try {
        Remove-Item -LiteralPath $PgDevDir -Recurse -Force -ErrorAction Stop
    }
    catch {
        Fail "删除 $PgDevDir 失败（文件被占用？先停掉 postgres 进程）：$($_.Exception.Message)"
    }
    Write-Ok '已删除；下次 up 会重新 initdb 并重跑 01-init.sql'
}

function Invoke-InitSqlOnly {
    Initialize-PgBin
    if (-not (Test-ServerReady)) { Fail "连接不上 $PgHost`:$Port，先 .\scripts\dev-pg.ps1 up" }
    Invoke-InitSql
}

function Invoke-Bootstrap {
    Invoke-Up
    Initialize-Python
    Write-Info '建表 + 补列（等价 API 启动时的 init_db）'
    $oldUrl = $env:DATABASE_URL
    $env:DATABASE_URL = $DatabaseUrl
    try {
        Push-Location $ApiDir
        try {
            Invoke-Python @('scripts/create_user.py', 'list') | Out-Null
            if ($LASTEXITCODE -ne 0) { Fail '建表失败：上面是 apps\api\scripts\create_user.py 的报错' }
        }
        finally { Pop-Location }

        $email = $env:BOOTSTRAP_EMAIL
        if (-not $email) { $email = Read-Host '首个账号邮箱' }
        if (-not $email) { Fail '邮箱不能为空' }
        if (-not $env:HANYUE_NEW_PASSWORD) {
            $secure = Read-Host -AsSecureString '密码（至少 8 位）'
            $env:HANYUE_NEW_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        }
        Write-Info "创建账号 $email"
        Push-Location $ApiDir
        try {
            Invoke-Python @('scripts/create_user.py', 'create', '--email', $email)
            if ($LASTEXITCODE -ne 0) { Fail '创建账号失败' }
        }
        finally { Pop-Location }
    }
    finally {
        $env:DATABASE_URL = $oldUrl
    }
    Write-Ok '完成：启动 uvicorn 即可用该账号登录'
    Show-Urls
}

function Invoke-ImportSqlite {
    $source = Join-Path $ApiDir 'hanyue.db'
    if ($Rest.Count -gt 0) { $source = $Rest[0] }
    if (-not (Test-Path -LiteralPath $source)) { Fail "找不到 SQLite 文件：$source" }
    Invoke-Up | Out-Null
    Initialize-Python
    Write-Warn2 "把 $source 导入 $PgDb（目标表已有数据时默认跳过，详见迁移脚本 --help）"
    $oldUrl = $env:DATABASE_URL
    $env:DATABASE_URL = $DatabaseUrl
    try {
        Invoke-Python @((Join-Path $InfraDir 'scripts\migrate_sqlite_to_pg.py'), '--sqlite', $source)
        if ($LASTEXITCODE -ne 0) { Fail '迁移失败（见上面的报错）' }
    }
    finally { $env:DATABASE_URL = $oldUrl }
}

function Invoke-WriteEnv {
    param([switch]$Quiet)
    $target = Join-Path $ApiDir '.env'
    if (-not (Test-Path -LiteralPath $target)) {
        Copy-Item -LiteralPath (Join-Path $ApiDir '.env.example') -Destination $target
    }
    # 保留原换行风格：Windows 上 .env 常是 CRLF，按文本读写会整片改成 LF
    $raw = [System.IO.File]::ReadAllText($target)
    $nl = if ($raw -match "`r`n") { "`r`n" } else { "`n" }
    $lines = $raw -split [regex]::Escape($nl)
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^DATABASE_URL=') {
            $lines[$i] = "DATABASE_URL=$DatabaseUrl"
            $done = $true
        }
    }
    if (-not $done) { $lines = @("DATABASE_URL=$DatabaseUrl") + $lines }
    [System.IO.File]::WriteAllText($target, ($lines -join $nl), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "已更新 $target : DATABASE_URL=$DatabaseUrl"
    Write-Ok '改完记得重启 uvicorn（配置只在进程启动时载入）'
}

function Show-Usage {
    @'
infra/scripts/dev-pg.ps1 —— 本机原生 PostgreSQL 管理（Windows PowerShell 版，不需要 Git Bash）

子命令：
  up [--write-env]     初始化（首次）+ 启动 + 建库，打印 DATABASE_URL
  down                 停止集群（数据保留；service 模式只提示）
  status               模式、端口、进程、连接、public 表数量
  psql                 用应用账号进入 psql（\q 退出）
                       传参数：psql --% -c "..." 或 psql "-c" "..."
                       内联 SQL 里带中文请写成 .sql 文件用 -f 执行（命令行按 GBK 传参）
  reset                删掉 infra\.pgdev 重建（二次确认，service 模式拒绝）
  url                  只打印连接串
  init-sql             只重跑 postgres\init\01-init.sql
  bootstrap            up + 建表 + 创建首个账号
  import-sqlite [文件] 把旧 SQLite 数据搬进来（默认 apps\api\hanyue.db）
  write-env            把连接串写进 apps\api\.env 的 DATABASE_URL

环境变量（也可写进 infra\.env）：
  PG_MODE=cluster|service     默认 cluster
  PG_BIN=C:\Program Files\PostgreSQL\17\bin
  DEV_PG_PORT                 默认 cluster 5433 / service 5432
  DEV_POSTGRES_USER / DEV_POSTGRES_PASSWORD / DEV_POSTGRES_DB
  PGSUPERUSER / PGSUPERUSER_PASSWORD   service 模式建角色用
  BOOTSTRAP_EMAIL / HANYUE_NEW_PASSWORD  bootstrap 免交互

示例（Windows PowerShell）：
  .\scripts\dev-pg.ps1 up
  .\scripts\dev-pg.ps1 status
  $env:PG_MODE='service'; .\scripts\dev-pg.ps1 up
'@ | Write-Host
}

switch ($Command.ToLower()) {
    'up'            { Invoke-Up }
    'down'          { Invoke-Down }
    'status' { Invoke-Status }
    'ps'            { Invoke-Status }
    'psql'          { Invoke-Psql }
    'reset'         { Invoke-Reset }
    'url'           { Write-Host "DATABASE_URL=$DatabaseUrl" }
    'init-sql'      { Invoke-InitSqlOnly }
    'bootstrap'     { Invoke-Bootstrap }
    'import-sqlite' { Invoke-ImportSqlite }
    'write-env'     { Invoke-WriteEnv }
    'help'          { Show-Usage }
    '-h'            { Show-Usage }
    '--help'        { Show-Usage }
    default         { Fail "未知子命令：$Command（用 help 查看）" }
}
