from __future__ import annotations

from typing import Any

from .shared import GuidePageDefinition


PAGE_DEFINITION = GuidePageDefinition(
    page_number=3,
    page_name="关键细节拆解",
    file_label="图解03_关键细节拆解",
    reading_goal="把最容易翻车的关键状态讲清楚，让用户知道做到什么程度才算真的对。",
    title_direction="标题要像“这三步做到位就稳了”这种直接承接用户焦虑的句子。",
    subtitle_direction="副标题要突出状态判断而不是动作名称。",
    content_requirements=(
        "只选 3 个最影响成败的关键细节，优先解释状态判断标准。",
        "每个细节都要写出做到位的表现，以及没做到位会出现什么问题。",
        "禁止泛泛写“炒香”“煮熟”，必须写得让普通人一看就知道什么叫对。",
    ),
    layout_requirements=(
        "中部必须是 3 张状态拆解卡，每张卡都像局部放大镜一样讲一个关键点。",
        "每张卡都要有对应的局部画面，例如焦边 豆粒糯化 挂汁浓度。",
        "整张图视觉上要像首图的延续页，但信息重心是细节判断，不是完整教程。",
    ),
    visual_focus="核心主料处理到位程度、关键上色状态、最后收汁浓度这类决定成败的局部特写。",
    negative_constraints=(
        "不要重复完整步骤条",
        "不要把所有细节都塞进去",
        "不要出现整盘大成品替代局部特写",
        "不要空泛写火候要够",
        "不要出现英文和乱码",
    ),
)


def build_local_page_text(bundle: dict[str, Any]) -> str:
    dish_name = bundle.get("dish_name", "这道菜")
    main_items = list(bundle.get("main_ingredients", []))
    first_name = main_items[0][0] if len(main_items) > 0 else "主料A"
    second_name = main_items[1][0] if len(main_items) > 1 else "主料B"
    texture_hint = bundle.get("texture", "层次清楚")

    return f"""
【页面信息】
页码：03/06
页面名称：关键细节拆解
页面标题：这三步做到位就稳
页面副标题：别只会照做 还要会看状态
阅读收益：看懂这三个状态 第一次做 {dish_name} 也更容易成功

【内容卡1】
标题：{first_name}做到什么程度
1. 正确状态是组织饱满且处理均匀 一眼能看出已经到位
2. 处理不到位 后续合炒或收汁时就很难稳定入味
3. 处理过头也会损失口感层次 影响整体完成度

【内容卡2】
标题：{second_name}火候怎么看
1. 颜色与状态达到微上色且结构稳定就够了
2. 太浅会缺香气和支撑感 太深容易发干发硬
3. 把握住中间窗口 最利于后续合味与收口

【内容卡3】
标题：最后汁收成什么样
1. 正确状态是酱汁薄薄挂住主料 不要变成一锅稀水
2. 勾芡太早 汁会闷住香气 口感发堵
3. 勾芡太重 整体会糊口 失去 {texture_hint}

【页尾提示】
会看状态 比死记步骤更重要 这才是让 {dish_name} 稳定好吃的关键
""".strip()
