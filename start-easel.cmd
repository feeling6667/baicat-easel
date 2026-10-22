@echo off
REM ============================================================
REM  Easel 工作台 一键启动 —— 先确保 gateway 活着，再开 Web 控制台
REM  用法：双击此文件，或在终端执行 start-easel.cmd
REM  依赖：node / openclaw / ffmpeg 已在 PATH 里
REM        （安装时已写入用户级 PATH：
REM          C:\Users\Administrator\.easel-tools\node24
REM          C:\Users\Administrator\.easel-tools\ffmpeg\bin）
REM
REM  【2026-10-12 第一次改】原版有个坑：若 Web 已在跑（端口 7860 被占），
REM   easel web 会因端口冲突立刻退出 —— cmd 窗口"闪一下就没"。
REM   但服务其实起得好好的。现在：
REM     1) 先探测 7860，已在跑就直接开浏览器，不再重复起进程
REM     2) 任何一个分支都有 pause 把窗口留住，不会再看不到原因
REM
REM  【2026-10-12 第二次改】上面那条还有个更隐蔽的坑：
REM   如果 7860 上跑的是**旧代码**（比如刚改完后端但没重启），
REM   探测照样返回 200 → 脚本以为"已经很好了" → 直接开浏览器 →
REM   于是又看到同样的报错，用户以为改动没生效。
REM   现在加一道「代码是不是比服务新」的检查（scripts\is_web_stale.py
REM   比关键源码的修改时间 vs 服务进程启动时间），旧了就自动重启，
REM   不用手动去 kill 进程。
REM ============================================================
setlocal
cd /d "%~dp0"

set "PORT=7860"
set "URL=http://localhost:%PORT%"
set "PY=.venv\Scripts\python.exe"

echo.
echo ============================================================
echo   Easel 工作台
echo ============================================================
echo.

REM ---------- 1) gateway ----------
echo [1/4] 检查 gateway...
".venv\Scripts\easel.exe" gateway start
if errorlevel 1 (
  echo.
  echo [x] gateway 启动失败，请把本窗口截图给维护者。
  echo.
  pause
  exit /b 1
)

REM ---------- 1.5) gateway 的「原始流」自检 ----------
REM 【2026-10-13 新增】这是「卡在正在思考…」的直接原因所在。
REM 只有常驻 gateway 会写原始流（token/思考流）；它必须带 --raw-stream 启动。
REM 若没开，文件永远不存在 → 前端一直转圈，模型答完了也推不出来一个字。
REM 这里启动后立刻验一下，有问题当场提示，不用等用户卡半天才发现。
echo       核对 gateway 原始流与默认模型...
set "RAWCHK=UNKNOWN"
for /f %%r in ('"%PY%" scripts\check_raw_stream.py 2^>NUL') do set "RAWCHK=%%r"

REM 输出形如 OK,DEF=magpie/xxx,MAGPIE（用逗号分隔，避开 cmd 的管道符 |）
REM 注意：不能写 echo %VAR% ^| findstr —— echo 出的 | 会被 cmd 当成管道，把命令拆烂。
REM 正确做法：findstr 直接搜「变量展开后的字符串」，用 echo 只做回显。
echo "%RAWCHK%" | findstr /C:"MISSING" >NUL 2>NUL
if not errorlevel 1 (
  echo.
  echo       [!] gateway 没开「原始流」——前端会卡在"正在思考"。
  echo           请把本窗口截图给维护者（files: scripts\gateway.ps1）。
  echo.
)
set "RAWOK=OK"
echo "%RAWCHK%" | findstr /C:"OK," >NUL 2>NUL
if not errorlevel 1 echo       [√] 原始流正常。
echo "%RAWCHK%" | findstr /C:",GEMINI" >NUL 2>NUL
if not errorlevel 1 (
  echo.
  echo       [!] 默认模型还指向 google-gemini-cli——漏传模型时会报 Gemini 安全警告。
  echo           请把本窗口截图给维护者（files: openclaw.json 的 agents.defaults.model）。
  echo.
)
echo "%RAWCHK%" | findstr /C:"NOGATEWAY" >NUL 2>NUL
if not errorlevel 1 (
  echo.
  echo       [!] 探测不到 gateway（端口 18789）——它可能还没起来或已崩。
  echo           对话会报"连不上网关"；请把本窗口截图给维护者。
  echo.
)

REM ---------- 2) Web 是否已在运行 ----------
echo.
echo [2/4] 检查 Web 控制台（端口 %PORT%）...
set "WEBUP=0"
REM 用 Windows 自带的 curl.exe 探测（比 powershell 更可靠，不依赖执行策略）
for /f %%i in ('curl -s -o NUL -w "%%{http_code}" -m 2 --noproxy 127.0.0.1 "%URL%/" 2^>NUL') do set "WEBUP=%%i"

if not "%WEBUP%"=="200" goto :need_start

REM ---- 已在运行：再确认它跑的是不是最新代码 ----
echo       检测到服务在跑，核对代码新旧...
set "STALE=UNKNOWN"
for /f %%t in ('"%PY%" scripts\is_web_stale.py 2^>NUL') do set "STALE=%%t"

if "%STALE%"=="STALE" (
  echo.
  echo       [!] 后台跑的还是【旧代码】（改完没重启），正在自动重启...
  echo           请稍等，重启大概十几秒。
  echo.
  goto :restart
)

if "%STALE%"=="FRESH" goto :already_up
REM NOLISTEN / UNKNOWN：判断不了就保守处理，不瞎重启
echo       (未能核对代码新旧，按已在运行处理)
goto :already_up

REM ---------- 3) 重启：先停掉旧进程，再往下走 ----------
:restart
echo       正在停止旧的后台进程...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
  taskkill /PID %%p /T /F >NUL 2>&1
)
REM 等端口真正释放（最多 20 秒）
set /a _TRY=0
:wait_port
set /a _TRY+=1
set "OCCUPIED=0"
for /f %%i in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do set "OCCUPIED=1"
if "%OCCUPIED%"=="1" (
  if %_TRY% LSS 20 (
    timeout /t 1 >nul
    goto :wait_port
  )
)
echo       旧进程已停止。

:need_start
REM ---------- 4) 启动 Web ----------
echo.
start "" "%URL%"
echo [3/4] 启动 Web 控制台：%URL%
echo.
echo      * 请保持本窗口开着，关掉它 = 停止控制台
echo      * 按 Ctrl+C 也可以停止
echo      * gateway 在后台常驻，不受影响
echo.
echo [4/4] 正在运行...
echo.

".venv\Scripts\easel.exe" web
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
  echo [x] Web 控制台退出（返回码 %RC%）。
  echo     常见原因：端口 %PORT% 被别的程序占用。
  echo     排查：netstat -ano ^| findstr :%PORT%
) else (
  echo [i] Web 控制台已停止。
)
echo.
pause
endlocal
exit /b 0

:already_up
echo.
echo [√] 控制台已经在跑（且是最新代码）：%URL%
echo     现在可以关掉本窗口（服务在后台运行）。
echo.
start "" "%URL%"
timeout /t 5 >nul
endlocal
exit /b 0
