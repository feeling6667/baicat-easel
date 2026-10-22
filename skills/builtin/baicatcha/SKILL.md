---
name: baicatcha
description: 图文故事生产全链路（FB-060 绘本水彩，风格与字体已写死）。当用户说「生成图文故事 / 做一篇图文故事 / 手绘图文 / 爆款改图文 / 转 FB-060 绘本风 / 复刻这条爆款」，或发来视频链接、粘贴爆款文案、只给一个热点标题要求做成图文故事时触发。走 输入归一化 → 爆点拆解 → 复刻改写 → 拆页分镜 → 生图 → 小红书文案 → 进发布队列；人物跨页一致，封面手写马克笔粗楷体、正文乐米蜡笔体、关键词正红。
layer: produce
---

# baicatcha · 图文故事生产线（FB-060 绘本水彩）

把一条爆款（视频链接 / 文案 / 主题）复刻成 FB-060 绘本水彩风格的图文故事卡片组 + 小红书文案，并进发布队列。
**风格、字体、字号规则、负向约束全部写死**，不允许在对话里临时换风格或换字体。

- 与 `skill-content-repurposing` 的区别：那个不生图（只改文案）。
- 与 `card-xiaohongshu` 的区别：那个是 HTML 排版截图，不是 AI 手绘。
- 与 `video-to-article` 的区别：那个只转文章，不做分镜与生图。

## 环境

- 脚本主入口：`skills/builtin/baicatcha/scripts/viral_pipeline.py`
- 解释器：仓库 venv `.venv\Scripts\python.exe`（yt-dlp / faster-whisper / cv2 都在里面，不要用系统 PATH）
- 风格资产（唯一来源）：`assets/fb060-frazee.json`、`assets/FB-060.webp`

## 写死的风格与字体（不可违反）

- **风格锁**：只允许 FB-060。`style_index ≠ FB-060` 或 `style_lock`/`typography`/`negative_prompt` 缺字段 → 脚本退出码 2，**不会带别的风格出图**。
- **字体两档**（全部由生图模型直接画进画面，不做后期叠字）：
  - `kind=cover` → **手写马克笔粗楷体**：圆润饱满、起收笔有笔锋、横细竖粗、略歪斜、字距紧凑。
  - `kind=content|cta` → **乐米蜡笔体（LemiLaBiTi）**：儿童蜡笔手写体，圆润厚实、粗细不匀、边缘带蜡笔毛感、略歪斜、字距宽松。
- **字号规则（硬性）**：
  - 封面：**有层次**——标题最大，短句约 45%。
  - 正文/收尾：**无层次**——不分主次、**不设大标题**，每行字符高度必须完全相等（误差≤3%），字距行距一致；第一行不得比其它行大。
  - 自适应：按本页字数整体缩放（单行占宽≤78%，字符高度≈画面宽 4.5%~9%）。
- **关键词**：正红 `#D93A2B`，**每页 2-4 个词**，只换颜色不换字形、不加粗、不描边、不加底色块。
- **禁用**：印刷体/宋体/黑体、花体连笔、描边/发光/渐变/立体字、贴图式字层、重影双影、用英文代替中文。
- **改字体/改风格只改 `assets/fb060-frazee.json`**（同步改生图风格库 `fb060-handdrawn.json`），不要改页数据。
- 路由表：`typography.route`（`cover→cover`，`content/cta→body`）。

## 人物一致性（跨页锁定，必带）

在 `storyboard.json` 里写角色台词本，脚本逐页注入 prompt：

```json
"characters": [
  {"name": "小满", "role": "女儿，8 岁",
   "look_zh": "齐耳黑短发扎两个小揪揪（红头绳）、圆脸、弯月眼；淡黄长袖上衣、靛蓝背带裤、白球鞋",
   "look_en": "8-year-old girl, ear-length black hair in two small buns, pale-yellow top, indigo overalls"}
]
```

- 每页用 `cast: ["小满", "爸爸"]` 指定本页出场角色；未定义的名字会被过滤并告警。
- 判定标准：任意两页并排看，必须是「同一个人换了动作」，不是「换了个孩子」——同脸、同发型发色、同服装款式与颜色、同体型年龄，禁换装、禁凭空加陌生人。

## 核对（零成本，不发请求）

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py style --dir "<DIR>" --pages 1
```

打出逐字实际发送的 prompt，用于确认字体/风格/人物设定确实进了这一页。

## 全局纪律

- 产物目录 `outputs/viral-<yyyymmdd-hhmm>/`，由 `ingest` 创建并把路径打印在最后一行；后续每步都用这个 `<DIR>`。
- **页数与字数不设上限**：按内容需要拆页、写多长都行；唯一约束是**长文案不进图**（正文只进 copy.md，图内只放标题与短句）。
- **复刻结构、内容全重写**：钩子→铺垫→展开→金句→收尾，节奏位一一对应，表达必须与原文不同。
- 状态机 `ingested→ready→storyboarded→generated→copied→enqueued→done` 写进 `manifest.json`，失败按该步降级，不跳步重跑。
- **一轮跑完不中途停**：用户显式触发即视为已知情本次按页数发起的全部生图与「进发布队列（draft）」。
- LLM 创作的 JSON 先写 `<DIR>/work/`，再由脚本校验落盘；报错会指出改哪里，改完重跑同一命令。

## 第 0 步 · 环境自检

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py check_env
```

看三行：`[set]` 的 IMG_BASE_URL / IMG_MODEL / IMG_API_KEY（生图必备），以及 **`生图端点可达`**。

- 缺 IMG 三项 → 提示用户去设置页配生图 API，不硬跑。
- `生图端点可达: DOWN` → **shim 没起来，不要去点生图**。先运行 `banana-shim\start-guard.cmd`（或 `start-shim.cmd`），再跑一次 check_env 看到 `ok` 才继续。

## 第 1 步 · 输入归一化（三路自动识别）

`http(s)://` → url 分支（yt-dlp 下载 + faster-whisper 转写）；≥60 字或 ≥2 行 → text 分支；一行短句 → topic 分支。

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py ingest --input-file <临时文件>
# 或：ingest --branch topic --input-raw "我爸攒了十年的火车票"
```

url 分支失败（下载/转写/超时，退出码 3）→ 告知"解析失败，请直接粘贴口播文案"，不要卡住重试。

## 第 2+3+4 步 · 拆解 → 改写 → 分镜（你创作，脚本校验）

读 `<DIR>/source.txt`，写 `<DIR>/work/storyboard.json`：

```json
{
  "topic": "一句话主题",
  "source_summary": "原文 50 字内摘要；topic 分支写主题本身",
  "analysis": {"hook": "", "conflict": "", "density": "", "golden": "", "cta": ""},
  "rewrite": {"hook": "", "buildup": "", "expand": "", "golden": "", "cta": ""},
  "characters": [{"name": "", "role": "", "look_zh": "", "look_en": ""}],
  "theme_color": "米白纸底 + 明黄/正红小面积重色（可省）",
  "pages": [
    {"kind": "cover", "cast": ["角色名"], "title": "页标题（字数不限）",
     "lines": ["短句（字数与条数不限）"], "visual_zh": "童趣具体化画面", "visual_en": "english scene",
     "theme_color": "#FFFDF8"}
  ]
}
```

- `pages` 页数不限（1 页也能跑）：第 1 页 cover，中间 content，最后 cta；节奏位对应 rewrite 五段。
- topic 分支：analysis 填「主题→爆款结构生成」的设计说明，不编造假原文。

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py storyboard --dir "<DIR>" --json "<DIR>/work/storyboard.json"
```

产出 `storyboard.md`（拆解表 + 复刻对照 + 分镜表 + 人物锁表）与 `pages.json`。

## 第 5 步 · 生图（3:4 / 1080x1440）

生图有三条路，按需选一条；**三者的提示词逐字相同**（同走 `_build_prompt`），只是出图通道不同。

### 方案一 · 本地 shim（默认，免费额度）

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py gen_pages --dir "<DIR>"
```

走 `banana-shim` 浏览器自动化。跑之前 `check_env` 必须是 `生图端点可达: ok`。

### 方案二 · 中转 API（`--via relay`）

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py gen_pages --dir "<DIR>" --via relay
```

读三个独立配置键，**不占用 shim 的 IMG_***，两条通道可以随时切：

```
IMG_RELAY_BASE_URL=https://<中转域名>/v1
IMG_RELAY_MODEL=<中转上的图模型名>
IMG_RELAY_API_KEY=<中转 key>
```

缺任一键直接报错并打印该填什么，**不会偷偷回退到 shim**——回退会让人以为中转通了。

### 方案三 · 逐页出提示词（一张一张发）

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py prompt --dir "<DIR>" --page 1
```

一次只出**一页**的完整提示词，不调接口、不落图。用户说「继续」再出下一页（脚本会提示下一句该发什么）。提示词拿去任何支持的中转/客户端生成，效果与走 shim 一致。

#### 方案一/二的公共参数

- 模型自动降级链：`--model` → `IMG_MODEL` → 网关 `/v1/models` 动态发现 → 硬编码兜底；失败原因逐条写进 `manifest.json` 的 `images[].failures`。
- 尺寸降级：`3:4` → `768x1024` → `1024x1536`；成功<3 张退出码 4，用 `--pages 2,4` 补页，`--force` 整页重生成。
- 垫图可选：`--use-ref` 走 `/images/edits`（带风格隔离指令），默认不开。

### 生图报错：脚本自己认，自己解决

失败不是 HTTP 码，而是上游页面文案。脚本把每条失败归成一类，并在 stderr 直接给出动作；归类同时写进 `manifest.json` 的 `images[].kinds / actions`。

| 归类 | 触发文案 | 脚本怎么做 |
|---|---|---|
| `blocked` | violates our Terms of Use / content policy / flagged | 本页立即放弃，不再换模型硬试；改 `visual_zh` 后 `--pages N --force` 补图 |
| `upstream` | something went wrong / please try again / rate limit | 自动重试，不需要人管 |
| `auth` | sign in / session expired / captcha | 运行 `banana-shim\login-arena.py` 重新登录 |
| `conn` | 无法连接 / actively refused / 10061 | 运行 `banana-shim\start-guard.cmd` 拉起 shim |

看到 `归类=blocked` 就改文案，别原句重试——同一句试 8 次只会浪费 8 次额度。

## 第 6 步 · 小红书配文

写 `<DIR>/work/copy.json`（title 需带 1-2 个 emoji，这是唯一硬校验；正文与 tags 的字数、条数都不设限）：

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py copy --dir "<DIR>" --json "<DIR>/work/copy.json"
```

## 第 7 步 · 进发布队列（draft）

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py enqueue --dir "<DIR>"
```

POST `http://localhost:7860/api/schedule`：`kind=content, status=draft, source=chat, platform=xiaohongshu`。失败退出码 5，stderr 会给可手工执行的 POST 命令，产物不受影响。

## 第 8 步 · 验收汇总

```powershell
.venv\Scripts\python.exe skills/builtin/baicatcha/scripts/viral_pipeline.py finish --dir "<DIR>"
```

PASS 要求：`images/page_*.png` 全部 3:4（张数不限）；`storyboard.md` / `copy.md` / `manifest.json` / `pages.json` 齐全。交付时给：

1. 封面图（相对路径 `outputs/viral-…/images/page_1.png`）
2. 拆解表结论（3-5 句说清为什么爆、怎么复刻）
3. copy.md 全文
4. 发布队列确认一行（draft 的 id / url）
