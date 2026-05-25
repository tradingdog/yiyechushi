from __future__ import annotations

from typing import Any

from .shared import GuidePageDefinition, find_ingredient_amount


PAGE_DEFINITION = GuidePageDefinition(
    page_number=2,
    page_name="食材怎么挑",
    file_label="图解02_食材怎么挑",
    reading_goal="先帮用户把最影响口感的核心食材买对，降低第一次就买错的概率。",
    title_direction="标题要像“豆腐和肉沫这样挑”这种一眼能看懂的买菜收益句。",
    subtitle_direction="副标题要突出买对原料比多放调味更重要。",
    content_requirements=(
        "优先讲 2 到 3 个最影响成菜口感的核心食材，说明推荐品类、部位或状态。",
        "每种核心食材都要写出好坏判断标准，并明确买错会带来什么问题。",
        "如果这道菜存在肥瘦比、老嫩度、新旧豆、含水量之类的关键差异，要明确写出来。",
    ),
    layout_requirements=(
        "顶部是强收益标题和一句副标题，中部必须是 3 张选择卡，底部是一条经验总结。",
        "3 张选择卡分别对应 3 种最重要的核心食材，每张卡都要有微距写实食材视觉。",
        "整张图要像会被截图去买菜的经验卡，而不是泛泛的科普海报。",
    ),
    visual_focus="核心主料的切面纹理、新鲜度状态与颗粒层次这类能看出好坏的食材近景。",
    negative_constraints=(
        "不要重复完整配方表",
        "不要完整复述 5 步做法",
        "不要做成普通超市海报",
        "不要出现过多无关食材",
        "不要出现英文和乱码",
    ),
)


def build_local_page_text(bundle: dict[str, Any]) -> str:
    dish_name = bundle.get("dish_name", "这道菜")
    main_items = list(bundle.get("main_ingredients", []))

    if len(main_items) < 3:
        main_items.extend([
            ("主料A", "300g"),
            ("主料B", "200g"),
            ("主料C", "150g"),
        ])

    first_name, first_amount = main_items[0]
    second_name, second_amount = main_items[1]
    third_name, third_amount = main_items[2]

    sauce_hint = bundle.get("sauce", "酱汁要能挂住主料")
    texture_hint = bundle.get("texture", "口感层次要清楚")

    return f"""
【页面信息】
页码：02/06
页面名称：食材怎么挑
页面标题：先把食材买对
页面副标题：核心食材这样挑才稳
阅读收益：买对原料 这道菜已经成功一半

【内容卡1】
标题：{first_name}先看这三点
1. 优先选状态新鲜的 {first_name} {first_amount}
2. 观察色泽和组织是否自然 不要选久放发蔫或出水明显的
3. 这一项选错最容易影响 {dish_name} 的主体口感和稳定度

【内容卡2】
标题：{second_name}别只看便宜
1. 建议按菜谱准备 {second_name} {second_amount}
2. 优先选处理状态稳定的原料 过老过嫩都会影响火候窗口
3. 与主料搭配时要能撑住 {texture_hint}

【内容卡3】
标题：{third_name}决定收口
1. 建议按菜谱准备 {third_name} {third_amount}
2. 关注新鲜度和含水状态 太旧或状态不稳会拉低风味表现
3. 这一项会直接影响最后的 {sauce_hint}

【页尾提示】
这道菜真正拉开差距的第一步 不是调料多少 而是核心原料先别买错
""".strip()
