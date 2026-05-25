from __future__ import annotations

from typing import Any

from .shared import GuidePageDefinition


PAGE_DEFINITION = GuidePageDefinition(
    page_number=5,
    page_name="平替方案",
    file_label="图解05_平替方案",
    reading_goal="让用户家里缺一两样东西时也能继续做，不会做到一半卡住。",
    title_direction="标题要像“缺料也能继续做”这种明确降低门槛的句子。",
    subtitle_direction="副标题要突出哪些能替 哪些不能乱替。",
    content_requirements=(
        "至少给出 2 到 3 类可执行的平替方案，优先写主料 调味和局部工具的平替。",
        "每条平替都要说明替完后的口感变化，而不是只说能换。",
        "要明确指出至少 1 个不建议乱替的关键点，帮用户避免做偏。",
    ),
    layout_requirements=(
        "中部做成 3 张平替卡，分别展示主料平替 调味平替 和不能乱替的底线。",
        "平替卡视觉要像对照关系，能一眼看懂原版和替代版的差异。",
        "底部要给一句帮助用户快速决策的结论。",
    ),
    visual_focus="食材和调味的一对一替代关系，像对照卡一样直观，而不是抽象说明文字。",
    negative_constraints=(
        "不要把所有东西都说成可以随便替",
        "不要给没有落地性的空泛建议",
        "不要重复完整做法",
        "不要出现超多复杂替代品导致更难买",
        "不要出现英文和乱码",
    ),
)


def build_local_page_text(bundle: dict[str, Any]) -> str:
    dish_name = bundle.get("dish_name", "这道菜")
    main_items = list(bundle.get("main_ingredients", []))
    first_name, first_amount = main_items[0] if len(main_items) > 0 else ("主料A", "300g")
    second_name, second_amount = main_items[1] if len(main_items) > 1 else ("主料B", "200g")

    seasonings = list(bundle.get("seasonings", []))
    seasoning_name = seasonings[0][0] if len(seasonings) > 0 else "核心调味"

    return f"""
【页面信息】
页码：05/06
页面名称：平替方案
页面标题：缺料也能继续做
页面副标题：能替的告诉你 不能乱替的也说清
阅读收益：家里少一两样也能把 {dish_name} 顺利做出来

【内容卡1】
标题：主料怎么平替
1. 没有 {first_name} {first_amount} 时 优先选择口感接近的同类原料替代
2. 没有 {second_name} {second_amount} 时 先保证结构功能一致 再考虑风味细节
3. 平替后火候和收汁时间要微调 不要完全照搬原时长

【内容卡2】
标题：调味怎么平替
1. 没有 {seasoning_name} 时 用同风味方向调味料分步替代 不要一次加太猛
2. 先保住咸鲜与香气主线 再去补颜色和层次
3. 每次替代后先小口试味 再决定是否继续加量

【内容卡3】
标题：这几个点别乱替
1. 决定菜名识别度的核心主料尽量别完全替掉
2. 决定口感骨架的关键工序尽量保留 只调轻重不全删
3. 收汁或挂汁步骤可以轻一点 但最好别完全省 不然容易散味

【页尾提示】
平替的核心不是一模一样 而是先保住这道菜最关键的口感骨架
""".strip()
