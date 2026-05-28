from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuidePageDefinition:
    page_number: int
    page_name: str
    file_label: str
    reading_goal: str
    title_direction: str
    subtitle_direction: str
    content_requirements: tuple[str, ...]
    layout_requirements: tuple[str, ...]
    visual_focus: str
    negative_constraints: tuple[str, ...]


def build_iphone_food_photo_requirement(subject_scope: str) -> str:
    return (
        f"{subject_scope}只要出现任何可见的菜品、食材、半成品、局部特写、汤汁、配料、背景食物，"
        "都必须像 iPhone 主摄 1x 默认相机直接拍出来的真实照片，非人像模式，"
        "以 30 到 45 度轻微俯拍或贴近桌面的自然取景为主，颜色偏暖但不要过度饱和，"
        "不要高对比，不要过度锐化，不要商业棚拍补光，不要电商主图感，不要 3D 渲染假光。"
        "食材大小、切面、熟度、煎色、汁水、摆放和盘边残留允许自然随机差异，"
        "切配要像家庭厨房的人手处理：有大有小、有长有短、厚薄不一，不要工厂化毫米级统一切割，"
        "不要整齐复制，不要塑料模型感。"
    )


def build_unbranded_prop_requirement(subject_scope: str) -> str:
    return (
        f"{subject_scope}只要出现器皿、调料、酱料、瓶罐、包装、厨房工具、小家电、袋装食材或任何辅助道具，"
        "都必须做成无品牌、无商标、无 logo、无可读标签、无贴纸文案的通用素面版本。"
        "不要出现超市包装、品牌瓶身、带字酱料罐、酒标、调料袋、印字餐具、假英文、假汉字或任何像真实商品却明显是 AI 乱造的商业包装。"
        "如果需要表现酱料、酒液、调味品或半成品，优先用无字玻璃瓶、素色陶碗、小碟、量杯或直接裸露食材状态呈现。"
    )


def build_adaptive_food_interaction_requirement(subject_scope: str, require_interaction: bool = False) -> str:
    lead_sentence = (
        f"{subject_scope}必须安排至少一组与主菜真实互动的餐具或上桌工具，不能把所有菜都固定成同一双筷子夹起主菜的模板。"
        if require_interaction
        else f"如果{subject_scope}里出现主菜成品、主菜局部、进食瞬间或与主菜互动的镜头，餐具和动作必须按这道菜本身的吃法、上桌逻辑和食材结构自行判断。"
    )
    return (
        lead_sentence
        + "餐具类型不要写死成筷子，汤、羹、煲类可用勺子、汤匙、长柄勺或勺配筷；牛排、排类、整块肉或需要切分的菜可用刀叉；面、粉、长条食材可用筷子、叉子、夹子或卷起动作；铁板、锅物、焗烤、蒸物或共享菜可用铲勺、夹子、锅勺、分餐勺等更合理的组合。"
        + "餐具数量不写死，是否一件或多件由菜品动作自然决定；手部也不写死，是否入镜、入镜多少和左右手如何分工，都按菜品和动作自然决定。"
        + "互动瞬间可以是舀汤、切开、夹起、叉起、捞起、翻面、撕开、分勺、蘸汁、卷起、拨散、盛入碗中、送到口边或刚上桌整理的一瞬，不要每道菜都拍成同一种悬空夹菜模板。"
        + "如果需要手部，允许少量真实手部自然入镜，男女都可以，以生活化抓拍为准；但不要人物脸部，不要完整手臂，不要手部特写抢戏。"
        + "不要求默认出现明显滴汁、挂汁或大片蒸汽，只有菜本身确实适合时才允许自然出现。"
    )


def build_strict_centered_title_requirement(title_scope: str) -> str:
    return (
        f"{title_scope}必须严格以整张海报的中轴线做水平居中排版，"
        "标题区的视觉中心、主标题文字中心点和副标题文字中心点都要落在画面正中线上，"
        "左右留白必须对称，不要偏左，不要偏右，不要故意错位，不要斜排。"
    )


def build_unified_follow_strip_requirement(ad_copy: str) -> str:
    return (
        f"页面底部保留一条与首图统一视觉语言的细长关注横条或页脚文案，文字必须直接使用：{ad_copy}。"
        "不需要生成任何图标，不要做成夸张大按钮，不要破坏当前延续页的信息结构和阅读节奏。"
    )


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def find_ingredient_amount(bundle: dict[str, Any], *ingredient_names: str) -> str:
    groups = [
        bundle.get("main_ingredients", []),
        bundle.get("spices", []),
        bundle.get("seasonings", []),
    ]

    for items in groups:
        for name, amount in items:
            if name in ingredient_names:
                return amount
    return ""


def format_page_progress(page_definition: GuidePageDefinition) -> str:
    return f"{page_definition.page_number:02d}/06"


def format_page_output_name(dish_name: str, page_definition: GuidePageDefinition) -> str:
    return f"{dish_name}_{page_definition.file_label}"


GUIDE_PAGE_MACHINE_LABELS: tuple[str, ...] = (
    "当前菜名",
    "当前页面",
    "页面信息",
    "页面名称",
    "页面标题",
    "页面副标题",
    "内容卡1",
    "内容卡2",
    "内容卡3",
    "页尾提示",
)


def extract_prefixed_line_value(text: str, field_name: str) -> str:
    match = re.search(rf"^{re.escape(field_name)}：(.+)$", text, flags=re.M)
    if not match:
        return ""
    return match.group(1).strip()


def extract_named_block(text: str, block_name: str) -> str:
    pattern = rf"【{re.escape(block_name)}】\s*(.*?)(?=\n【|\Z)"
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def extract_numbered_items(block_text: str) -> list[str]:
    items: list[str] = []
    for line in block_text.splitlines():
        normalized_line = line.strip()
        match = re.match(r"^\d+\.\s*(.+)$", normalized_line)
        if match:
            items.append(match.group(1).strip())
    return items


def build_guide_page_display_text_brief(page_text: str, include_machine_label_examples: bool = True) -> str:
    page_progress = extract_prefixed_line_value(page_text, "页码")
    page_title = extract_prefixed_line_value(page_text, "页面标题")
    page_subtitle = extract_prefixed_line_value(page_text, "页面副标题")
    reading_benefit = extract_prefixed_line_value(page_text, "阅读收益")
    footer_tip = extract_named_block(page_text, "页尾提示")

    card_descriptions: list[str] = []
    for card_index in range(1, 4):
        card_block = extract_named_block(page_text, f"内容卡{card_index}")
        if not card_block:
            continue

        card_title = extract_prefixed_line_value(card_block, "标题")
        card_items = extract_numbered_items(card_block)
        card_parts: list[str] = []
        if card_title:
            card_parts.append(f"标题“{card_title}”")
        if card_items:
            joined_items = "；".join(f"“{item}”" for item in card_items)
            card_parts.append(f"正文要点 {joined_items}")
        if card_parts:
            card_descriptions.append(f"第{card_index}张内容卡只展示 {'，'.join(card_parts)}")

    lines = [
        "下面这些才是页面里允许直接展示给用户看的实际文字内容，只能渲染这些内容值本身，不要把说明字段、占位字段或程序标签画进画面。",
    ]
    if page_progress:
        lines.append(f"左上角页码只显示“{page_progress}”。")
    if page_title:
        if include_machine_label_examples:
            lines.append(f"页面主标题只显示“{page_title}”。")
        else:
            lines.append(f"顶部大标题只显示“{page_title}”。")
    if page_subtitle:
        if include_machine_label_examples:
            lines.append(f"页面副标题只显示“{page_subtitle}”。")
        else:
            lines.append(f"标题下方的小标题只显示“{page_subtitle}”。")
    if reading_benefit:
        lines.append(f"阅读收益区只显示“{reading_benefit}”。")
    lines.extend(card_descriptions)
    if footer_tip:
        if include_machine_label_examples:
            lines.append(f"页尾提示只显示“{footer_tip}”。")
        else:
            lines.append(f"底部经验句只显示“{footer_tip}”。")
    if include_machine_label_examples:
        lines.append(
            "绝对不要出现这些程序化字段标签：当前菜名、当前页面、页面信息、页面名称、页面标题、页面副标题、内容卡1、内容卡2、内容卡3、页尾提示。"
        )
    else:
        lines.append("绝对不要出现任何程序化字段标签、后台说明词、占位字段名或模板提示词。")
    return "\n".join(lines)


def build_guide_page_text_system_prompt(
    page_definition: GuidePageDefinition,
    fixed_dish_name: str,
) -> str:
    content_requirements = "\n".join(
        f"{index}. {item}" for index, item in enumerate(page_definition.content_requirements, start=1)
    )

    return f"""
你是阿叶造新菜账号的多图解编辑，负责把一张已经定稿的一页菜谱，拆成一组更有收藏价值的补充图解。

当前要写的是第 {format_page_progress(page_definition)} 张，页面名称是：{page_definition.page_name}。

这不是首图。首图已经完成了完整菜谱，所以你这张图的职责不是重复完整配方和完整步骤，而是补充用户最想知道、首图又放不下的隐性经验。

用户当前看到这张图时，菜名已经明确为：{fixed_dish_name}
这张图的阅读目标是：{page_definition.reading_goal}

标题与文案要求：
1. 页面标题必须短、直接、有收益感，不要把菜名再完整重复一遍。
2. 页面副标题要进一步解释这张图能帮用户解决什么问题，长度控制在 18 个汉字以内。
3. 全部内容必须是中文，适合做成高密度但易读的抖音图文卡片。
4. 每张内容卡只写最重要的 2 到 3 条，不要写成长段教程。
5. 禁止把首图里已经有的完整食材表和完整 5 步再抄一遍。
6. 所有表达都要像真正有做菜经验的人在给用户减坑，而不是空泛地讲道理。

这张图必须重点覆盖以下内容：
{content_requirements}

输出必须严格遵循这个纯文本结构，不要写解释，不要写 Markdown 代码块：

【页面信息】
页码：{format_page_progress(page_definition)}
页面名称：{page_definition.page_name}
页面标题：...
页面副标题：...
阅读收益：...

【内容卡1】
标题：...
1. ...
2. ...
3. ...

【内容卡2】
标题：...
1. ...
2. ...
3. ...

【内容卡3】
标题：...
1. ...
2. ...
3. ...

【页尾提示】
...

额外要求：
1. 页面标题方向：{page_definition.title_direction}
2. 页面副标题方向：{page_definition.subtitle_direction}
3. 页尾提示必须像一句能让人记住的经验话，不要写泛泛 CTA。
""".strip()


def build_guide_page_text_user_prompt(
    page_definition: GuidePageDefinition,
    recipe_text: str,
    notes: str,
) -> str:
    note_text = notes or "无额外补充说明，请基于菜谱本身补全。"
    return f"""
请根据下面已经定稿的一页菜谱文字，写出第 {format_page_progress(page_definition)} 张补充图解文案。

注意：
1. 这一张图只补充 {page_definition.page_name} 相关经验。
2. 用户希望这一张图读完后会觉得更有用、更愿意继续往下滑，而不是觉得内容重复。
3. 不要把首图完整内容重复一遍。

用户补充说明：
{note_text}

菜谱全文：
{recipe_text}
""".strip()


def build_guide_page_image_system_prompt(
    page_definition: GuidePageDefinition,
    style_reference: str,
    fixed_dish_name: str,
    ad_copy: str,
) -> str:
    layout_requirements = "\n".join(
        f"{index}. {item}" for index, item in enumerate(page_definition.layout_requirements, start=1)
    )
    negative_constraints = "\n".join(
        f"{index}. {item}" for index, item in enumerate(page_definition.negative_constraints, start=1)
    )

    return f"""
你是阿叶造新菜账号的多图解 prompt 导演。你的任务是把一页多图解中的第 {format_page_progress(page_definition)} 张文案，转换成一条给 gpt-image-2 使用的完整中文文生图 prompt。

这张图必须与首图和其他图保持同一套 VI：
1. 固定为竖版 2:3 中文抖音美食图文海报。
2. 整体沿用暖奶白、橙红、金黄、焦糖棕的成熟爆款美食视觉。
3. 字体层级、卡片边框、标题气质和信息密度都要和首图属于同一个系列，不能因为局部图片更真实就改变整张图的 VI、版式、字体、边框和整体色调。
4. 顶部标题区中的页面标题和页面副标题必须严格水平居中，视觉中心对齐整张海报中轴线；左上角页码可以单独存在，但不能把标题区挤偏。
5. 主标题仍然要有橙红或朱红刷字感和明显描边，但信息结构不再做成首图那种完整菜谱。
6. 左上角必须出现清晰的页码进度感，例如 {format_page_progress(page_definition)}，让用户知道自己在翻第几张。
7. 这张图必须明显围绕“{page_definition.page_name}”展开，不能像一张泛用教程图。

页面设计重点：
1. 本页涉及的菜固定为 {fixed_dish_name}；如果页面里需要出现菜名，只能直接写“{fixed_dish_name}”这 1 个菜名本身，不要出现“当前菜名”“本页菜名”这类说明标签。
2. 这张图的核心视觉焦点必须是：{page_definition.visual_focus}
3. 页面版式要优先满足以下要求：
{layout_requirements}
4. {build_unified_follow_strip_requirement(ad_copy)}

请优先继承下面这份当前满意版本的参考 prompt 的字体气质、配色、卡片风格和成熟爆款图文感，但不要把首图的完整食材卡、完整步骤条和底部 CTA 机械照搬：

---参考 prompt 开始---
{style_reference}
---参考 prompt 结束---

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要解释。
2. 必须把这张图文案里的标题、内容卡和页尾提示全部吸收到 prompt 里。
3. 必须强调这是一组多图解中的延续页，而不是单独海报。
4. {build_strict_centered_title_requirement('顶部标题区中的页面标题和副标题')}
5. {build_iphone_food_photo_requirement('卡片里、页面背景里、局部插图里和工具旁边')}
6. {build_unbranded_prop_requirement('卡片里、页面背景里、局部插图里和工具旁边')}
7. {build_adaptive_food_interaction_requirement('这张延续页的照片内容层', require_interaction=False)}
8. 这些真实拍摄要求只作用于页面中的照片内容层，不改变整张延续页的 VI、版式、字体、边框和整体配色。
9. {build_unified_follow_strip_requirement(ad_copy)}
10. 所有中文必须自然工整，不要英文，不要乱码，不要错字。
11. 画面里绝对不要出现“当前菜名”“当前页面”“页面名称”“页面标题”“页面副标题”“内容卡1”“内容卡2”“内容卡3”“页尾提示”这类程序化字段标签。
12. 必须保留这些负面约束：
{negative_constraints}
""".strip()


def build_guide_page_image_user_prompt(
    page_definition: GuidePageDefinition,
    page_text: str,
    fixed_dish_name: str,
    ad_copy: str,
) -> str:
    display_text_brief = build_guide_page_display_text_brief(page_text, include_machine_label_examples=True)
    return f"""
请根据下面这份第 {format_page_progress(page_definition)} 张图解文案，写出最终文生图 prompt。

这是一张围绕“{fixed_dish_name}”展开的 {page_definition.page_name} 延续页；如果页面里需要提到菜名，只能直接写“{fixed_dish_name}”，不要加任何字段标签。
这张图必须保持和首图同一套 VI，并明确呈现页码 {format_page_progress(page_definition)}。
{build_strict_centered_title_requirement('这张图顶部的页面标题和页面副标题')}
{build_iphone_food_photo_requirement('这张图中')}
{build_unbranded_prop_requirement('这张图中')}
{build_adaptive_food_interaction_requirement('这张图的照片内容层', require_interaction=False)}
{build_unified_follow_strip_requirement(ad_copy)}
不要把“当前菜名”“当前页面”“页面名称”“页面标题”“页面副标题”“内容卡1”“内容卡2”“内容卡3”“页尾提示”这类程序化字段标签写进画面。
不要改整张延续页的 VI、版式、字体、边框和整体色调。

{display_text_brief}
""".strip()


def build_local_guide_page_image_prompt(
    page_definition: GuidePageDefinition,
    fixed_dish_name: str,
    page_text: str,
    ad_copy: str,
) -> str:
    negative_constraints = "\n".join(page_definition.negative_constraints)
    display_text_brief = build_guide_page_display_text_brief(page_text, include_machine_label_examples=False)
    return f"""
请生成一张竖版 2:3 的中文抖音美食多图解海报，主题是 {fixed_dish_name} 的第 {format_page_progress(page_definition)} 张：{page_definition.page_name}。

整体 VI 必须与阿叶造新菜首图保持一致，沿用暖奶白、橙红、金黄、焦糖棕的成熟爆款图文风。左上角必须有明显的页码进度 {format_page_progress(page_definition)}，主标题使用红色描边刷字感中文标题。整体不是首图，不要重复完整菜谱，而是围绕 {page_definition.page_name} 做一张更有经验价值的延续页。

页面视觉重点必须是：{page_definition.visual_focus}
    {build_strict_centered_title_requirement('顶部标题区中的大标题和小标题')}
{build_iphone_food_photo_requirement('卡片中的食材近景、局部状态、工具画面和页面里任何可见食物内容')}
{build_unbranded_prop_requirement('卡片中的食材近景、局部状态、工具画面和页面里任何可见的器皿、调料、包装与辅助道具')}
{build_adaptive_food_interaction_requirement('这张延续页的照片内容层', require_interaction=False)}
{build_unified_follow_strip_requirement(ad_copy)}
但不要把整张延续页改成普通纪实照片，也不要改变原有 VI、版式、字体、边框和配色。
如果页面里需要出现菜名，只能直接写“{fixed_dish_name}”，不要加任何说明标签或模板字段名。

页面里允许真正显示给用户看的文字内容如下，必须自然拆成标题区、内容卡和底部经验句区域，只渲染这些内容值本身：
{display_text_brief}

所有中文必须正确、工整、无乱码。不要英文，不要把内容挤满到贴边，不要极简空白，不要变成另一套视觉系统。绝对不要出现任何程序化字段标签、后台说明词、占位字段名或模板提示词。

强负面要求：
{negative_constraints}
""".strip()
