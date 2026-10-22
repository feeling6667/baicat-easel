# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | skill-douyin-data |
| **所属层** | discover |
| **来源类型** | 自研（基于 TikHub 官方 OpenAPI） |
| **原始来源** | Easel 自研 |
| **参考项目** | 1) TikHub OpenAPI（https://api.tikhub.io/openapi.json，322 个抖音端点）— 接口路径、参数与计费模型；2) 同仓 `tikhub_search.py` —  token 加载方式、HTTP 封装与 item 归一化风格；3) MediaCrawler（https://github.com/NanmiCoder/MediaCrawler）— 多平台采集的数据字段设计参考 |
| **许可** | 待核实 |

> 整理时间: 2026-10-20
> 用途: 来源溯源与致谢

## 实现说明

- **纯标准库**：只用 urllib / json / argparse，与 `tikhub_search.py` 保持一致，不引入 requests。
- **单一搜索实现**：search 模式转调 `tikhub_search.py` 的 `search_videos()`，不复制一份搜索逻辑，避免参数漂移。
- **补齐的三类采集**：`user` / `comments` / `feed` 是原 `tikhub_search.py` 没有的，本 SKILL 补上。
- **key 不进代码**：统一从项目根 `.env` 的 `TIKHUB_API_KEY` 读，脚本不写死也不打印 token。
- **实测**：2026-10-20 用真实 key 跑通 `feed --limit 5`，返回 5 条真实作品并落盘。
