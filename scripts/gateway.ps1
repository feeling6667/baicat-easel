$ErrorActionPreference = 'Stop'
# 不要用 $Profile：它是 PowerShell 的自动变量（当前用户的配置脚本路径），
# 覆写它会遮蔽自动变量，PSScriptAnalyzer 也会直接报警。
$ProfileName = 'easel'
$Root = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $env:TEMP 'easel-gateway.log'
$ErrorLogFile = Join-Path $env:TEMP 'easel-gateway.error.log'
$ConfigDir = Join-Path $HOME ".openclaw-$ProfileName"

# 对原生命令做 stderr 重定向必须先把 ErrorActionPreference 降到 Continue：
# 本文件顶部是 'Stop'，而 PS 5.1 在 Stop 下会把原生命令 stderr 的每一行包成
# ErrorRecord 抛出 NativeCommandError —— 脚本级终止，轮不到读 $LASTEXITCODE。
function Invoke-NativeCapturePS([string]$Exe, [string[]]$Arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $Exe @Arguments 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return [pscustomobject]@{ Ok = ($code -eq 0); Output = $out; Code = $code }
}

# ---- gateway 端口：问 Easel 的解析器，不要在这里再抄一份 ----------------------
# 这里曾经用 PowerShell 重新实现过一遍 OpenClaw 的 resolveGatewayPort（FNV-1a 哈希、
# 配置文件读取、环境变量优先级）。那样一来同一个端口就有三份独立实现：这里、
# easel/gateway_endpoint.py、以及 OpenClaw 自己。三份只要有一份跟不上版本变化就错配，
# 而下面的 start 带 --force —— 错配的后果不是探不通，而是**杀掉占用那个端口的别人家
# gateway**（Linux 侧实测发生过：老版 OpenClaw 不认 profile 哈希端口、照旧绑 18789，
# --force 就把默认 profile 的健康进程干掉了）。单一真相源在 Python 那边，这里只负责问。
function Get-EaselPython {
    # 优先用项目内 venv：gateway.ps1 可能被 easel CLI 以外的方式调起，此时 PATH 上
    # 未必有正确的 python。回退链与 agents/sync.sh 一致。
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return @($venv, @()) }
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py) { return @($py, @()) }
    $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyLauncher) { return @($pyLauncher, @('-3')) }
    return @($null, @())
}

$pyExe, $pyPre = Get-EaselPython
if (-not $pyExe) {
    Write-Error '[easel] 找不到 Python，无法解析 gateway 端口；请先运行 setup.ps1。'
    exit 1
}
# PS 5.1 按 [Console]::OutputEncoding 解码原生命令 stdout（中文系统是 OEM 936），
# 而 Python 输出 UTF-8；这里只取一个数字，仍统一设好编码以免将来扩展时踩坑。
$prevEnc = [Console]::OutputEncoding
$prevPyPath = $env:PYTHONPATH
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONPATH = $Root
    $probe = Invoke-NativeCapturePS $pyExe ($pyPre + @('-c',
        'from easel.gateway_endpoint import resolve_gateway_port as p; print(p())'))
} finally {
    [Console]::OutputEncoding = $prevEnc
    $env:PYTHONPATH = $prevPyPath
}
$Port = 0
if ($probe.Ok) { [void][int]::TryParse(($probe.Output -split "`n" | Where-Object { $_ } | Select-Object -Last 1).Trim(), [ref]$Port) }
# 解析不出来就明着失败，而不是退到一个猜的端口 —— 「悄悄探错端口」正是要根治的病。
if ($Port -le 0) {
    Write-Error "[easel] 无法解析 gateway 端口（需要能 import easel）。手动查：`n" +
                "        `$env:PYTHONPATH='$Root'; $pyExe -c ""from easel.gateway_endpoint import describe; print(describe())"""
    exit 1
}
# 把解析结果**无条件**钉给 OpenClaw：gateway 进程只认 OPENCLAW_GATEWAY_PORT。
# 以前只在 EASEL_GATEWAY_PORT 显式设置时才透传，于是平时探测端口与实际绑定端口是
# 两份独立推导。无条件透传后两边同源，--force 最多只会动我们自己这个端口。
$env:OPENCLAW_GATEWAY_PORT = "$Port"

# ══════════════════════════════════════════════════════════════════════════════
#  以下到 switch 之前，全部是**本机专属补丁**（官方 v0.3.0 代码里没有）。
#  为什么需要：官方 start 分支用 `Start-Process powershell -Command "openclaw …"`，
#  而本机 PATH 上的 openclaw 是 .cmd shim（CreateProcess 跑不了），且本机 Node 的
#  spawnSync 有一个 EBUSY 缺陷会让 gateway 直接起不来。详见各段注释。
#  这三段都只影响本机，升级时若 gateway.ps1 被官方覆盖，照抄本段即可。
# ══════════════════════════════════════════════════════════════════════════════

# ── 本机补丁 1/3：运行时定位改为绝对路径（不再依赖 PATH）───────────────────
# PATH 里的 `openclaw` 是 .cmd shim，CreateProcess 直接跑不了；而且外部进程
# （WorkBuddy / 计划任务）的 PATH 未必带 node24，靠 PATH 解析会静默失败
# （曾导致 `restart` 看起来成功、PID 却纹丝不动）。
$NodeDir  = 'C:\Users\Administrator\.easel-tools\node24'
$NodeExe  = Join-Path $NodeDir 'node.exe'
$OpenClaw = Join-Path $NodeDir 'node_modules\openclaw\openclaw.mjs'

# ── 本机补丁 2/3：Node spawnSync EBUSY 的绕行补丁 ─────────────────────────
# 现象：本机 spawnSync 只要需要为子进程创建 stdin 管道，就必然抛 EBUSY
#       （Node 22 / 24 都一样；异步 spawn 正常；系统层 CreatePipe 正常）。
# 影响：openclaw 启动时要 spawnSync 一个「只读读状态库」的 worker，被拒后网关
#       直接退出，而它自己的报错格式化代码有 bug，把真实原因吞成了
#       `Cannot read properties of undefined (reading 'trim')`。
# 绕行：spawn-shim.cjs 把「调用方本来就不喂数据」的同步调用的 stdin 由
#       'pipe' 改成 'ignore'（对子进程而言都是立刻 EOF，行为等价）。
# 卸载：删掉下面 $env:NODE_OPTIONS 这一行即可，不改动任何已安装文件。
$SpawnShim = 'C:\Users\Administrator\.easel-tools\spawn-shim.cjs'
if (Test-Path $SpawnShim) {
    if ($env:NODE_OPTIONS) { $env:NODE_OPTIONS = "$($env:NODE_OPTIONS) --require $SpawnShim" }
    else { $env:NODE_OPTIONS = "--require $SpawnShim" }
}

# ── 本机补丁 3/3：收敛大小写重名的环境变量键 ──────────────────────────────
# PowerShell 5.1 的 Start-Process 一旦带上 -RedirectStandard*，就会因为环境变量里
# 存在大小写重名的键（例如同时有 Path 和 PATH、http_proxy 和 HTTP_PROXY）而抛
#   “已添加项。字典中的关键字:'PATH'所添加的关键字:'Path'”
# 干净的桌面环境不会出现，但被外层工具/脚本注入过的环境会出现（实测复现）。
# 这里把每组大小写重名的键收敛成一个，保留第一个。
function Repair-DuplicateEnvKeys {
    $keys = @([Environment]::GetEnvironmentVariables('Process').Keys)
    $groups = $keys | Group-Object { $_.ToUpperInvariant() } | Where-Object { $_.Count -gt 1 }
    foreach ($g in $groups) {
        $keep = $g.Group[0]
        $val = [Environment]::GetEnvironmentVariable($keep, 'Process')
        foreach ($k in $g.Group) { [Environment]::SetEnvironmentVariable($k, $null, 'Process') }
        [Environment]::SetEnvironmentVariable($keep, $val, 'Process')
    }
}

function Test-Gateway {
    try { Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

function Get-GatewayProcess {
    # 【本机补丁】官方正则要求 openclaw 出现在 --profile 之前；本机改成
    #   "…\node.exe" "…\openclaw.mjs" --profile easel gateway run …
    # 后匹配不上，于是 stop/restart 静默变成空操作。这里放宽为三个条件都含即可。
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'openclaw' -and
            $_.CommandLine -match "--profile\s+$ProfileName" -and
            $_.CommandLine -match 'gateway'
        } |
        Select-Object -First 1
}

function Stop-Gateway {
    $process = Get-GatewayProcess
    if ($process) { Stop-Process -Id $process.ProcessId -Force; Write-Host '[easel] Gateway stopped' }
    else { Write-Host '[easel] Gateway was not running' }
}

switch ($args[0]) {
    'start' {
        if (Test-Gateway) { Write-Host '[easel] Gateway already running'; break }
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
        Write-Host "[easel] Starting Easel gateway (profile: $ProfileName, port: $Port)..."
        # 【本机补丁】官方这里用 `openclaw`（PATH .cmd shim）+ powershell -Command，
        # 本机跑不通。改为绝对路径 node + openclaw.mjs 直接起，并先收敛重名环境变量键。
        if (-not (Test-Path $NodeExe))  { Write-Error "找不到 node: $NodeExe"; exit 1 }
        if (-not (Test-Path $OpenClaw)) { Write-Error "找不到 openclaw.mjs: $OpenClaw"; exit 1 }
        # 注意：不要在这里给 $env:PATH 赋值。PS 5.1 的 $env: 提供程序在写入大小写
        # 不一致的变量名时会产生重复键（Path / PATH），随后 Start-Process 会抛
        # “已添加项。字典中的关键字:'PATH'”。既然 node 与 openclaw.mjs 都用了绝对
        # 路径，PATH 保持原样即可。
        Repair-DuplicateEnvKeys
        # ── 本机补丁 4/4：开启 gateway 的原始流（token / 思考流）日志 ─────────────
        # 为什么需要：`openclaw agent` 这个瘦客户端**不写**原始流（源码 run-*.mjs:1724
        # 那条只在 `gateway run` 命令里执行），只有常驻 gateway 才会写。web 侧 CLI 模式
        # 靠 tail 这个文件拿 token 与思考过程；不开启 → 文件永远不存在 → 前端一直卡在
        # 「🧠 正在思考…」，模型其实答完了也一个字都推不出来（实测复现）。
        # 路径用**绝对路径 + 正斜杠**：Windows 下 `~` 不会被展开成用户目录，openclaw
        # 会照字面建一个名为 `~` 的目录；而反斜杠经 Start-Process -ArgumentList 传参时
        # 有被吞/转义的风险。正斜杠已被实测确认 openclaw 接受（日志原样回显该路径）。
        $RawStreamDir = Join-Path $ConfigDir 'logs'
        New-Item -ItemType Directory -Force -Path $RawStreamDir | Out-Null
        $RawStreamFile = Join-Path $RawStreamDir 'raw-stream.jsonl'
        try { Set-Content -LiteralPath $RawStreamFile -Value '' -Encoding ASCII -ErrorAction Stop } catch { }
        # 传给 gateway 的那一份统一转成正斜杠（仅用于命令行/环境变量，文件操作仍用反斜杠）。
        $RawStreamPath = $RawStreamFile -replace '\\', '/'
        # 同时用 env 与 CLI 参数表达（两条路径 gateway 都认；参数优先）。
        $env:OPENCLAW_RAW_STREAM = '1'
        $env:OPENCLAW_RAW_STREAM_PATH = $RawStreamPath
        $gwArgs = @($OpenClaw, '--profile', $ProfileName, 'gateway', 'run',
                    '--force', '--allow-unconfigured', '--bind', 'loopback',
                    '--raw-stream', '--raw-stream-path', $RawStreamPath)
        Start-Process -FilePath $NodeExe -ArgumentList $gwArgs -WorkingDirectory $Root `
            -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile -WindowStyle Hidden | Out-Null
        # 【本机补丁】冷启动要读并迁移状态库，实测可能超过 20 秒，给足 90 秒
        $ready = $false
        1..90 | ForEach-Object {
            if (-not $ready) {
                if (Test-Gateway) { $ready = $true }
                else { Start-Sleep -Seconds 1 }
            }
        }
        if ($ready) { Write-Host '[easel] Gateway started' }
        else { Write-Error "Gateway 启动失败；请检查 $LogFile 和 $ErrorLogFile"; exit 1 }
    }
    'stop' { Stop-Gateway }
    'restart' { Stop-Gateway; Start-Sleep -Seconds 2; & $PSCommandPath start }
    'status' {
        if (Test-Gateway) {
            # 【本机补丁】带上 PID，便于确认 restart 是否真的换过进程
            $p = Get-GatewayProcess
            if ($p) { Write-Host "[easel] Gateway running (profile: $ProfileName, port: $Port, pid: $($p.ProcessId))" }
            else    { Write-Host "[easel] Gateway running (profile: $ProfileName, port: $Port)" }
        }
        else { Write-Host "[easel] Gateway not running (profile: $ProfileName, port: $Port)" }
    }
    'logs' { Get-Content $LogFile -Wait }
    default { Write-Host 'Usage: gateway.ps1 {start|stop|restart|status|logs}'; exit 1 }
}
