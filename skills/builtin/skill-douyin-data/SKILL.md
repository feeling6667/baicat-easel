---
name: skill-douyin-data
description: >
  抖音结构化数据采集：经 TikHub 接口拉博主作品、作品评论、推荐流、关键词搜索，
  输出统一 item 并落盘 outputs/_douyin/。用户说"抓抖音数据"、"采这个博主"、
  "这条视频的评论"、"抖音数据采集"、"扒一下这个抖号"时触发。
  边界：只负责把数据拿回来，不做粉丝增长和内容生命周期分析（那是 skill-data-tracker）；
  热榜聚合走 skill-trending-topics，本 SKILL 是账号级结构化采集、按次计费。
layer: discover
---

# 抖音数据采集

> 用 TikHub 官方接口拿抖音的结构化数据，不需要登录抖音、不开浏览器。

## 输入

| 字段 | 必填 | 说明 |
|------|------|------|
| mode | 是 | user / comments / feed / search |
| sec_user_id | Mode user: 是 | 博主加密 ID，形如 `MS4wLjABAAAA...`，从主页 URL 取 |
| aweme_id | Mode comments: 是 | 作品 ID，纯数字，从视频 URL 取 |
| keyword | Mode search: 是 | 搜索关键词 |
| limit | 否 | 最多抓多少条。user 默认 0（全部），comments 默认 200，feed 默认 30，search 默认 20 |
| push | 否 | 是否写入选题库 outputs/_ideas.json |

参数获取方式：

- `sec_user_id`：打开博主主页，地址栏 `douyin.com/user/` 后面那一整串
- `aweme_id`：打开视频页，地址栏 `douyin.com/video/` 后面的数字

## 输出

统一 item 格式，与 `creator_feed.py` / `bili_creator.py` 一致：

```json
{
  "id": "作品或评论 ID",
  "title": "文案 / 评论内容（截断 200 字）",
  "url": "https://www.douyin.com/video/{id}",
  "published": "ISO8601 时间",
  "stats": {"views": null, "likes": 0, "comments": 0, "shares": 0, "collects": 0},
  "_author_name": "作者昵称",
  "_author_sec_id": "作者 sec_user_id",
  "_author_fans": 0,
  "_duration_ms": 0,
  "_tags": ["话题"]
}
```

评论模式额外带 `_aweme_id` / `_ip_label` / `_is_author` / `_parent_id`。

落盘路径：`outputs/_douyin/{mode}_{key}_{时间戳}.json`，外层包 `{kind, key, count, collected_at, items}`。

## 执行步骤

1. 判断 mode，缺参数就向用户要 `sec_user_id` / `aweme_id` / `keyword`，不要自己编。
2. 执行对应命令（工作目录为项目根）：

```bash
# 博主主页作品
python skills/shared/scripts/douyin_data.py user --sec-user-id MS4wLjABAAAA... --limit 50

# 单条作品评论
python skills/shared/scripts/douyin_data.py comments --aweme-id 7676311289018207523 --limit 200

# 首页推荐流
python skills/shared/scripts/douyin_data.py feed --limit 30

# 关键词搜索
python skills/shared/scripts/douyin_data.py search --keyword 露营装备 --limit 20
```

3. 需要把结果直接读进上下文时加 `--json`；需要进选题库时加 `--push`。
4. 读命令末尾打印的落盘路径，从中取数据做后续分析。
5. 向用户汇报：抓了多少条、时间范围、点赞/评论分布、Top 作品。

## 计费

TikHub 按次计费约 $0.01/次，翻页会累加。执行前告诉用户预计消耗，
`--limit` 要按需给，别默认拉全量。

## 失败处理

| 现象 | 原因 | 处理 |
|------|------|------|
| `TikHub 401` | API key 无效或过期 | 检查项目根 `.env` 的 `TIKHUB_API_KEY` |
| `TikHub 429` | 触发限流或余额不足 | 降低 limit、隔几分钟重试、去控制台查余额 |
| `接口返回 code=...` | 参数不对或该接口对当前账号不可用 | 检查 sec_user_id / aweme_id 是否抄错 |
| 只返回几条就停 | 该博主作品少，或接口分页到底 | 正常，看落盘文件的 count |

## Profile 感知

本 SKILL 不依赖 Profile。若用户给了 Profile，可用其中的账号名判断"这个博主"指谁，
但 `sec_user_id` 仍必须从主页 URL 取实际值，不要从 Profile 推断。
