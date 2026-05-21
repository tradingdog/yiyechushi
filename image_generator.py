from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_FILE = ROOT_DIR / "临时调试prompt.txt"
DEFAULT_IDEA_FILE = ROOT_DIR / "dish_name.txt"
OUTPUT_ROOT_DIR = ROOT_DIR / "output"
CREATIVE_OUTPUT_DIR = OUTPUT_ROOT_DIR / "chuangyi"
PROMPT_OUTPUT_DIR = OUTPUT_ROOT_DIR / "prompt"
IMAGE_OUTPUT_DIR = OUTPUT_ROOT_DIR / "image"
DEFAULT_OUTPUT_DIR = IMAGE_OUTPUT_DIR
DEFAULT_AD_COPY_FILE = ROOT_DIR / "guanggaoyu.txt"
DEFAULT_COLLECTION_HINT = "先收藏，想做时直接照着买照着做"
DEFAULT_COLLECTION_COPY = "这张先收藏 原创新菜照着做更稳"
DEFAULT_DYNAMIC_ACTION = "一双木筷从画面侧上方夹起一块主菜悬在半空 带轻微挂汁与热气"
DEFAULT_REQUEST_RETRY_COUNT = 2


def build_recipe_system_prompt(ad_copy: str, fixed_dish_name: str) -> str:
    return f"""
你是一页厨账号的原创融合新菜研发编辑，负责把用户给出的菜名想法或口味灵感，扩写成一张竖版一页菜谱海报所需的完整中文文案。

你的目标不是复述常见菜谱，而是做出“原创融合新菜研发”风格的新菜：
1. 成品必须像市面上少见但逻辑成立、家庭和小店都能复刻的原创菜。
2. 如果用户给的是常见菜名，也要基于做法、口感、卖点、结构、器皿或场景做明显优化，但最终菜名必须保持用户输入原样。
3. 要优先吸收用户的补充说明，例如摆盘、器皿、核心调味、关键操作和口感方向。
4. 全部配方与步骤统一使用 g、ml、L、分钟 这类物理量单位，不要使用“适量、少许、1勺”这类模糊表达。
5. 默认按 2 人份来写，步骤控制在 5 步，适合做成高密度、易收藏的一页菜谱海报。
6. 成败关键固定输出 5 条短句，每条都不要使用逗号、句号、顿号等标点。
7. 底部关注文案必须固定为：{ad_copy}
8. 底部收藏文案必须固定为：{DEFAULT_COLLECTION_COPY}
9. 收藏提示语必须固定为：{DEFAULT_COLLECTION_HINT}
10. 最终菜名必须严格等于：{fixed_dish_name}
11. 主画面必须有一双筷子夹起一块主菜悬在半空，画面要有动作感和食欲感。

输出必须严格遵循下面这份纯文本结构，不要添加解释，不要使用 Markdown 代码块：

【基础定位】
创意来源：...
最终菜名：{fixed_dish_name}
引导句：...
副标题：...
收藏提示：{DEFAULT_COLLECTION_HINT}
账号定位：原创融合新菜研发

【主画面说明】
器皿与摆盘：...
主画面食材：...
汤汁或酱体：...
质感重点：...
动态动作：{DEFAULT_DYNAMIC_ACTION}
色彩点缀：...

【2人份食材】
主料
- 食材名 数量
- 食材名 数量

香料
- 食材名 数量
- 食材名 数量

调味料
- 食材名 数量
- 食材名 数量

【成败关键】
1. ...
2. ...
3. ...
4. ...
5. ...

【5步出锅】
1. 标题：...
内容：...
2. 标题：...
内容：...
3. 标题：...
内容：...
4. 标题：...
内容：...
5. 标题：...
内容：...

【底部文案】
收藏文案：{DEFAULT_COLLECTION_COPY}
关注文案：{ad_copy}

额外要求：
1. 标题要有明显带动性句式，但主标题菜名必须直接使用用户输入的固定菜名，不能改字，不能另起新名。
2. 引导句必须像“周末请客就做这锅”这种成熟爆款写法，短 6 到 10 个汉字，最多不超过 12 个汉字，不要标点，不要长句，不要解释型语气，不要把菜名再重复一遍。
3. 副标题必须像“高压锅1小时出软糯蹄花 酸辣鲜香 一锅超有面子”这种黄条卖点写法，压缩成 2 到 3 个短卖点，总长度控制在 24 个汉字以内，优先用空格隔开，不要写成长句。
4. 主画面说明要足够具体，让后续文生图阶段知道器皿、主体食材、汤汁或酱体状态、画面颜色重点，还要明确筷子夹起主菜的动作镜头。
4. 食材分组要清楚，数量要合理，不能互相打架。
5. 5 步必须前后顺序清晰，适合普通人照做。
6. 文字整体要像成熟抖音爆款图文海报，而不是教程论文或餐厅菜单。
""".strip()


def build_recipe_user_prompt(dish_idea: str, notes: str) -> str:
    note_text = notes or "无补充说明，请按原创融合新菜方向自行补齐。"
    return f"""
用户输入的菜名或创意：{dish_idea}
用户补充说明：{note_text}

请你基于这两部分信息，写出一整张一页菜谱海报所需的完整文案。
如果输入看起来像现有家常菜，也要把卖点、做法重点和画面表现提炼得更有记忆点，但最终菜名必须保持“{dish_idea}”完全不变。
""".strip()


def build_prompt_system_prompt(style_reference: str, fixed_dish_name: str) -> str:
    return f"""
你是一页厨账号的海报 prompt 导演。你的任务是把一份已经完成的中文菜谱文案，转换成一条给 gpt-image-2 使用的完整中文文生图 prompt。

你必须锁定以下 VI 与版式，不要自由发挥成别的风格：
1. 竖版 2:3 中文抖音美食图文海报。
2. 整体是成熟爆款菜谱海报风，不是极简风，不是杂志风，不要大面积留白。
3. 顶部是“引导句小于菜名”的双层标题结构，菜名是最大主标题，橙红或朱红手写刷字质感，带明显白边或高对比描边。
4. 标题下方要有一条高转化卖点副标题黄条，再下一条较细的收藏提醒条。
5. 中间偏左是主菜成品大图，必须是写实热门美食封面效果。
6. 左侧是“2人份食材”卡片，右侧是“成败关键”卡片。
7. 下半部分是“5步出锅”步骤条，5 个步骤卡横向排开，信息密度高但仍清楚。
8. 底部是一整条收藏与关注 CTA 横条，颜色醒目，像成熟爆款海报。
9. 主配色固定为暖奶白、橙红、金黄、焦糖棕，背景允许木桌、香料、小食材、暖色虚化氛围。
10. 所有中文必须自然工整，不要英文，不要乱码，不要错字。
11. 主标题必须直接使用“{fixed_dish_name}”这 1 个菜名，不能改字，不能扩写，不能另起新名。
12. 主画面必须出现一双筷子从画面边缘夹起一块主菜悬在半空，形成明显动作感和食欲点。
13. 主标题上方的引导句必须很短，控制在 12 个汉字以内。
14. 主标题下方黄条卖点必须精简成 2 到 3 个短卖点，总长度控制在 24 个汉字以内。

请优先继承下面这份当前满意版本的参考 prompt 的风格取向，但不要照抄其中具体菜名和配方，只复用它的版式、密度、配色、语气、卡片结构和负面约束：

---参考 prompt 开始---
{style_reference}
---参考 prompt 结束---

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要加解释。
2. 必须把用户菜谱中的标题、副标题、食材、成败关键、5 步内容和底部文案全部吸收到 prompt 里。
3. 主画面必须以菜谱中的器皿与摆盘说明为准。
4. 强调整张图是“信息很多但一眼就想收藏”的成熟爆款海报。
5. 保留清晰的负面约束，避免极简、错误结构、食材畸形、塑料感、贴边、乱码和过度装饰。
6. 明确写出筷子夹起主菜的镜头，并说明不要人物脸部，不要手部特写，只保留筷子和主菜动作。
""".strip()


def build_prompt_user_prompt(recipe_text: str, fixed_dish_name: str) -> str:
    return f"""
请根据下面这份已经定稿的菜谱文字，写出最终文生图 prompt：

主标题必须直接使用：{fixed_dish_name}
画面里必须出现一双筷子夹起一块主菜在半空。
引导句和副标题都要短，不要写成长句。

{recipe_text}
""".strip()


def load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_client() -> OpenAI:
    load_env_file(ROOT_DIR / ".env")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请先在 .env 文件中配置。")

    request_timeout = get_request_timeout_seconds()

    return OpenAI(api_key=api_key, timeout=request_timeout)


def get_request_timeout_seconds() -> float:
    timeout_text = os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "900").strip() or "900"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def is_timeout_error(exc: Exception) -> bool:
    return "timed out" in str(exc).lower()


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def dedupe_items(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for name, amount in items:
        if name in seen:
            continue
        seen.add(name)
        deduped.append((name, amount))
    return deduped


def join_selling_points(points: list[str], max_chars: int = 24) -> str:
    filtered = [point.strip() for point in points if point.strip()]
    if not filtered:
        return "层次清楚 一盘就想配饭"

    chosen: list[str] = []
    for point in filtered:
        candidate = " ".join(chosen + [point]).strip()
        candidate_length = len(candidate.replace(" ", ""))
        if candidate_length <= max_chars or not chosen:
            chosen.append(point)

    return " ".join(chosen).strip()


def infer_guide_line(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    if contains_any(combined_text, ["下饭", "拌饭", "配饭"]):
        return "米饭党就做这盘"
    if contains_any(combined_text, ["请客", "宴客", "待客"]):
        return "请客就做这盘"
    if contains_any(combined_text, ["汤", "锅", "煲"]):
        return "就馋这口热乎"
    if contains_any(combined_text, ["脆", "焦", "煎"]):
        return "就馋这口焦香"
    return "今天就做这盘"


def infer_subtitle(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    selling_points: list[str] = []

    if contains_any(combined_text, ["黄豌豆", "耙豌豆", "软糯", "高压锅"]):
        selling_points.append("黄豌豆压到软糯")
    if contains_any(combined_text, ["豆腐", "金黄", "焦色", "煎"]):
        selling_points.append("豆腐两面煎焦香")
    if contains_any(combined_text, ["肉沫", "猪肉"]):
        selling_points.append("肉香裹汁更有层次")
    if contains_any(combined_text, ["下饭", "拌饭", "配饭"]):
        selling_points.append("一盘超下饭")
    if contains_any(combined_text, ["酸", "梅子", "青柠"]):
        selling_points.append("酸香提味更开胃")
    if contains_any(combined_text, ["黄油", "焦葱", "香"]):
        selling_points.append("咸香浓郁有记忆点")

    return join_selling_points(selling_points)


def infer_plate_description(notes: str) -> str:
    if "木筛盘" in notes:
        return "木筛盘加一层食物纸铺垫"
    if "黑陶锅" in notes:
        return "浅口黑陶锅配木托"
    if "木托" in notes:
        return "暖色陶盘配木托"
    return "暖色陶盘配木纹桌面"


def infer_main_food_description(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]) and contains_any(combined_text, ["豆腐", "肉沫"]):
        return "金黄焦边豆腐块裹着软糯耙豌豆和油润肉沫，盘中有明显颗粒层次和浓稠挂汁"
    if contains_any(combined_text, ["鸡肉", "鸡腿肉", "年糕"]):
        return "主菜堆叠有层次，鸡肉表面油亮，年糕边缘微焦，酱汁包裹均匀"
    return "主菜主体清楚，块面饱满，表面有自然挂汁和热气"


def infer_sauce_description(notes: str) -> str:
    if contains_any(notes, ["勾芡", "浓稠", "挂汁"]):
        return "酱汁浓稠，能明显挂在主菜表面，不是稀汤感"
    if contains_any(notes, ["汤", "清水", "煲"]):
        return "汤汁饱满但不浑浊，有热气和油亮反光"
    return "酱汁均匀包裹主菜，画面有热气和油亮感"


def infer_texture_description(notes: str) -> str:
    if contains_any(notes, ["软糯", "粘性强"]):
        return "主菜同时呈现软糯、焦香、挂汁三种口感层次"
    if contains_any(notes, ["脆", "焦"]):
        return "表面微焦微脆，内部保留湿润和饱满质感"
    return "质感真实自然，不能像塑料模型"


def infer_color_description(notes: str) -> str:
    if contains_any(notes, ["老抽", "黄豆酱"]):
        return "主色为焦糖棕、暖金黄和葱花鲜绿"
    if contains_any(notes, ["青柠", "梅子"]):
        return "主色为暖金黄、青绿色和少量酸香色点缀"
    return "主色为暖奶白、橙金和焦糖棕"


def infer_main_ingredients(dish_name: str, notes: str) -> list[tuple[str, str]]:
    combined_text = f"{dish_name} {notes}"
    ingredients: list[tuple[str, str]] = []
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]):
        ingredients.append(("黄豌豆", "250g"))
    if "豆腐" in combined_text:
        ingredients.append(("老豆腐", "400g"))
    if contains_any(combined_text, ["肉沫", "猪肉"]):
        ingredients.append(("猪肉沫", "150g"))
    if contains_any(combined_text, ["鸡腿肉", "鸡肉"]):
        ingredients.append(("鸡腿肉", "300g"))
    if "年糕" in combined_text:
        ingredients.append(("年糕片", "220g"))
    if "虾" in combined_text:
        ingredients.append(("鲜虾", "250g"))
    if not ingredients:
        ingredients.append((dish_name, "300g"))
    return dedupe_items(ingredients)


def infer_spice_ingredients(notes: str) -> list[tuple[str, str]]:
    spices: list[tuple[str, str]] = []
    if "葱花" in notes:
        spices.append(("葱花", "10g"))
    if contains_any(notes, ["青花椒", "花椒"]):
        spices.append(("青花椒", "4g"))
    if "藤椒" in notes:
        spices.append(("藤椒", "4g"))
    if "薄荷" in notes:
        spices.append(("薄荷碎", "3g"))
    if not spices:
        spices.append(("葱花", "10g"))
    return dedupe_items(spices)


def infer_seasoning_ingredients(notes: str) -> list[tuple[str, str]]:
    seasonings: list[tuple[str, str]] = []
    if "黄豆酱" in notes:
        seasonings.append(("黄豆酱", "25g"))
    if "老抽" in notes:
        seasonings.append(("老抽", "10ml"))
    if "盐" in notes:
        seasonings.append(("盐", "4g"))
    if "味精" in notes:
        seasonings.append(("味精", "2g"))
    if "鸡精" in notes:
        seasonings.append(("鸡精", "2g"))
    if contains_any(notes, ["水", "清水"]):
        seasonings.append(("清水", "300ml"))
    if contains_any(notes, ["勾芡", "淀粉"]):
        seasonings.append(("水淀粉", "20ml"))
    if not seasonings:
        seasonings.extend([
            ("盐", "4g"),
            ("生抽", "15ml"),
            ("清水", "250ml"),
        ])
    return dedupe_items(seasonings)


def infer_key_tips(dish_name: str, notes: str) -> list[str]:
    tips: list[str] = []
    if contains_any(notes, ["黄豌豆", "耙豌豆", "高压锅", "软糯"]):
        tips.append("黄豌豆一定先压到软糯")
    if contains_any(notes, ["豆腐", "煎", "金黄", "焦色"]):
        tips.append("豆腐先煎出焦边再合炒")
    if contains_any(notes, ["肉沫", "猪肉"]):
        tips.append("肉沫先炒香再并入主菜")
    if contains_any(notes, ["水到豆腐的一半", "加水到豆腐的一半", "加水"]):
        tips.append("加水别没过豆腐")
    if contains_any(notes, ["勾芡", "浓稠"]):
        tips.append("最后勾芡把汁收到位")
    if not tips:
        tips.extend([
            "主料火候要先做透",
            "酱汁要能挂住主料",
            "出锅前再补最终香气",
        ])
    return tips[:5]


def infer_steps(dish_name: str, notes: str) -> list[dict[str, str]]:
    combined_text = f"{dish_name} {notes}"
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]) and contains_any(combined_text, ["豆腐", "肉沫"]):
        return [
            {
                "title": "压豌豆",
                "content": "黄豌豆加清水没过，电高压锅压30分钟，压到颗粒松软带明显糯感",
            },
            {
                "title": "炒肉沫",
                "content": "锅中下猪肉沫150g炒散，加入黄豆酱25g、老抽10ml炒到肉香和酱香完全融合",
            },
            {
                "title": "煎豆腐",
                "content": "老豆腐切厚块，下锅煎到两面微金黄，边缘有轻微焦香感再盛出",
            },
            {
                "title": "合炒烧味",
                "content": "把耙豌豆、肉沫和豆腐回锅同炒，加入盐4g、味精2g、鸡精2g和清水300ml，大火烧5分钟",
            },
            {
                "title": "勾芡出锅",
                "content": "最后淋入水淀粉20ml把汁收浓，撒葱花10g，装入木筛盘食物纸上桌",
            },
        ]

    return [
        {"title": "备主料", "content": "先把主料和核心配料按出菜顺序处理干净备用"},
        {"title": "做底味", "content": "先把香味和底味炒出来，让主菜后续更容易挂味"},
        {"title": "处理主菜", "content": "把主菜做到七八成熟，保留最关键的口感层次"},
        {"title": "合味收汁", "content": "把主料和调味重新合在一起，让汤汁或酱汁均匀包裹主菜"},
        {"title": "出锅装盘", "content": "最后补香并装盘，保留热气、亮泽和最强食欲感"},
    ]


def build_local_recipe_bundle(dish_name: str, notes: str, ad_copy: str) -> dict[str, Any]:
    normalized_notes = " ".join(notes.split())
    return {
        "dish_name": dish_name,
        "guide_line": infer_guide_line(dish_name, normalized_notes),
        "subtitle": infer_subtitle(dish_name, normalized_notes),
        "collection_hint": DEFAULT_COLLECTION_HINT,
        "collection_copy": DEFAULT_COLLECTION_COPY,
        "ad_copy": ad_copy,
        "plate": infer_plate_description(normalized_notes),
        "main_food": infer_main_food_description(dish_name, normalized_notes),
        "sauce": infer_sauce_description(normalized_notes),
        "texture": infer_texture_description(normalized_notes),
        "dynamic_action": DEFAULT_DYNAMIC_ACTION,
        "colors": infer_color_description(normalized_notes),
        "main_ingredients": infer_main_ingredients(dish_name, normalized_notes),
        "spices": infer_spice_ingredients(normalized_notes),
        "seasonings": infer_seasoning_ingredients(normalized_notes),
        "tips": infer_key_tips(dish_name, normalized_notes),
        "steps": infer_steps(dish_name, normalized_notes),
        "notes": normalized_notes,
    }


def render_recipe_bundle_text(bundle: dict[str, Any]) -> str:
    main_ingredients = "\n".join(f"- {name} {amount}" for name, amount in bundle["main_ingredients"])
    spices = "\n".join(f"- {name} {amount}" for name, amount in bundle["spices"])
    seasonings = "\n".join(f"- {name} {amount}" for name, amount in bundle["seasonings"])
    tips = "\n".join(f"{index}. {tip}" for index, tip in enumerate(bundle["tips"], start=1))
    steps = []
    for index, step in enumerate(bundle["steps"], start=1):
        steps.append(f"{index}. 标题：{step['title']}\n内容：{step['content']}")

    return f"""
【基础定位】
创意来源：根据用户输入菜名和补充说明整理为可直接出图的一页菜谱
最终菜名：{bundle['dish_name']}
引导句：{bundle['guide_line']}
副标题：{bundle['subtitle']}
收藏提示：{bundle['collection_hint']}
账号定位：原创融合新菜研发

【主画面说明】
器皿与摆盘：{bundle['plate']}
主画面食材：{bundle['main_food']}
汤汁或酱体：{bundle['sauce']}
质感重点：{bundle['texture']}
动态动作：{bundle['dynamic_action']}
色彩点缀：{bundle['colors']}

【2人份食材】
主料
{main_ingredients}

香料
{spices}

调味料
{seasonings}

【成败关键】
{tips}

【5步出锅】
{'\n'.join(steps)}

【底部文案】
收藏文案：{bundle['collection_copy']}
关注文案：{bundle['ad_copy']}
""".strip()


def build_local_image_prompt(bundle: dict[str, Any]) -> str:
    main_ingredients = "\n".join(f"{name} {amount}" for name, amount in bundle["main_ingredients"])
    spices = "\n".join(f"{name} {amount}" for name, amount in bundle["spices"])
    seasonings = "\n".join(f"{name} {amount}" for name, amount in bundle["seasonings"])
    tips = "\n".join(bundle["tips"])
    steps = []
    for index, step in enumerate(bundle["steps"], start=1):
        steps.append(f"{index} {step['title']}\n{step['content']}")

    return f"""
请生成一张竖版 2:3 的中文抖音美食图文海报，主题是：{bundle['dish_name']}。

整体设计模板必须采用成熟爆款菜谱海报风，版式和气质沿用当前满意版本：顶部双层标题区，中间主菜成品图，左侧“2人份食材”卡，右侧“成败关键”卡，下半部分“5步出锅”步骤条，底部是一整条收藏与关注横条。不要极简，不要杂志风，不要大面积留白，必须是信息很多但一眼就想收藏的高密度爆款图文模板。

标题要求：
上方引导句必须是：{bundle['guide_line']}
主标题必须直接使用：{bundle['dish_name']}
主标题下方黄条卖点必须是：{bundle['subtitle']}
收藏提示细条内容必须是：{bundle['collection_hint']}

主菜成品图要求：
器皿与摆盘必须是：{bundle['plate']}
主画面主体必须是：{bundle['main_food']}
酱汁或汤汁状态必须是：{bundle['sauce']}
质感重点必须是：{bundle['texture']}
色彩点缀必须是：{bundle['colors']}
画面中必须出现一双木筷从画面侧上方夹起一块主菜悬在半空，带轻微挂汁与热气，只保留筷子和主菜动作，不要人物脸部，不要手部特写。
整体必须像 iPhone 17 Pro Max 拍摄的真实热门美食封面，有热气、亮泽、挂汁和食欲感，不要塑料假食物，不要静止僵硬摆盘。

左侧“2人份食材”卡内容必须清楚排版：
2人份食材

主料
{main_ingredients}

香料
{spices}

调味料
{seasonings}

右侧“成败关键”卡内容必须是 5 条无标点短句：
成败关键
{tips}

下半部分“5步出锅”步骤条内容必须是：
5步出锅
{'\n'.join(steps)}

底部收藏关注横条内容必须是：
{bundle['collection_copy']}
{bundle['ad_copy']}

整体配色固定为暖奶白、橙红、金黄、焦糖棕，背景可有木桌、香料、小食材和暖色虚化氛围。所有中文必须正确、自然、工整、无乱码、无错字。

强负面要求：
不要极简风
不要杂志留白风
不要清淡排版
不要设计练习稿感
不要英文
不要乱码
不要错字
不要人物脸部
不要手部特写
不要黑金厚重风
不要模块贴边
不要塑料食物质感
不要静止无动作的摆盘
不要没有筷子夹菜动作
不要过度饱和
不要无意义装饰
""".strip()


def load_prompt(prompt_file: Path) -> str:
    if not prompt_file.exists():
        raise FileNotFoundError(f"未找到调试 prompt 文件：{prompt_file}")

    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"调试 prompt 文件为空：{prompt_file}")

    return prompt


def load_dish_idea(idea_file: Path) -> dict[str, str]:
    if not idea_file.exists():
        raise FileNotFoundError(f"未找到菜品创意文件：{idea_file}")

    raw_lines = idea_file.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise ValueError(f"菜品创意文件为空：{idea_file}")

    dish_idea = raw_lines[0].strip()
    if not dish_idea:
        raise ValueError(f"菜品创意文件第一行为空：{idea_file}")

    notes = "\n".join(line.strip() for line in raw_lines[1:] if line.strip())
    return {
        "dish_idea": dish_idea,
        "notes": notes,
    }


def load_text_variable(text_file: Path, variable_name: str) -> str:
    if not text_file.exists():
        raise FileNotFoundError(f"未找到变量文件 {variable_name}：{text_file}")

    value = text_file.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"变量文件 {variable_name} 为空：{text_file}")

    return value


def render_prompt_template(prompt: str) -> str:
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    return prompt.replace("{{GUANGGAOYU}}", ad_copy)


def sanitize_file_name(name: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return sanitized or "生成图片"


def get_text_model() -> str:
    return os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


def get_text_fallback_model() -> str:
    return os.getenv("OPENAI_TEXT_FALLBACK_MODEL", "gpt-4.1-nano").strip() or "gpt-4.1-nano"


def get_text_max_output_tokens(stage_name: str) -> int:
    if stage_name == "创意菜谱":
        return 1400
    return 2600


def get_text_request_timeout_seconds() -> float:
    timeout_text = os.getenv("OPENAI_TEXT_REQUEST_TIMEOUT_SECONDS", "120").strip() or "120"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_TEXT_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def get_image_request_timeout_seconds() -> float:
    timeout_text = os.getenv("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", "900").strip() or "900"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def get_image_settings() -> dict[str, Any]:
    return {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "size": os.getenv("OPENAI_IMAGE_SIZE", "1024x1536").strip() or "1024x1536",
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high",
        "image_count": int(os.getenv("OPENAI_IMAGE_COUNT", "2").strip() or "2"),
    }


def extract_text_output(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text and output_text.strip():
        return output_text.strip()

    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {}

    text_parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text") or ""
            if text:
                text_parts.append(text)

    return "\n".join(text_parts).strip()


def extract_chat_text_output(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    text_parts: list[str] = []
    for item in content or []:
        text = getattr(item, "text", None) or item.get("text") or ""
        if text:
            text_parts.append(text)

    return "\n".join(text_parts).strip()


def request_text_generation(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    stage_name: str,
) -> dict[str, str]:
    text_model = get_text_model()
    fallback_model = get_text_fallback_model()
    request_timeout = get_text_request_timeout_seconds()
    max_output_tokens = get_text_max_output_tokens(stage_name)
    candidate_models = [text_model]
    if fallback_model != text_model:
        candidate_models.append(fallback_model)

    response = None
    used_model = text_model
    for model_index, current_model in enumerate(candidate_models, start=1):
        used_model = current_model
        if model_index == 1:
            print(f"正在生成{stage_name}，调用文本模型：{current_model}")
        else:
            print(f"{stage_name}超时后切换备用文本模型：{current_model}")

        try:
            response = client.chat.completions.create(
                model=current_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=request_timeout,
                max_tokens=max_output_tokens,
            )
            break
        except Exception as exc:
            if not is_timeout_error(exc):
                raise
            if model_index < len(candidate_models):
                continue
            raise

    content = extract_chat_text_output(response)
    if not content:
        raise RuntimeError(f"{stage_name}阶段未获得有效文本输出。")

    return {
        "model": used_model,
        "content": content,
    }


def extract_image_items(response: Any) -> list[dict[str, str]]:
    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {"data": getattr(response, "data", [])}

    image_items: list[dict[str, str]] = []
    for item in payload.get("data", []):
        if isinstance(item, dict):
            image_base64 = item.get("b64_json") or item.get("result") or ""
            revised_prompt = item.get("revised_prompt") or ""
        else:
            image_base64 = getattr(item, "b64_json", None) or getattr(item, "result", None) or ""
            revised_prompt = getattr(item, "revised_prompt", None) or ""

        if image_base64:
            image_items.append(
                {
                    "image_base64": image_base64,
                    "revised_prompt": revised_prompt,
                }
            )

    return image_items


def save_generated_images(
    image_items: list[dict[str, str]],
    dish_name: str,
    output_dir: Path,
    timestamp: str,
    revised_prompt_output_dir: Path | None = None,
    revised_prompt_stem: str | None = None,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_file_name(dish_name)
    saved_files: list[str] = []

    for index, item in enumerate(image_items, start=1):
        image_file = output_dir / f"{timestamp}_{safe_name}_{index:02d}.png"
        image_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_files.append(str(image_file))

        revised_prompt = item["revised_prompt"].strip()
        if revised_prompt:
            prompt_dir = revised_prompt_output_dir or output_dir
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_stem = revised_prompt_stem or f"{timestamp}_{safe_name}"
            revised_prompt_file = prompt_dir / f"{prompt_stem}_{index:02d}_revised_prompt.txt"
            revised_prompt_file.write_text(revised_prompt, encoding="utf-8")

    return saved_files


def save_text_output(
    content: str,
    output_dir: Path,
    timestamp: str,
    base_name: str,
    suffix: str,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{timestamp}_{sanitize_file_name(base_name)}{suffix}.txt"
    file_path.write_text(content.strip() + "\n", encoding="utf-8")
    return str(file_path)


def replace_or_insert_prefixed_line(
    text: str,
    prefix: str,
    replacement_line: str,
    insert_after_prefix: str | None = None,
) -> str:
    lines = text.splitlines()

    for index, raw_line in enumerate(lines):
        if raw_line.strip().startswith(prefix):
            lines[index] = replacement_line
            return "\n".join(lines)

    if insert_after_prefix:
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(insert_after_prefix):
                lines.insert(index + 1, replacement_line)
                return "\n".join(lines)

    lines.append(replacement_line)
    return "\n".join(lines)


def normalize_recipe_text(recipe_text: str, fixed_dish_name: str, ad_copy: str, notes: str = "") -> str:
    normalized_text = recipe_text.strip()
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="最终菜名：",
        replacement_line=f"最终菜名：{fixed_dish_name}",
        insert_after_prefix="创意来源：",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="收藏提示：",
        replacement_line=f"收藏提示：{DEFAULT_COLLECTION_HINT}",
        insert_after_prefix="副标题：",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="动态动作：",
        replacement_line=f"动态动作：{DEFAULT_DYNAMIC_ACTION}",
        insert_after_prefix="质感重点：",
    )
    if notes.strip():
        normalized_text = replace_or_insert_prefixed_line(
            text=normalized_text,
            prefix="器皿与摆盘：",
            replacement_line=f"器皿与摆盘：{infer_plate_description(notes)}",
            insert_after_prefix="【主画面说明】",
        )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="收藏文案：",
        replacement_line=f"收藏文案：{DEFAULT_COLLECTION_COPY}",
        insert_after_prefix="【底部文案】",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="关注文案：",
        replacement_line=f"关注文案：{ad_copy}",
        insert_after_prefix="收藏文案：",
    )
    return normalized_text.strip()


def generate_images_from_prompt_text(
    client: OpenAI,
    dish_name: str,
    prompt: str,
    timestamp: str,
) -> dict[str, Any]:
    image_settings = get_image_settings()
    request_timeout = get_image_request_timeout_seconds()

    print(f"正在调用模型：{image_settings['model']}")
    print(
        f"生成尺寸：{image_settings['size']}，质量：{image_settings['quality']}，数量：{image_settings['image_count']}"
    )
    response = None
    for attempt in range(1, DEFAULT_REQUEST_RETRY_COUNT + 1):
        try:
            response = client.images.generate(
                model=image_settings["model"],
                prompt=prompt,
                n=image_settings["image_count"],
                size=image_settings["size"],
                quality=image_settings["quality"],
                timeout=request_timeout,
            )
            break
        except Exception as exc:
            if attempt >= DEFAULT_REQUEST_RETRY_COUNT or not is_timeout_error(exc):
                raise
            print(f"图片请求超时，正在重试第 {attempt + 1}/{DEFAULT_REQUEST_RETRY_COUNT} 次...")

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("接口已返回响应，但未发现可保存的图片数据。")

    print("图片接口已返回，正在保存文件...")
    prompt_stem = f"{timestamp}_{sanitize_file_name(dish_name)}"
    saved_files = save_generated_images(
        image_items=image_items,
        dish_name=dish_name,
        output_dir=IMAGE_OUTPUT_DIR,
        timestamp=timestamp,
        revised_prompt_output_dir=PROMPT_OUTPUT_DIR,
        revised_prompt_stem=prompt_stem,
    )

    return {
        "model": image_settings["model"],
        "size": image_settings["size"],
        "quality": image_settings["quality"],
        "image_count": image_settings["image_count"],
        "saved_files": saved_files,
    }


def generate_recipe_assets_from_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, Any]:
    idea_file = ROOT_DIR / idea_file_name
    idea_payload = load_dish_idea(idea_file)
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    style_reference = render_prompt_template(load_prompt(DEFAULT_PROMPT_FILE))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"正在读取创意文件：{idea_file}")
    print(f"本次菜品创意：{idea_payload['dish_idea']}")
    if idea_payload["notes"]:
        print(f"补充说明：{idea_payload['notes']}")

    client = build_client()
    fallback_bundle: dict[str, Any] | None = None

    try:
        recipe_result = request_text_generation(
            client=client,
            system_prompt=build_recipe_system_prompt(
                ad_copy=ad_copy,
                fixed_dish_name=idea_payload["dish_idea"],
            ),
            user_prompt=build_recipe_user_prompt(
                dish_idea=idea_payload["dish_idea"],
                notes=idea_payload["notes"],
            ),
            stage_name="创意菜谱",
        )
        recipe_text = normalize_recipe_text(
            recipe_text=recipe_result["content"],
            fixed_dish_name=idea_payload["dish_idea"],
            ad_copy=ad_copy,
            notes=idea_payload["notes"],
        )
    except Exception as exc:
        if not is_timeout_error(exc):
            raise
        fallback_bundle = build_local_recipe_bundle(
            dish_name=idea_payload["dish_idea"],
            notes=idea_payload["notes"],
            ad_copy=ad_copy,
        )
        recipe_text = render_recipe_bundle_text(fallback_bundle)
        recipe_result = {
            "model": "local-timeout-fallback",
            "content": recipe_text,
        }
        print("创意菜谱文本接口超时，已切换为本地模板兜底。")

    generated_dish_name = idea_payload["dish_idea"]
    creative_file = save_text_output(
        content=recipe_text,
        output_dir=CREATIVE_OUTPUT_DIR,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix="_一页菜谱",
    )
    print(f"创意菜谱已保存：{creative_file}")

    try:
        prompt_result = request_text_generation(
            client=client,
            system_prompt=build_prompt_system_prompt(
                style_reference=style_reference,
                fixed_dish_name=generated_dish_name,
            ),
            user_prompt=build_prompt_user_prompt(
                recipe_text=recipe_text,
                fixed_dish_name=generated_dish_name,
            ),
            stage_name="文生图prompt",
        )
        image_prompt = prompt_result["content"]
    except Exception as exc:
        if not is_timeout_error(exc):
            raise
        if fallback_bundle is None:
            fallback_bundle = build_local_recipe_bundle(
                dish_name=generated_dish_name,
                notes=idea_payload["notes"],
                ad_copy=ad_copy,
            )
        image_prompt = build_local_image_prompt(fallback_bundle)
        prompt_result = {
            "model": "local-timeout-fallback",
            "content": image_prompt,
        }
        print("文生图 prompt 文本接口超时，已切换为本地模板兜底。")

    prompt_file = save_text_output(
        content=image_prompt,
        output_dir=PROMPT_OUTPUT_DIR,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix="_文生图prompt",
    )
    print(f"文生图 prompt 已保存：{prompt_file}")

    image_result = generate_images_from_prompt_text(
        client=client,
        dish_name=generated_dish_name,
        prompt=image_prompt,
        timestamp=timestamp,
    )

    return {
        "dish_idea": idea_payload["dish_idea"],
        "dish_name": generated_dish_name,
        "notes": idea_payload["notes"],
        "text_model": recipe_result["model"],
        "prompt_model": prompt_result["model"],
        "image_model": image_result["model"],
        "creative_file": creative_file,
        "prompt_file": prompt_file,
        "output_root": str(OUTPUT_ROOT_DIR),
        "creative_output_dir": str(CREATIVE_OUTPUT_DIR),
        "prompt_output_dir": str(PROMPT_OUTPUT_DIR),
        "image_output_dir": str(IMAGE_OUTPUT_DIR),
        "saved_files": image_result["saved_files"],
        "timestamp": timestamp,
    }


def generate_images_from_prompt_file(
    dish_name: str,
    prompt_file_name: str = "临时调试prompt.txt",
) -> dict[str, Any]:
    prompt_file = ROOT_DIR / prompt_file_name
    prompt_template = load_prompt(prompt_file)
    prompt = render_prompt_template(prompt_template)

    client = build_client()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_result = generate_images_from_prompt_text(
        client=client,
        dish_name=dish_name,
        prompt=prompt,
        timestamp=timestamp,
    )

    rendered_prompt_file = PROMPT_OUTPUT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}_原始prompt.txt"
    PROMPT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered_prompt_file.write_text(prompt, encoding="utf-8")

    return {
        "model": image_result["model"],
        "size": image_result["size"],
        "quality": image_result["quality"],
        "output_dir": str(IMAGE_OUTPUT_DIR),
        "saved_files": image_result["saved_files"],
        "rendered_prompt_file": str(rendered_prompt_file),
        "timestamp": timestamp,
    }


if __name__ == "__main__":
    result = generate_recipe_assets_from_idea_file()
    print("模块直接运行完成")
    print(f"输出根目录：{result['output_root']}")
    print(f"创意菜谱：{result['creative_file']}")
    print(f"文生图 prompt：{result['prompt_file']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")
