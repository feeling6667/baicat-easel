"""热点雷达回归测试：各平台响应格式归一化 + 取不到数据要能被识别出来。

背景（2026-10-01）：热点雷达六个平台里三个是空的——B站其实是解析器的锅（备用源返回 50 条
纯字符串数组，而解析器只认对象数组，全被丢掉），知乎/头条则是压根没有备用源且主源被限流。
这个文件守住两件事：各家格式都解析得出来、以及"取不到"要和"真的没数据"分开。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "web"))

import app as web  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_trend_cache(monkeypatch):
    monkeypatch.setattr(web, "_TREND_CACHE", {})


# ---- 各家响应格式 ----

def test_parse_hot_handles_plain_string_array():
    """xxapi 的 B站热榜：data 就是一串标题字符串，没有对象包着。"""
    items = web._parse_hot({"code": 200, "data": ["凉哈皮谈首登亚运解说台", "   ", "holymoly亚运金牌"]})
    assert [i["title"] for i in items] == ["凉哈皮谈首登亚运解说台", "holymoly亚运金牌"]
    assert all(i["hot"] == "" and i["url"] == "" for i in items)


def test_parse_hot_handles_capitalized_fields():
    """头条官方热榜：Title / HotValue / Url 全是大写开头，热度还会是数字。"""
    items = web._parse_hot({"data": [
        {"ClusterId": 1, "Title": "农民交公粮能否视同缴社保", "HotValue": 1234567,
         "Url": "https://www.toutiao.com/trending/1/"},
    ]})
    assert items == [{"title": "农民交公粮能否视同缴社保", "hot": "1234567",
                      "url": "https://www.toutiao.com/trending/1/"}]


def test_parse_hot_handles_nested_target_and_rewrites_zhihu_url():
    """知乎官方热榜：正文在 target 里，链接给的是 api.zhihu.com，要点开得换成网页地址。"""
    items = web._parse_hot({"data": [
        {"type": "hot_list_feed", "card_id": "Q_1",
         "target": {"id": 2088649572151194406, "title": "迪拜航空客机返航",
                    "url": "https://api.zhihu.com/questions/2088649572151194406",
                    "metrics_area": {"text": "387 万热度"}}},
    ]})
    assert items == [{"title": "迪拜航空客机返航", "hot": "387 万热度",
                      "url": "https://www.zhihu.com/question/2088649572151194406"}]


def test_parse_hot_keeps_original_shape_working():
    """60s 那套老格式（data 直接是对象数组）不能被新写法改坏。"""
    items = web._parse_hot({"data": [{"title": "迪拜航空确认航班发生事故", "hot": "118万",
                                      "url": "https://s.weibo.com/x"}]})
    assert items == [{"title": "迪拜航空确认航班发生事故", "hot": "118万", "url": "https://s.weibo.com/x"}]


def test_parse_hot_accepts_nested_data_wrapper():
    items = web._parse_hot({"data": {"list": [{"word": "热词", "num": 42}]}})
    assert items == [{"title": "热词", "hot": "42", "url": ""}]


@pytest.mark.parametrize("bad", [
    {}, {"data": None}, {"data": "不是列表"}, {"data": [1, 2, 3]}, {"data": [{"hot": "1"}]}, [],
])
def test_parse_hot_returns_empty_on_junk(bad):
    """垃圾/空响应要安静地返回空列表，不能抛异常（抛了整条平台就断）。"""
    assert web._parse_hot(bad) == []


def test_trend_web_url_passes_through_other_urls():
    assert web._trend_web_url("") == ""
    assert web._trend_web_url("https://s.weibo.com/x") == "https://s.weibo.com/x"
    assert web._trend_web_url("https://api.zhihu.com/questions/abc") == "https://api.zhihu.com/questions/abc"


# ---- 源表本身 ----

def test_every_platform_has_a_source():
    """每个能选中的平台都要有主源，别出现"按钮在但没人给它取数据"。"""
    assert set(web.TREND_SOURCES) == set(web.TREND_LABELS)
    for pf, (primary, backup) in web.TREND_SOURCES.items():
        assert primary and primary.startswith("http"), f"{pf} 缺主源"
        if backup:
            assert backup.startswith("http")


# ---- 接口：取不到 vs 真的没有 ----

def test_trends_marks_unavailable_platform(monkeypatch):
    """/api/trends 要给每个平台带 ok 标：空列表＝这个平台取不到，不是"今天没热搜"。"""
    web._TREND_CACHE.clear()
    monkeypatch.setattr(web, "_fetch_platform",
                        lambda pf: [{"title": f"{pf} 的头条", "hot": "", "url": ""}] if pf == "weibo" else [])
    data = asyncio.run(web.api_trends("weibo,bilibili", 15))
    by_pf = {g["platform"]: g for g in data["trends"]}
    assert by_pf["weibo"]["ok"] is True and len(by_pf["weibo"]["items"]) == 1
    assert by_pf["bilibili"]["ok"] is False and by_pf["bilibili"]["items"] == []


def test_trends_ignores_unknown_platform(monkeypatch):
    """乱传平台名不能把接口带崩，也不能凭空多出渠道。"""
    web._TREND_CACHE.clear()
    monkeypatch.setattr(web, "_fetch_platform", lambda pf: [])   # 测试不打真实网络
    data = asyncio.run(web.api_trends("weibo,不存在", 5))
    assert [g["platform"] for g in data["trends"]] == ["weibo"]
    assert [g["label"] for g in data["trends"]] == ["微博"]


@pytest.mark.parametrize("primary_result", [None, {"data": []}, {"data": "invalid"}])
def test_fetch_platform_falls_back_on_error_or_empty_response(monkeypatch, primary_result):
    primary, backup = web.TREND_SOURCES["weibo"]
    calls = []

    def fetch(url):
        calls.append(url)
        if url == primary:
            if primary_result is None:
                raise OSError("unavailable")
            return primary_result
        return {"data": [{"title": "备用热点"}]}

    monkeypatch.setattr(web, "_http_get_json", fetch)
    assert web._fetch_platform("weibo") == [{"title": "备用热点", "hot": "", "url": ""}]
    assert calls == [primary, backup]


def test_trends_reuses_cached_items_when_all_sources_fail(monkeypatch):
    items = [{"title": "缓存热点", "hot": "", "url": ""}]
    web._TREND_CACHE["weibo"] = (0, items)
    monkeypatch.setattr(web, "_fetch_platform", lambda platform: [])
    data = asyncio.run(web.api_trends("weibo", 15))
    assert data["trends"][0]["items"] == items
    assert data["trends"][0]["ok"] is True


# ---- 共鸣度打分与 mode 参数 ----
#
# 背景：平台总榜天然被时政社会硬新闻占满，而用户赛道是家庭/情感/人际故事。后端因此加了一层
# "共鸣度"打分（_resonance_score）把相关选题捞出来重排，并由 /api/trends 的 mode 参数切换。
# 这里守住三件事：打分的正负向、单层封顶不被同义词刷分、以及 mode="all" 的向后兼容。

# 家庭/情感/人际类标题：关系词 + 情绪词 / 冲突词，是"看了想讲两句"的选题。
RESONANCE_TITLES = (
    "婆婆和儿媳因为彩礼闹翻，老公沉默我哭了",
    "我妈生病住院，我辞职照顾却被说不孝，哭了一夜",
    "孩子在学校被同学欺负，我该不该找老师",
    "婆婆来带娃后天天吵架，我快崩溃了",
)

# 官方通稿 / 资本市场类标题：一眼就是硬新闻，不该占用共鸣位。
HARD_NEWS_TITLES = (
    "国务院常务会议召开，部署新一轮政策，外交部发布声明",
    "公司发布年报，营收增长，证监会关注",
    "央行降准，A股涨停",
    "人工智能大会召开，多部门发布新政策",
)


def _item(title: str) -> dict:
    """造一条归一化后的热榜条目，形状与 _parse_hot 的输出一致。"""
    return {"title": title, "hot": "", "url": ""}


def _stub_fetch(monkeypatch, payload: dict) -> None:
    """把 _fetch_platform 换成固定条目，测试不打真实网络。"""
    monkeypatch.setattr(web, "_fetch_platform", lambda pf: [dict(i) for i in payload.get(pf, [])])


@pytest.mark.parametrize("title", RESONANCE_TITLES)
def test_resonance_score_rewards_personal_story_titles(title):
    """家庭/情感/人际类标题必须得正分——否则它们永远挤不过总榜上的硬新闻。"""
    assert web._resonance_score(title) > 0


@pytest.mark.parametrize("title", HARD_NEWS_TITLES)
def test_resonance_score_penalizes_hard_news_titles(title):
    """公文/会议/资本市场类标题要拿非正分，被 _resonance_group 过滤掉。"""
    assert web._resonance_score(title) <= 0


@pytest.mark.parametrize("raw", ["", None, "   ", "\n", "！！！", "###", "，。！？", "1234567890"])
def test_resonance_score_returns_zero_for_blank_or_symbol_only(raw):
    """空串 / None / 纯空白 / 纯符号要安静地返回 0，不能抛异常（脏标题会从接口直接透传进来）。"""
    assert web._resonance_score(raw) == 0


def test_resonance_score_strips_surrounding_whitespace():
    """首尾空白不该影响打分——真实热榜标题常带空格。"""
    assert web._resonance_score("  我妈生病住院  ") == web._resonance_score("我妈生病住院")
    assert web._resonance_score("  我妈生病住院  ") > 0


def test_resonance_score_caps_hits_within_a_layer():
    """同一层堆同义词不能无限加分：命中数超过 _RESONANCE_LAYER_CAP 后分数不再上涨。"""
    cap = web._RESONANCE_LAYER_CAP
    assert web._resonance_score("哭") == 4                       # 情绪层权重 4，单次命中照样计
    assert web._resonance_score("哭破防心寒") == 4 * cap          # 正好 cap 次
    assert web._resonance_score("哭破防心寒崩溃泪目") == 4 * cap   # 远超 cap 次，分数与上面一致


def test_resonance_score_sums_across_layers():
    """封顶只作用于单层：不同层的信号要能叠加，否则"关系词+情绪词"的强共鸣标题会被压平。"""
    assert web._resonance_score("哭破防心寒") == 12          # 情绪层 4 × cap(3)
    assert web._resonance_score("哭破防心寒离婚出轨") == 18   # 再叠冲突层 3 × 2


def test_resonance_group_merges_filters_sorts_and_truncates():
    """_resonance_group：合并多平台、滤掉 score<=0、按 score 降序、按 limit 截断，并带上来源平台。"""
    groups = [
        {"platform": "weibo", "label": "微博", "ok": True, "items": [
            _item("婆婆和儿媳因为彩礼闹翻，老公沉默我哭了"),        # 27
            _item("国务院常务会议召开，部署新一轮政策"),             # 硬新闻 → 滤掉
            _item("我妈生病住院，我辞职照顾却被说不孝，哭了一夜"),    # 23
        ]},
        {"platform": "douyin", "label": "抖音", "ok": True, "items": [
            _item("央行降准，A股涨停"),                              # 硬新闻 → 滤掉
            _item("孩子在学校被同学欺负，我该不该找老师"),            # 16
        ]},
    ]
    group = web._resonance_group(groups, 3)
    assert group["platform"] == "resonance" and group["label"] == "我的赛道"
    assert group["ok"] is True
    assert [i["title"] for i in group["items"]] == [
        "婆婆和儿媳因为彩礼闹翻，老公沉默我哭了",
        "我妈生病住院，我辞职照顾却被说不孝，哭了一夜",
        "孩子在学校被同学欺负，我该不该找老师",
    ]
    # 来源平台跟着条目走，前端点开才知道跳哪儿；原始字段也要保住
    assert [i["platform"] for i in group["items"]] == ["weibo", "weibo", "douyin"]
    assert [i["label"] for i in group["items"]] == ["微博", "微博", "抖音"]
    assert all(i["hot"] == "" and i["url"] == "" for i in group["items"])
    scores = [i["score"] for i in group["items"]]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)
    assert len(web._resonance_group(groups, 2)["items"]) == 2


def test_resonance_group_ok_is_false_only_when_every_platform_fails():
    """ok 语义与其它分组一致：所有源都挂了才算 False；取到了但全被滤掉仍是 True。"""
    dead = [
        {"platform": "weibo", "label": "微博", "ok": False, "items": []},
        {"platform": "douyin", "label": "抖音", "ok": False, "items": []},
    ]
    assert web._resonance_group(dead, 10) == {
        "platform": "resonance", "label": "我的赛道", "items": [], "ok": False,
    }

    fetched_but_filtered = [
        {"platform": "weibo", "label": "微博", "ok": True, "items": [_item("央行降准，A股涨停")]},
        {"platform": "douyin", "label": "抖音", "ok": False, "items": []},
    ]
    group = web._resonance_group(fetched_but_filtered, 10)
    assert group["ok"] is True and group["items"] == []


def test_api_trends_all_mode_keeps_legacy_shape(monkeypatch):
    """mode 不传（默认 "all"）时返回结构必须与改动前一致：多平台分组、条目无 score、不重排。"""
    payload = {
        "weibo": [_item("婆婆和儿媳因为彩礼闹翻，老公沉默我哭了"), _item("央行降准，A股涨停")],
        "douyin": [_item("孩子在学校被同学欺负，我该不该找老师")],
    }
    _stub_fetch(monkeypatch, payload)
    data = asyncio.run(web.api_trends("weibo,douyin", 5))
    assert [g["platform"] for g in data["trends"]] == ["weibo", "douyin"]
    assert [g["label"] for g in data["trends"]] == ["微博", "抖音"]
    assert "mode" not in data                      # 旧前端不认识这个字段，别凭空多出来
    assert isinstance(data["updated"], int)
    for g in data["trends"]:
        assert g["ok"] is True
        assert [i["title"] for i in g["items"]] == [i["title"] for i in payload[g["platform"]]]
        for item in g["items"]:
            assert set(item) == {"title", "hot", "url"}   # 不按共鸣度重排、不加 score


def test_api_trends_resonance_mode_returns_single_merged_group(monkeypatch):
    """mode="resonance"：多平台合并成一组「我的赛道」，顶层带 mode，条目带 score 且按分降序。"""
    payload = {
        "weibo": [_item("婆婆和儿媳因为彩礼闹翻，老公沉默我哭了"), _item("央行降准，A股涨停")],
        "douyin": [_item("孩子在学校被同学欺负，我该不该找老师"),
                   _item("人工智能大会召开，多部门发布新政策")],
    }
    _stub_fetch(monkeypatch, payload)
    data = asyncio.run(web.api_trends("weibo,douyin", 5, "resonance"))
    assert data["mode"] == "resonance"
    assert len(data["trends"]) == 1
    group = data["trends"][0]
    assert group["platform"] == "resonance" and group["label"] == "我的赛道"
    assert group["ok"] is True
    assert [i["title"] for i in group["items"]] == [
        "婆婆和儿媳因为彩礼闹翻，老公沉默我哭了",
        "孩子在学校被同学欺负，我该不该找老师",
    ]
    assert [i["platform"] for i in group["items"]] == ["weibo", "douyin"]
    assert all(i["score"] > 0 for i in group["items"])


def test_api_trends_resonance_mode_honours_limit(monkeypatch):
    """resonance 模式的输出仍按 limit 截断（候选池会放宽，但吐给前端的条数不变）。"""
    _stub_fetch(monkeypatch, {"weibo": [_item(t) for t in RESONANCE_TITLES]})
    data = asyncio.run(web.api_trends("weibo", 2, "resonance"))
    assert len(data["trends"][0]["items"]) == 2


def test_api_trends_unknown_mode_falls_back_to_all(monkeypatch):
    """没见过的 mode 值按 "all" 处理：不能崩，也不能给出半吊子的单组结构。"""
    payload = {
        "weibo": [_item("婆婆和儿媳因为彩礼闹翻，老公沉默我哭了")],
        "douyin": [_item("央行降准，A股涨停")],
    }
    _stub_fetch(monkeypatch, payload)
    data = asyncio.run(web.api_trends("weibo,douyin", 5, "xyz"))
    assert [g["platform"] for g in data["trends"]] == ["weibo", "douyin"]
    assert "mode" not in data
    assert all(set(i) == {"title", "hot", "url"} for g in data["trends"] for i in g["items"])


def test_api_trends_resonance_mode_ok_follows_source_availability(monkeypatch):
    """共鸣组的 ok 跟着数据源走：全都取不到是 False；取到了但全被滤成硬新闻仍是 True。"""
    monkeypatch.setattr(web, "_fetch_platform", lambda pf: [])
    data = asyncio.run(web.api_trends("weibo,douyin", 5, "resonance"))
    assert data["trends"][0]["ok"] is False and data["trends"][0]["items"] == []

    web._TREND_CACHE.clear()
    _stub_fetch(monkeypatch, {
        "weibo": [_item("央行降准，A股涨停")],
        "douyin": [_item("人工智能大会召开，多部门发布新政策")],
    })
    data = asyncio.run(web.api_trends("weibo,douyin", 5, "resonance"))
    assert data["trends"][0]["ok"] is True and data["trends"][0]["items"] == []
