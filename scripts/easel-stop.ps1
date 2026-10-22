# ============================================================
#  Easel workbench - one-click STOP
#  Triggered by the desktop shortcut "Easel 工作台 - 停止"
#  Stops gateway (18789) and web workbench (7860).
# ============================================================

$ErrorActionPreference = 'Continue'

$EaselDir  = 'C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel'
$NodeDir   = 'C:\Users\Administrator\.easel-tools\node24'
$FFmpegDir = 'C:\Users\Administrator\.easel-tools\ffmpeg\bin'
$EaselExe  = Join-Path $EaselDir '.venv\Scripts\easel.exe'

$env:PATH = "$NodeDir;$FFmpegDir;$env:PATH"

# 1) stop gateway through easel itself
if (Test-Path $EaselExe) {
  & $EaselExe gateway stop 2>&1 | Out-Null
}

# 2) stop web workbench by listening port
$pids = (& netstat -ano -p TCP 2>$null | Select-String ":7860\s+.*LISTENING") |
        ForEach-Object { ($_.ToString() -split '\s+')[-1] } |
        Where-Object { $_ -match '^\d+$' } |
        Select-Object -Unique

foreach ($p in $pids) {
  Stop-Process -Id ([int]$p) -Force -ErrorAction SilentlyContinue
}

# 3) 清理网关僵尸锁
#    网关异常退出（被强杀/系统回收）时，会在 tmp\openclaw\ 下留下
#    gateway*.lock，里面记着已死进程的 PID。下次启动网关会误判
#    "gateway already running (pid XXXX)" 并 5 秒超时退出，
#    表现就是 web 工作台报 ECONNREFUSED。这里按 PID 存活情况清理。
$LockDir = Join-Path $env:USERPROFILE '.openclaw-easel\tmp\openclaw'
if (Test-Path $LockDir) {
  Get-ChildItem -Path $LockDir -Filter 'gateway*.lock' -File -ErrorAction SilentlyContinue | ForEach-Object {
    $lockFile = $_
    $alive = $false
    try {
      $j = Get-Content -Raw -Path $lockFile.FullName | ConvertFrom-Json
      $prop = $j.PSObject.Properties['pid']
      if ($prop -and $prop.Value) {
        $lockPid = [int]$prop.Value
        if ($lockPid -gt 0) {
          $p = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
          if ($p) { $alive = $true }
        }
      }
    } catch { }
    if (-not $alive) {
      try { [System.IO.File]::Delete($lockFile.FullName) } catch { }
    }
  }
}

Start-Sleep -Seconds 1
