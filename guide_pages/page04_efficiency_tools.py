from __future__ import annotations

from typing import Any

from .shared import GuidePageDefinition, contains_any


PAGE_DEFINITION = GuidePageDefinition(
    page_number=4,
    page_name="省时技巧与工具",
    file_label="图解04_省时技巧与工具",
    reading_goal="让用户不只是会做这一道菜，还能学到更省时间、更顺手的实战方法。",
    title_direction="标题要像“这样做更省时间”这种明显帮用户省事的收益句。",
    subtitle_direction="副标题要同时覆盖工具选择和流程提效。",
    content_requirements=(
        "至少给出 2 个能立刻省时间的实操技巧，而不是空话。",
        "说明这道菜更适合什么锅具、刀具或辅助工具，以及为什么。",
        "如果存在切配顺序、控水、提前处理、摆盘出菜等更顺手的方法，要讲出来。",
    ),
    layout_requirements=(
        "中部做成 3 张功能卡，分别对应提效 工具 出菜顺序。",
        "工具卡里要有锅具或刀具的明确视觉，不要只写字没有物。",
        "整张图要让用户感觉这页学完后做菜会更快 更顺 更少出错。",
    ),
    visual_focus="高压锅 平底锅 宽铲这类能显著改变效率和稳定性的工具与处理动作。",
    negative_constraints=(
        "不要变成泛用厨房用品广告",
        "不要重复首图完整菜谱",
        "不要只有文字没有工具视觉",
        "不要堆太多器具导致信息混乱",
        "不要出现英文和乱码",
    ),
)


def build_local_page_text(bundle: dict[str, Any]) -> str:
    dish_name = bundle.get("dish_name", "这道菜")
    notes = bundle.get("notes", "")
    main_items = list(bundle.get("main_ingredients", []))
    first_name = main_items[0][0] if len(main_items) > 0 else "主料"
    second_name = main_items[1][0] if len(main_items) > 1 else "配料"
    pot_advice = "有高压锅就先用高压锅" if contains_any(notes, ["高压锅", "压30分钟", "软糯"]) else "能稳控火的小锅更合适"

    return f"""
【页面信息】
页码：04/06
页面名称：省时技巧与工具
页面标题：这样做更省时间
页面副标题：工具选对 顺手度会高很多
阅读收益：少走弯路 少洗锅 还更容易稳定做出 {dish_name}

【内容卡1】
标题：先把时间省下来
1. 需要软化或炖煮的原料先做预处理 熟得更快 口感也更稳
2. {first_name} 先按烹饪需求处理好 下锅时更完整 不容易翻车
3. {second_name} 和调味先备在一碗里 下锅时不会手忙脚乱

【内容卡2】
标题：这道菜更适合什么锅
1. {pot_advice} 能明显缩短长时处理步骤
2. 需要煎炒上色时更适合大底平锅或不粘锅 受热更均匀
3. 宽铲比尖铲更稳 翻主料时不容易断边碎块

【内容卡3】
标题：出菜顺序这样更顺
1. 先做底味层 再处理主料上色 最后合味收汁
2. 勾芡和葱花都放在最后 口感会更干净也更亮
3. 装盘前先把盘子备好 出锅就上桌 热气和卖相都更完整

【页尾提示】
做菜不只拼手法 顺手的流程和工具 往往才是让家常菜更稳的隐藏加分项
""".strip()
