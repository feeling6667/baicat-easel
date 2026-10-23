"""风格与字体写死（hard lock）+ 全局默认 的回归测试。

守住五件事：
1. assets/fb060-frazee.json 必须是 FB-060，且 style_lock / typography / negative_prompt 齐全；
2. 换风格编号或漏字体锁字段时，_load_locked_style() 必须直接 SystemExit(2)，不许带病出图；
3. 拼出来的 prompt 必须逐字带上写死的字体描述与负向约束，且页数据无法覆盖它们；
4. 字体分档：封面(kind=cover) → 手写马克笔粗楷体；正文/收尾(content/cta) → 乐米蜡笔体；关键词一律正红；
5. Easel 生图风格库里登记的 fb060-handdrawn 可加载、字体锁为真，且它是全局默认风格。
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "builtin" / "baicatcha"
STYLE_JSON = SKILL / "assets" / "fb060-frazee.json"
REGISTRY_JSON = (ROOT / "skills" / "builtin" / "skill-wechat-publisher"
                 / "assets" / "image-styles" / "fb060-handdrawn.json")
PUBLISHER_SCRIPTS = ROOT / "skills" / "builtin" / "skill-wechat-publisher" / "scripts"

COVER_FONT_KEY = "手写马克笔粗楷体"
COVER_FONT_EN = "handwritten marker-style bold kaishu"
BODY_FONT_KEY = "乐米蜡笔体"
EMPHASIS_COLOR = "#D93A2B"


def _load_pipeline():
    spec = importlib.util.spec_from_file_location("viral_pipeline_under_test",
                                                 SKILL / "scripts" / "viral_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 模块导入会 os.environ.setdefault("EASEL_ROOT", ...)；那会改变其他测试的 skip 条件
# （setup/web 那批用例一见到 EASEL_ROOT 就不再 skip）。这里导入后立刻还原环境。
_ENV_SNAPSHOT = dict(os.environ)
vp = _load_pipeline()
os.environ.clear()
os.environ.update(_ENV_SNAPSHOT)
del _ENV_SNAPSHOT


def _page(kind="cover"):
    return {
        "page": 1,
        "kind": kind,
        "title": "当老板说AI能替代你",
        "lines": ["凌晨一点删掉方案后"],
        "visual_zh": "深夜办公室里疲惫的小人",
        "visual_en": "a tired tiny professional in a late-night office",
        "theme_color": "#FFFDF8",
    }


def test_style_asset_is_locked_and_complete():
    style = json.loads(STYLE_JSON.read_text(encoding="utf-8"))
    assert style["style_index"] == vp.STYLE_LOCK_INDEX == "FB-060"
    assert style["locked"] is True
    for field in vp.STYLE_LOCK_FIELDS:
        assert style.get(field), f"缺少写死字段 {field}"
    typo = style["typography"]
    assert typo["locked"] is True
    assert typo["generated_by_model"] is True, "字体必须由模型直接生成（不做后期叠字）"
    assert typo["cover"]["font_name"] == COVER_FONT_KEY
    assert typo["body"]["font_name"].startswith(BODY_FONT_KEY)
    assert typo["route"] == {"cover": "cover", "content": "body", "cta": "body", "default": "body"}
    assert style["canvas"] == {"ratio": "3:4", "width": 1080, "height": 1440}


def test_loader_rejects_wrong_style_index(monkeypatch, tmp_path):
    bad = json.loads(STYLE_JSON.read_text(encoding="utf-8"))
    bad["style_index"] = "FB-999"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vp, "FB060_JSON", path)
    with pytest.raises(SystemExit) as exc:
        vp._load_locked_style()
    assert exc.value.code == 2


def test_loader_rejects_missing_font_lock(monkeypatch, tmp_path):
    bad = json.loads(STYLE_JSON.read_text(encoding="utf-8"))
    bad.pop("typography")
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vp, "FB060_JSON", path)
    with pytest.raises(SystemExit) as exc:
        vp._load_locked_style()
    assert exc.value.code == 2


@pytest.mark.parametrize("kind,expected,unexpected", [
    ("cover", COVER_FONT_KEY, BODY_FONT_KEY),
    ("content", BODY_FONT_KEY, COVER_FONT_KEY),
    ("cta", BODY_FONT_KEY, COVER_FONT_KEY),
])
def test_font_slot_routing(kind, expected, unexpected):
    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, _page(kind))
    assert expected in prompt, f"kind={kind} 应该用 {expected}"
    assert unexpected not in prompt, f"kind={kind} 不应该出现 {unexpected}"


@pytest.mark.parametrize("kind", ["cover", "content", "cta"])
def test_prompt_carries_locked_style_and_negative(kind):
    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, _page(kind))
    # 风格写死，但不得指名在世画师（指名会触发 arena / 上游模型的条款拦截，
    # 风格改由所附参考图锚定；见 2026-10-22 A/B 实测）
    assert "Marla Frazee" not in prompt
    assert "参考图" in prompt
    assert "铅笔速写线稿打底" in prompt
    assert "水彩晕染透明上色" in prompt
    # 关键词正红（两档字体都有）
    assert EMPHASIS_COLOR in prompt
    assert "【文字禁止项】" in prompt and "印刷体/宋体/黑体" in prompt
    # 负向约束写死
    assert "照片写实" in prompt and "赛博朋克" in prompt
    # 画幅写死
    assert "1080x1440" in prompt
    # 页数据照常进入 prompt
    assert "当老板说AI能替代你" in prompt


def test_page_data_cannot_override_style_or_font():
    style = vp._load_locked_style()
    hijack = _page("content")
    hijack["typography"] = {"zh_heading": "微软雅黑"}
    hijack["traits"] = ["赛博朋克霓虹"]
    hijack["prompt_template_zh"] = "随便画"
    prompt = vp._build_prompt(style, hijack)
    assert "微软雅黑" not in prompt
    assert "赛博朋克霓虹" not in prompt
    assert BODY_FONT_KEY in prompt


def test_unknown_kind_falls_back_to_body_font():
    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, _page("whatever"))
    assert BODY_FONT_KEY in prompt


def test_text_hard_contract_present_and_forceful():
    """文字硬性契约：同一支笔 / 小字不许掉回机打体 / 不许叠字 —— 缺一条就算回归。"""
    style = vp._load_locked_style()
    rules = style["typography"]["hard_contract"]
    assert len(rules) >= 6
    contract = vp._locked_contract(style)
    for must in ("同一支笔", "手写笔触", "印刷体", "叠字", "字距", "行高", "35%", "换字体"):
        assert must in contract, f"契约里缺少 {must}"
    assert contract.startswith("1) ") and "；2) " in contract

    for kind in ("cover", "content", "cta"):
        prompt = vp._build_prompt(style, _page(kind))
        assert "文字硬性契约" in prompt or "硬性契约" in prompt
        assert "违反即判废图" in prompt
        # 关键词只换色不换字形
        assert "只换色" in prompt or "只换颜色" in prompt
        # 小字必须单独点名（实测最容易掉回黑体的位置）
        assert "小字" in prompt
        # prompt 里不许残留 markdown 星号（会被模型当正文画出来）
        assert "**" not in prompt


def test_subline_uses_same_font_explicitly():
    """封面：短句同款字体、约 45%；正文：同一字号，不许缩不许放。"""
    style = vp._load_locked_style()
    cover_block = style["typography"]["cover"]
    assert "45%" in cover_block["zh_subline"]
    cover_prompt = vp._build_prompt(style, _page("cover"))
    assert cover_block["zh_subline"] in cover_prompt

    body_block = style["typography"]["body"]
    assert "字号" in body_block["zh_subline"]
    assert "必须一致" in body_block["zh_subline"]
    assert "45%" not in body_block["zh_subline"]
    body_prompt = vp._build_prompt(style, _page("content"))
    assert body_block["zh_subline"] in body_prompt


def test_content_pages_have_no_big_headline_and_uniform_size():
    """正文/收尾页：不设大标题，标题与短句同一字号（逐字同大）。"""
    style = vp._load_locked_style()
    body = style["typography"]["body"]
    assert "不分标题" in body["zh_layout"]
    assert "一种字号" in body["zh_layout"] and "字符高度必须完全相等" in body["zh_layout"]

    for kind in ("content", "cta"):
        prompt = vp._build_prompt(style, _page(kind))
        assert "无大标题" in prompt
        assert "逐字同大" in prompt
        assert "大字标题" not in prompt, "正文页不该出现大字标题"
        assert "同一字号" in prompt
        # 行数与每行文字都要给全
        assert f"「{_page(kind)['title']}」" in prompt
    # 契约层面也要求了
    contract = vp._locked_contract(style)
    assert "不分主次" in contract and "字符高度必须完全相等" in contract
    # 契约里的「封面标题」是整套图的字号锚点，不属于正文页内部的层级语言；
    # 2026-10-23 起因正文字号固定为封面标题的 65%（用户要求），故排除该词后再查层级残留。
    assert "标题" not in contract.replace("封面标题", ""), "契约里不该再出现标题/短句的层级语言"
    assert "65%" in contract, "契约里必须写死正文字号＝封面标题的 65%"


def test_cover_keeps_headline_hierarchy():
    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, _page("cover"))
    assert "大字标题" in prompt
    assert "45%" in prompt
    assert "无大标题" not in prompt


def test_registry_entry_is_loadable_and_locked():
    assert REGISTRY_JSON.is_file()
    sys.path.insert(0, str(PUBLISHER_SCRIPTS))
    import config

    assert "fb060-handdrawn" in config.list_image_styles()
    style = config.get_image_style("fb060-handdrawn")
    assert style["locked"] is True
    assert style["typography"]["locked"] is True
    assert style["typography"]["cover"]["font_name"] == COVER_FONT_KEY
    assert BODY_FONT_KEY in style["typography"]["body"]["font_name"]
    assert BODY_FONT_KEY in style["prompt_template"]["newspic_card"]
    # 登记副本的 prompt 模板是英文的：正文卡只写乐米蜡笔体，封面卡只写马克笔粗楷体
    assert "LemiLaBiTi" in style["prompt_template"]["newspic_card"]
    assert COVER_FONT_EN not in style["prompt_template"]["newspic_card"]
    assert COVER_FONT_EN in style["prompt_template"]["cover_card"]
    assert "LemiLaBiTi" not in style["prompt_template"]["cover_card"]
    for key in ("newspic_card", "cover_card", "article_inline"):
        assert "{card_main}" in style["prompt_template"][key] or "{image_subject}" in style["prompt_template"][key]
    # 英文硬性契约（小字不许掉回机打体 / 不许叠字）必须进每一套模板
    assert len(style["typography"]["hard_contract"]) >= 6
    assert len(style["typography"]["hard_contract_en"]) >= 6
    for key in ("newspic_card", "cover_card", "article_inline"):
        tmpl = style["prompt_template"][key]
        assert "LOCKED TYPOGRAPHY CONTRACT" in tmpl
        assert "NEVER fall back to a printed" in tmpl
        assert "NO overlapping glyphs" in tmpl


def test_global_default_style_is_fb060():
    sys.path.insert(0, str(PUBLISHER_SCRIPTS))
    import config

    assert config.DEFAULT_IMAGE_STYLE == "fb060-handdrawn"
    assert config.DEFAULT_NEWSPIC_IMAGE_STYLE == "fb060-handdrawn"
    # 不传 name 时拿到的就是写死风格
    assert config.get_image_style()["style_name"] == "fb060-handdrawn"
    assert config.resolve_image_style()["style_name"] == "fb060-handdrawn"


# ── 关键词 2-4 词 / 字号自适应 / 人物一致性 ───────────────────
CHARACTERS = [
    {"name": "小满", "role": "女儿，8 岁",
     "look_zh": "齐耳黑短发扎两个小揪揪（红头绳）、圆脸、弯月眼；淡黄长袖上衣、靛蓝背带裤、白球鞋",
     "look_en": "8-year-old girl, ear-length black hair in two small buns, round face, pale-yellow top, indigo overalls"},
    {"name": "爸爸", "role": "父亲，42 岁",
     "look_zh": "黑色寸头、国字脸、短胡茬；深灰夹克、深色长裤、旧布鞋",
     "look_en": "father, 42, short black hair, square face, dark grey jacket, dark trousers"},
]


def _cast_line(prompt):
    m = re.search(r"【本页可出现的人物】([^【。]*)", prompt)
    return m.group(1) if m else ""


def test_keyword_emphasis_is_two_to_four_words():
    style = vp._load_locked_style()
    for slot in ("cover", "body"):
        assert "2-4 个词" in style["typography"][slot]["zh_emphasis"]
    for kind in ("cover", "content", "cta"):
        assert "2-4 个词" in vp._build_prompt(style, _page(kind))


def test_auto_fit_typesetting_present():
    style = vp._load_locked_style()
    for kind in ("cover", "content"):
        prompt = vp._build_prompt(style, _page(kind))
        assert "自适应" in prompt
        assert "78%" in prompt


def test_character_lock_block_and_cast_filtering():
    style = vp._load_locked_style()
    cover = dict(_page("cover"), cast=["小满", "爸爸"])
    p1 = vp._build_prompt(style, cover, CHARACTERS)
    assert "人物一致性" in p1
    assert "跨页锁定" in p1
    assert "小满（女儿，8 岁）" in p1 and "爸爸（父亲，42 岁）" in p1
    assert _cast_line(p1) == "小满、爸爸"

    cta = dict(_page("cta"), cast=["爸爸"])
    p3 = vp._build_prompt(style, cta, CHARACTERS)
    assert _cast_line(p3) == "爸爸"
    assert "小满（女儿，8 岁）" not in p3


def test_no_characters_means_no_lock_block():
    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, _page("cover"), [])
    assert "人物一致性" not in prompt


def test_character_rules_cover_outfit_and_face():
    style = vp._load_locked_style()
    block = style["character_consistency"]
    assert block["locked"] is True
    joined = "".join(block["rules"])
    for must in ("同一张脸", "发型", "衣服", "体型", "换装", "陌生人"):
        assert must in joined
    assert len(block["rules"]) >= 4


def test_storyboard_carries_characters_and_cast(tmp_path):
    """storyboard 必须把 characters + 每页 cast 落进 pages.json（否则 gen_pages 无从注入）。"""
    from types import SimpleNamespace

    out = tmp_path / "proj"
    (out / "work").mkdir(parents=True)
    cjk = ["一", "二", "三"]
    storyboard = {
        "topic": "测试",
        "analysis": {"hook": "h", "conflict": "c", "density": "d", "golden": "g", "cta": "x"},
        "rewrite": {"hook": "h", "buildup": "b", "expand": "e", "golden": "g", "cta": "x"},
        "characters": CHARACTERS,
        "pages": [
            {"kind": "cover", "title": cjk[0], "cast": ["小满", "爸爸"],
             "visual_zh": f"场景{cjk[0]}"},
            {"kind": "content", "title": cjk[1], "cast": ["小满"], "visual_zh": f"场景{cjk[1]}"},
            {"kind": "cta", "title": cjk[2], "cast": ["爸爸", "查无此人"],
             "visual_zh": f"场景{cjk[2]}"},
        ],
    }
    sb_path = out / "work" / "storyboard.json"
    sb_path.write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")

    rc = vp.cmd_storyboard(SimpleNamespace(dir=str(out), json=str(sb_path)))
    assert rc == 0

    payload = json.loads((out / "pages.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in payload["characters"]] == ["小满", "爸爸"]
    assert payload["pages"][0]["cast"] == ["小满", "爸爸"]
    assert payload["pages"][2]["cast"] == ["爸爸"], "未定义的角色名应被过滤"

    style = vp._load_locked_style()
    prompt = vp._build_prompt(style, payload["pages"][2], payload["characters"])
    assert _cast_line(prompt) == "爸爸"


# ── 页数 / 字数不设上限（用户明确要求，不得再引入）─────────────
def test_no_page_or_length_limits(tmp_path):
    """1 页 storyboard、长标题、多行短句、超长文案都必须原样通过。"""
    from types import SimpleNamespace

    out = tmp_path / "proj"
    (out / "work").mkdir(parents=True)
    long_title = "我爸攒了十年的火车票原来一张都没丢过"        # 18 字
    long_line = "最早那张是我上大学那年冬天最冷的一个晚上他站在站台等了我两个小时"  # 32 字
    storyboard = {
        "topic": "限制取消",
        "analysis": {"hook": "h", "conflict": "c", "density": "d", "golden": "g", "cta": "x"},
        "rewrite": {"hook": "h", "buildup": "b", "expand": "e", "golden": "g", "cta": "x"},
        "pages": [{"kind": "cover", "title": long_title,
                   "lines": [long_line, "第二行", "第三行", "第四行", "第五行"],
                   "visual_zh": "老屋柜顶的铁盒"}],
    }
    sb = out / "work" / "storyboard.json"
    sb.write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
    assert vp.cmd_storyboard(SimpleNamespace(dir=str(out), json=str(sb))) == 0

    pages = json.loads((out / "pages.json").read_text(encoding="utf-8"))["pages"]
    assert len(pages) == 1, "1 页必须允许"
    assert pages[0]["title"] == long_title, "页标题不得被截断"
    assert len(pages[0]["lines"]) == 5, "短句条数不得被裁剪"
    assert pages[0]["lines"][0] == long_line, "短句字数不得被截断"

    copy = {"title": "一个长达三十个字符的小红书标题用来验证字数限制是否真的取消了🎯",
            "body": "短正文。", "tags": ["手绘", "故事"]}
    cp = out / "work" / "copy.json"
    cp.write_text(json.dumps(copy, ensure_ascii=False), encoding="utf-8")
    assert vp.cmd_copy(SimpleNamespace(dir=str(out), json=str(cp))) == 0


def test_source_has_no_page_or_length_limits():
    """源码里不许再出现页数/字数上限的硬拦。"""
    src = (SKILL / "scripts" / "viral_pipeline.py").read_text(encoding="utf-8")
    for forbidden in ("3 <= len(pages_raw)", "len(ok) < 3", "len(pngs) < 3",
                      "_clip(p.get(\"title\"", "280 <= body_len", "len(tags) < 5"):
        assert forbidden not in src, f"限制又回来了：{forbidden}"
