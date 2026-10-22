# ============================================================
#  Easel workbench - one-click START
#  Triggered by the desktop shortcut "Easel 工作台"
#  Starts gateway (18789) + web workbench (7860), then opens browser.
#  Log: C:\Users\Administrator\.easel-tools\easel-start.log
#
#  【2026-10-14 升级】与 start-easel.cmd 对齐。
#  原先这个脚本只在「端口没在监听」时才启动 —— 于是当后台正跑着
#  **旧代码**时它什么都不做，用户打开浏览器看到的还是旧界面，
#  误以为改动没生效。现在补上：
#    1) 清网关僵尸锁（原有逻辑，保留）
#    2) 起网关（若没跑），并等它就绪
#    3) 自检「原始流 + 默认模型」（scripts\check_raw_stream.py）
#       —— 这两项是"对话能正常出字"的两道命脉
#    4) 网页若在跑但是**旧代码** → 自动杀掉重启（scripts\is_web_stale.py）
#    5) 起网页 → 等 7860 就绪 → 开浏览器
#    6) 任何一项出问题 → 弹窗提示，不再静默失败
# ============================================================

$ErrorActionPreference = 'Continue'

$LOG = 'C:\Users\Administrator\.easel-tools\easel-start.log'
function Log([string]$m) {
  "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $LOG -Encoding utf8 -Append
}

$Problems = New-Object 'System.Collections.Generic.List[string]'
function Note([string]$m) { Log ("PROBLEM: " + $m); [void]$Problems.Add($m) }

Log '=== script started ==='

try {
  $EaselDir  = 'C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel'
  $NodeDir   = 'C:\Users\Administrator\.easel-tools\node24'
  $FFmpegDir = 'C:\Users\Administrator\.easel-tools\ffmpeg\bin'
  $EaselExe  = Join-Path $EaselDir '.venv\Scripts\easel.exe'
  $PyExe     = Join-Path $EaselDir '.venv\Scripts\python.exe'
  $Url       = 'http://127.0.0.1:7860'
  $GwPort    = 18789
  $WebPort   = 7860

  Log ("EaselExe = $EaselExe ; exists = " + (Test-Path $EaselExe))
  if (-not (Test-Path $EaselExe)) { Log 'FATAL: easel.exe not found'; throw 'easel.exe 不存在，安装可能不完整' }

  # prepend our toolchain to PATH
  $sep = [IO.Path]::PathSeparator
  $env:PATH = $NodeDir + $sep + $FFmpegDir + $sep + $env:PATH
  Log 'PATH prepared'

  function Test-Listening([int]$Port) {
    $out = & netstat -ano -p TCP 2>$null | Select-String (":$Port\s+.*LISTENING")
    return [bool]$out
  }

  # 取某个端口上正在监听的进程 PID（可能多个）
  function Get-ListeningPids([int]$Port) {
    $hits = & netstat -ano -p TCP 2>$null | Select-String (":$Port\s+.*LISTENING")
    $ids = @()
    foreach ($h in $hits) {
      $parts = @($h.ToString().Trim() -split '\s+')
      $candidate = $parts[$parts.Count - 1]
      if ($candidate -match '^\d+$') { $ids += [int]$candidate }
    }
    return @($ids | Sort-Object -Unique)
  }

  # 调用一个诊断脚本，返回它输出的第一个 token（失败返回空串）
  function Invoke-CheckScript([string]$ScriptName) {
    try {
      if (-not (Test-Path $PyExe)) { return '' }
      $full = Join-Path $EaselDir ('scripts\' + $ScriptName)
      if (-not (Test-Path $full)) { return '' }
      $out = @(& $PyExe $full 2>$null)
      # 取「最后一个非空行」：即使脚本先吐了别的（警告等），真正的结果 token 也在最后
      $last = ''
      foreach ($ln in $out) {
        if ($null -eq $ln) { continue }
        $s = $ln.ToString().Trim()
        if ($s -ne '') { $last = $s }
      }
      return $last
    } catch {
      return ''
    }
  }

  # 启动前先清网关僵尸锁（网关被强杀时会残留 gateway*.lock，
  # 里面记着已死进程的 PID，会让新网关误判 "already running" 后 5s 超时退出，
  # 表现就是 web 报 ECONNREFUSED）。按 PID 存活情况清，活的锁不动。
  # 注意：不能写 $j.pid —— PowerShell 里 $PID 是只读自动变量，会抛
  # ParameterBindingValidationException；必须用 PSObject.Properties 取。
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
        Log ("cleared stale gateway lock: " + $lockFile.Name)
      }
    }
  }

  # ---------- 1) 网关 ----------
  $gwUp = Test-Listening $GwPort
  Log "pre-check: gateway=$gwUp web=$(Test-Listening $WebPort)"

  if (-not $gwUp) {
    Log 'starting gateway ...'
    Start-Process -FilePath $EaselExe -ArgumentList 'gateway','start' `
      -WorkingDirectory $EaselDir -WindowStyle Hidden -ErrorAction Continue
    Log 'gateway launch issued'
    # 等它就绪（最多 90 秒）；就绪后再做自检，避免误报 NOGATEWAY
    $gwDeadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $gwDeadline) {
      if (Test-Listening $GwPort) { break }
      Start-Sleep -Seconds 3
    }
    Log ("gateway ready = " + (Test-Listening $GwPort))
  } else {
    Log 'gateway already listening'
  }

  # ---------- 2) 自检：原始流 + 默认模型 ----------
  $rawChk = Invoke-CheckScript 'check_raw_stream.py'
  Log ("raw-check: '" + $rawChk + "'")
  if ($rawChk -eq '') {
    Note '自检脚本没输出，无法核对「原始流 / 默认模型」'
  }
  elseif ($rawChk -like 'NOGATEWAY*') {
    Note '探测不到 gateway（端口 18789）——对话会报"连不上网关"'
  }
  elseif ($rawChk -like 'MISSING*') {
    Note 'gateway 没开「原始流」——对话会卡在"正在思考"，一个字都不出'
  }
  elseif ($rawChk -like 'EMPTY*') {
    Log 'raw stream file empty (还没有对话产生，正常)'
  }
  else {
    Log 'raw stream OK'
  }
  if ($rawChk -like '*,GEMINI*') {
    Note '默认模型仍指向 google-gemini-cli——漏传模型时会报 Gemini 安全警告'
  }

  # ---------- 3) 网页 ----------
  $webUp = Test-Listening $WebPort
  if ($webUp) {
    $stale = Invoke-CheckScript 'is_web_stale.py'
    Log ("web-stale: '" + $stale + "'")
    if ($stale -eq 'STALE') {
      Log 'web is running STALE code -> restarting it'
      foreach ($p in (Get-ListeningPids $WebPort)) {
        try {
          Stop-Process -Id $p -Force -ErrorAction Stop
          Log ("stopped stale web pid " + $p)
        } catch {
          Log ("could not stop pid " + $p + " : " + $_.Exception.Message)
        }
      }
      $t = 0
      while ((Test-Listening $WebPort) -and ($t -lt 20)) { Start-Sleep -Seconds 1; $t += 1 }
      $webUp = Test-Listening $WebPort
      if ($webUp) { Note '旧网页进程没能停掉（可能权限不足），请手动重启电脑或关掉占用 7860 的程序' }
    }
  }

  if (-not $webUp) {
    Log 'starting web ...'
    Start-Process -FilePath $EaselExe -ArgumentList 'web' `
      -WorkingDirectory $EaselDir -WindowStyle Hidden -ErrorAction Continue
    Log 'web launch issued'
  } else {
    Log 'web already listening (fresh)'
  }

  # ---------- 4) 等就绪 + 开浏览器 ----------
  $deadline = (Get-Date).AddMinutes(4)
  $waited = 0
  while ((Get-Date) -lt $deadline) {
    if (Test-Listening $WebPort) { Log ("web ready after ~$waited s"); break }
    Start-Sleep -Seconds 3
    $waited += 3
  }
  if (-not (Test-Listening $WebPort)) { Note '网页 4 分钟内没起来，请查看日志' }

  Log 'opening browser'
  # 关键修复：Chrome 里若已经开着 http://127.0.0.1:7860 的旧标签，
  # 直接打开同一个地址，浏览器只会「切过去」而**不会重新加载** ——
  # 用户看到的还是旧界面，感觉像"点了没反应"。加一个时间戳参数后
  # 就是全新地址，浏览器必定新开标签并加载最新页面。
  $OpenUrl = $Url + '/?t=' + [DateTime]::Now.Ticks
  $opened = $false
  $chromeCands = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
  )
  foreach ($cand in $chromeCands) {
    if ($cand -and (Test-Path $cand)) {
      try {
        Start-Process -FilePath $cand -ArgumentList $OpenUrl -ErrorAction Stop
        $opened = $true
        Log ('browser opened via chrome: ' + $cand)
        break
      } catch {
        Log ('chrome launch failed: ' + $_.Exception.Message)
      }
    }
  }
  if (-not $opened) {
    try {
      Start-Process $OpenUrl -ErrorAction Stop
      $opened = $true
      Log 'browser opened via default handler'
    } catch {
      Log ('default browser launch failed: ' + $_.Exception.Message)
    }
  }
  if (-not $opened) { Note '没能自动打开浏览器，请手动访问 http://127.0.0.1:7860' }
  Log '=== script finished ==='
}
catch {
  Log ('EXCEPTION: ' + $_.Exception.GetType().FullName + ' : ' + $_.Exception.Message)
  Log ('STACK: ' + $_.ScriptStackTrace)
  [void]$Problems.Add('启动过程出错：' + $_.Exception.Message)
}

# ---------- 5) 有问题就弹窗，别静默失败 ----------
if ($Problems.Count -gt 0) {
  Log ('PROBLEMS SUMMARY: ' + ($Problems -join ' | '))
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $lines = @()
    foreach ($p in $Problems) { $lines += ('  • ' + $p) }
    $text = "Easel 工作台启动时发现问题：`r`n`r`n" + ($lines -join "`r`n") +
            "`r`n`r`n服务可能已经起来了，但对话有可能不正常。`r`n" +
            "日志：C:\Users\Administrator\.easel-tools\easel-start.log"
    [System.Windows.Forms.MessageBox]::Show($text, 'Easel 工作台', 'OK', 'Warning') | Out-Null
  } catch { }
}
