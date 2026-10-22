# banana-shim — 让 EASEL 走 Banana(Nano Banana)生图

不改 EASEL 一行代码：它是一个 OpenAI 兼容的生图中转站，把 EASEL 原本打给付费网关的
请求，转到 Google 的 Banana 模型上，再把图按原格式还回去。

    EASEL skill (skills/shared/scripts/ai_image.py)
          |
          |  POST {IMG_BASE_URL}/images/generations   (OpenAI 格式)
          v
    banana-shim  http://127.0.0.1:8790/v1
          |
          +-- backend=gemini  ->  Google AI Studio  gemini-2.5-flash-image
          +-- backend=flow    ->  Playwright 驱动 labs.google Flow 网页(用订阅额度)
          +-- backend=mock    ->  本地画占位图(验链路)

## 三条后端怎么选

| 后端 | 花什么 | 稳定性 | 适用 |
|---|---|---|---|
| gemini | Gemini API key，按张计费 | 高，官方接口 | 日常跑量，推荐 |
| flow   | 你的 Gemini Pro 订阅额度 | 中，网页改版/验证码会影响 | 就是不想再掏钱 |
| mock   | 不花钱不出网 | — | 验证 shim 通不通 |

flow 是网页自动化，属于灰色使用，可能触发验证码或影响账号；想省心就用 gemini——
它和 Flow 网页背后是同一个模型。

## 三步跑起来

1. 装配置（把默认的 mock 改成你要的后端）
   复制 shim.env.example 为 shim.env，改这两行之一：

       BANANA_BACKEND=gemini
       GEMINI_API_KEY=你的key

   或

       BANANA_BACKEND=flow

2. 起服务：双击 start-shim.cmd
   看到 listening on http://127.0.0.1:8790 就行，这个窗口别关。

3. 把 EASEL 指过来（自动备份 .env）：

       powershell -ExecutionPolicy Bypass -File wire-easel.ps1

   它写 4 个键：IMG_BASE_URL / IMG_MODEL / IMG_API_KEY / IMG_NO_PROXY。
   回滚：powershell -ExecutionPolicy Bypass -File wire-easel.ps1 -Revert

之后 EASEL 生图（生图 skill、批量流水线、工作台里点生成）全部走本地 shim。
EASEL 的 .env 改完要重启 EASEL 进程才会重新读。

## flow 后端首次使用

第一次会弹出 Chrome，手动登录 Google 账号（窗口别关），登录态存在
%USERPROFILE%\.banana-shim\chrome-profile，之后复用。

如果捕获不到输入框，抓一次真实页面结构再调选择器：

    cd banana-shim
    ..\.venv\Scripts\python.exe backend_flow.py --dump

产物在 banana-shim\debug\：flow-*.html / flow-*.png / flow-*.json（列出了所有
textarea/input/button 的 text / aria-label / placeholder）。把里面的关键词补进
backend_flow.py 顶部的 PROMPT_SELECTORS / SEND_SELECTORS 即可。

## 自检与调试

    # 后端自检（key 有没有、profile 有没有）
    ..\.venv\Scripts\python.exe shim.py --backend gemini --check

    # 直接打接口，绕过 EASEL
    curl http://127.0.0.1:8790/healthz
    curl http://127.0.0.1:8790/v1/models

    # 用 EASEL 自己的客户端打一发（--size 传 1024x1024 或 16:9）
    $env:IMG_BASE_URL='http://127.0.0.1:8790/v1'
    $env:IMG_MODEL='nano-banana'
    $env:IMG_API_KEY='local-banana-shim'
    $env:IMG_NO_PROXY='1'
    ..\.venv\Scripts\python.exe ..\skills\shared\scripts\ai_image.py text2img --prompt '测试' -o ..\outputs\banana-shim-smoke\test.png --size 1024x1024

日志：banana-shim\logs\shim-YYYYMMDD.log，每次请求的 backend / 张数 / 尺寸 / 用时都记。

## 支持的接口

    GET  /healthz                     后端状态
    GET  /v1/models                   模型列表（给 EASEL 的动态模型发现用）
    GET  /files/<name>                用 response_format=url 时托管的图
    POST /v1/images/generations       文生图 + 图生图（输入图放 image / images / extra_body.image，
                                      支持 data:URI 和 http(s) 两种形式）
    POST /v1/images/edits             标准 multipart 图生图
    POST /v1/images/variations        变体

返回标准 OpenAI 结构：{"created":..., "data":[{"b64_json":...} 或 {"url":...}]}。

## 排错

* 端口被占：改 shim.env 的 BANANA_PORT，再重跑 wire-easel.ps1 -Port 新端口。
* EASEL 报连接失败：shim 窗口是不是关了；或 IMG_NO_PROXY 没设（EASEL 走系统代理会打不到 127.0.0.1）。
* 图是灰色方框写着 mock：BANANA_BACKEND 还是 mock，改 shim.env 重启 shim。
* flow 一直超时：多半没登录或页面改版，看 banana-shim\debug\ 的截图。
* 想让某个 skill 用别的模型：那个 skill 单独设 IMG_MODEL 环境变量即可，shim 不看模型名。

## 文件

    shim.py              HTTP 服务 + 路由 + 输入解析
    backend_gemini.py    Google AI Studio 后端
    backend_flow.py      Playwright 驱动 Flow 后端（含 --dump 模式）
    backend_mock.py      占位后端
    start-shim.cmd       启动器（纯 ASCII + CRLF）
    wire-easel.ps1       改写 EASEL .env，带备份/回滚
    shim.env.example     配置模板
    logs/ public/ debug/ 运行产物

## arena 后端（网页驱动，用试用额度）

BANANA_BACKEND=arena 时，shim 用 Playwright 驱动 arena.ai 网页：投提示词 → 点生成 →
抓图 → 按 OpenAI 格式返回。**不走 API 抓包**，因为 arena.ai 挂 Cloudflare，
cf_clearance 绑定 IP+UA，Python 裸重放必然失效；真浏览器天然绕开这层。

实测数据（2026-10-20）：

    出图耗时   ~50s / 张
    产物体积   1.8–2.1 MB PNG（约 1330x1180）
    已验证     EASEL 客户端 → shim → arena.ai → 回图 全链路

### 首次使用

1. ...venvScriptspython.exe login-arena.py    # 开窗口手动登录一次
2. 登录完关掉窗口，登录态留在 ~/.banana-shim/arena-profile
3. shim.env 设 BANANA_BACKEND=arena，重启 shim

### 已知边界

* **单线程**：shim 用 HTTPServer 而非 ThreadingHTTPServer——Playwright sync API
  绑定创建它的线程，多线程会炸。生成本来就是串行的，无损失。
* **常驻浏览器**：shim 进程内保持一个 Chrome，别手动关那个窗口。
* **reCAPTCHA**：页面挂了 reCAPTCHA，触发时会抛错并提示手动过一次。
* **配额未知**：10 天试用的每日上限站点未公开，需要登录后在界面确认。
* **选择器**：站点改版后跑 arena-probe.py 重新 dump，更新
  backend_arena.py 顶部的 PROMPT_SELECTORS / SEND_SELECTORS 即可。

### 换站复用

通用层在 browser_driver.py（浏览器启动 / 双路取图 / 页面 dump）。
换目标站只需新写一个 backend_<站点>.py，实现 generate() 即可。
