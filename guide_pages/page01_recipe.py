from __future__ import annotations

from typing import Any

from guide_pages.shared import (
    build_adaptive_food_interaction_requirement,
    build_iphone_food_photo_requirement,
    build_strict_centered_title_requirement,
    build_unbranded_prop_requirement,
)


PAGE_NUMBER = 1
PAGE_NAME = "一页菜谱"
FILE_LABEL = "图解01_一页菜谱"


def build_page01_output_name(dish_name: str) -> str:
    return f"{dish_name}_{FILE_LABEL}"


def build_page01_final_priority_sentence() -> str:
    return "请优先保证首图上半部分是一张与标题融合的连续主菜背景照片，主菜照片和步骤照片都要像真实手机实拍，饱和度降低20%，再完成整张海报的文字和排版。"


def build_page01_real_photo_requirement() -> str:
    return """
首图从页面顶边到步骤条上缘的整个上半部分，都必须来自同一张连续的 iPhone 主摄 1x 默认相机直出主菜照片。
这不是“上方纯色标题底 + 下方独立主图”的两段式结构；顶部引导句、主标题和黄条卖点都要直接压在同一张主菜照片的上半部，照片继续向下延伸到菜品主体区域。
左右信息卡和其它文字模块也都是压在这张连续主菜照片上的版式层，不要把主菜照片装进单独矩形相框、圆角卡片、白边照片框、贴纸照片或任何额外边框容器里，也不要用横向分割把标题区和照片区切开。
主菜照片边缘要自然延伸到主体区域两侧和顶部，看起来像整张海报上半部分共用一张背景照片，而不是标题区单独一块底、照片区再单独一块底。
非人像模式，30 到 45 度轻微俯拍，中近景构图，整盘菜大部分区域都清晰，背景只有轻微自然虚化，不要奶油景深，不要电影感虚化。
光线像晚饭前在室内拍摄：暖色顶灯加侧面自然光或窗光，亮部正常不过曝，阴影保留细节，白平衡自然偏暖，不要霓虹橙红，不要商业棚拍补光，不要夸张轮廓光。
桌面材质、餐具类型和后景小物必须服从菜谱里的“器皿与摆盘”“桌面与环境”“背景陪衬”说明，可以根据菜本身改成石面、水磨石、深色木桌、亚麻餐垫、搪瓷托盘、锅盖、原料小碗或简洁厨房台面，但数量必须少且合理。
任何出现的调料瓶、酱料罐、小碟、器皿、包装或厨房小道具都必须是无品牌、无文字、无商标的通用款，不要标签贴纸，不要假品牌，不要像真实商品又明显是 AI 乱造的包装。
不要把每道菜都默认做成同一种木桌、暖色陶盘、木托和几只失焦小碗的固定模板，不要刻意摆满香料、辣椒、小道具和无意义配件。
""".strip()


def build_page01_dish_interaction_requirement() -> str:
    return build_adaptive_food_interaction_requirement("主画面里", require_interaction=True)


def build_page01_title_axis_requirement() -> str:
    return (
        "首图顶部标题区必须做成沿同一条画面正中竖线向下堆叠的中心柱布局，"
        "上方引导句、主标题和黄条卖点要一层压一层地居中排布，"
        "每一行文字块和黄条块的中心点都要落在同一条画面正中竖线上。"
        "不要把任何一行做成从左边起排的左对齐短条，不要主标题偏左后再用黄条补右，"
        "也不要额外再加一条收藏提示细条。"
    )


def build_page01_step_section_title(steps: list[dict[str, Any]]) -> str:
    step_count = max(3, min(len(steps), 5))
    return f"{step_count}步出锅"


def build_page01_prompt_system_prompt(style_reference: str, fixed_dish_name: str) -> str:
    return f"""
你是阿叶造新菜账号的海报 prompt 导演。你的任务是把一份已经完成的中文菜谱文案，转换成一条给 gpt-image-2 使用的完整中文文生图 prompt。

你必须锁定以下 VI 与版式，不要自由发挥成别的风格：
1. 竖版 2:3 中文抖音美食图文海报。
2. 整体是成熟爆款菜谱海报风，不是极简风，不是杂志风，不要大面积留白。
3. {build_page01_title_axis_requirement()}
4. 顶部是“引导句小于菜名”的双层标题结构，菜名是最大主标题，橙红或朱红手写刷字质感，带明显白边或高对比描边。
5. 标题下方要有一条高转化卖点副标题黄条，不要再额外放收藏提醒细条。
6. 从页面顶边到步骤条上缘的整个上半部分，必须由同一张连续主菜成品大图作为统一背景层铺开；顶部标题、黄条和左右卡片都直接压在这张照片上，不允许另做纯色标题底，也不允许把主菜照片做成单独带边框的照片卡片。
7. 左侧是“2人份食材”卡片，右侧是“成败关键”卡片。
8. 下半部分是 3 到 5 步的步骤条，具体步数按这道菜本身决定，越少越好，最多 5 步；步骤卡按实际步数横向排开，信息密度高但仍清楚。
9. 底部是一整条收藏与关注 CTA 横条，颜色醒目，像成熟爆款海报。
10. 主配色固定为暖奶白、橙红、金黄、焦糖棕，但器皿、桌面材质和后景陪衬必须跟随菜谱主画面说明变化，不允许所有菜都回到木桌、暖陶盘、木托这一套默认模板。
11. 所有中文必须自然工整，不要英文，不要乱码，不要错字。
12. 主标题必须直接使用“{fixed_dish_name}”这 1 个菜名，不能改字，不能扩写，不能另起新名。
13. 主画面必须安排与这道菜匹配的真实餐具和互动动作，餐具类型、数量、是否一只手或多只手入镜都按菜品自行判断，但必须形成明确动作感和食欲点，不要回到所有菜都固定用一双木筷的模板。
14. 主标题上方的引导句必须很短，控制在 12 个汉字以内。
15. 主标题下方黄条卖点必须精简成 2 到 3 个短卖点，总长度控制在 24 个汉字以内。
16. 严禁生成“上方暖奶白标题底 + 下方矩形主图”的两段式首图，也严禁用任何横向切割把标题区和主菜照片区拆开。

请优先继承下面这份当前满意版本的参考 prompt 的风格取向，但不要照抄其中具体菜名和配方，只复用它的版式、密度、配色、语气、卡片结构和负面约束：

---参考 prompt 开始---
{style_reference}
---参考 prompt 结束---

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要加解释。
2. 必须把用户菜谱中的标题、副标题、食材、成败关键、步骤内容和底部文案全部吸收到 prompt 里。
3. 左侧食材卡、右侧成败关键和实际步骤条里的每一条文字，都必须逐项继承用户菜谱原文；食材名、数量、顺序、短句标题和步骤内容都不能自行概括、换词或改写成通用模板句。
4. 不要根据菜名臆测主料；只有当原文食材表里真的写了菜名本身，才能把菜名放进“主料”卡。
5. 主画面必须以菜谱中的器皿与摆盘、桌面与环境、背景陪衬说明为准。
6. 强调整张图是“信息很多但一眼就想收藏”的成熟爆款海报，整张图的版式、字体、配色、标题层级和卡片结构必须严格沿用参考 VI；但主菜图片的真实手机实拍感优先级高于氛围道具和广告大片感。
7. 保留清晰的负面约束，避免极简、错误结构、食材畸形、塑料感、贴边、乱码和过度装饰。
8. 明确写出主菜要与最合适的餐具或上桌工具产生互动，餐具类型、数量、手数和动作瞬间都按菜品自行分析，但要像真实吃饭或上桌抓拍，不要人物脸部，不要手部特写。
9. {build_strict_centered_title_requirement('顶部引导句、主标题和黄条卖点')}
10. {build_page01_title_axis_requirement()}
11. {build_iphone_food_photo_requirement('主菜大图、背景里可见的食材点缀和画面中任何出现的食物内容')}
12. {build_unbranded_prop_requirement('主菜大图、左右信息卡附近、桌面上和背景里的所有器皿、调料、瓶罐、包装与小道具')}
13. {build_page01_real_photo_requirement()}
14. {build_page01_dish_interaction_requirement()}
15. 这些真实拍摄要求只作用于食物照片层，不改变整张海报 VI。
16. 最终输出的 prompt 末尾必须原样追加这一句，并且作为最后一行收尾：{build_page01_final_priority_sentence()}
""".strip()


def build_page01_prompt_user_prompt(recipe_text: str, fixed_dish_name: str) -> str:
    return f"""
请根据下面这份已经定稿的菜谱文字，写出最终文生图 prompt：

主标题必须直接使用：{fixed_dish_name}
{build_strict_centered_title_requirement('顶部引导句、主标题和黄条卖点')}
{build_page01_title_axis_requirement()}
首图从页面顶边到步骤条上缘必须是一张连续主菜背景照片，顶部标题、黄条和左右卡片都直接压在这张照片上；不要再单独做奶白标题底，不要把主菜照片裁成带框主图。
首图上半部不要再额外加收藏提示细条，避免和底部收藏关注横条重复。
画面里必须安排与这道菜匹配的真实餐具和主菜互动，餐具类型、数量、是否一只手或多只手入镜都按菜品自行判断；动作感必须保留，但不要再固定成所有菜都用一双木筷夹起主菜的模板。
{build_iphone_food_photo_requirement('整张海报里')}
{build_unbranded_prop_requirement('整张海报里')}
{build_page01_real_photo_requirement()}
{build_page01_dish_interaction_requirement()}
不要改整张海报的 VI、排版、字体、边框、标题结构和整体配色。
最终 prompt 的最后一行必须原样写成：{build_page01_final_priority_sentence()}
引导句和副标题都要短，不要写成长句。
左侧食材卡必须逐字使用【2人份食材】中的主料、香料、调味料，不要把菜名当成主料，不要删成只剩 1 种香料或 1 种调味料。
右侧成败关键必须逐字使用【成败关键】里的短句；下半部分步骤条必须逐条使用菜谱里实际的【3步出锅】、【4步出锅】或【5步出锅】区块中的标题和内容，不要改写成泛化模板步骤。

{recipe_text}
""".strip()


def build_page01_food_specific_negative_requirements(bundle: dict[str, Any]) -> list[str]:
    combined_text_parts = [
        bundle.get("dish_name", ""),
        bundle.get("main_food", ""),
        bundle.get("sauce", ""),
        bundle.get("texture", ""),
    ]
    for group_name in ("main_ingredients", "spices", "seasonings"):
        combined_text_parts.extend(name for name, _ in bundle.get(group_name, []))

    combined_text = " ".join(part for part in combined_text_parts if part)
    negatives: list[str] = []
    if any(keyword in combined_text for keyword in ("豆腐", "豆干", "豆泡", "豆皮", "千页豆腐")):
        negatives.append("不要把豆腐或豆制品做成所有块面完全一样大一样方")
    if any(keyword in combined_text for keyword in ("耙豌豆", "黄豌豆", "豌豆")):
        negatives.append("不要把豌豆或豆粒做成均匀圆珠密铺，要保留自然软烂和大小差异")
    return negatives


def build_local_page01_prompt(bundle: dict[str, Any]) -> str:
    main_ingredients = "\n".join(f"{name} {amount}" for name, amount in bundle["main_ingredients"])
    spices = "\n".join(f"{name} {amount}" for name, amount in bundle["spices"])
    seasonings = "\n".join(f"{name} {amount}" for name, amount in bundle["seasonings"])
    tips = "\n".join(bundle["tips"])
    dish_specific_negative_lines = build_page01_food_specific_negative_requirements(bundle)
    dish_specific_negative_block = ""
    if dish_specific_negative_lines:
        dish_specific_negative_block = "\n".join(dish_specific_negative_lines) + "\n"
    steps = []
    for index, step in enumerate(bundle["steps"], start=1):
        steps.append(f"{index} {step['title']}\n{step['content']}")
    step_section_title = build_page01_step_section_title(bundle["steps"])

    return f"""
请生成一张竖版 2:3 的中文抖音美食图文海报，主题是：{bundle['dish_name']}。

整体设计模板必须采用成熟爆款菜谱海报风，版式和气质沿用当前满意版本：整张上半部分是一张连续主菜背景照片，顶部双层标题区、黄条和左右信息卡都直接压在这张照片上，下半部分是 3 到 5 步的步骤条，具体按这道菜本身决定，越少越好，最多 5 步，底部是一整条收藏与关注横条。不要极简，不要杂志风，不要大面积留白，必须是信息很多但一眼就想收藏的高密度爆款图文模板。

标题要求：
上方引导句必须是：{bundle['guide_line']}
主标题必须直接使用：{bundle['dish_name']}
主标题下方黄条卖点必须是：{bundle['subtitle']}
首图上半部不要再额外放收藏提示细条，避免和底部横条重复。
{build_strict_centered_title_requirement('顶部引导句、主标题和黄条卖点')}
{build_page01_title_axis_requirement()}

主菜成品图要求：
首图从页面顶边到步骤条上缘必须共用同一张连续主菜背景照片，标题和卡片直接压在照片上；不要把照片单独裁成带框卡片，也不要把标题区另做纯色底，也不要再单独加收藏提示细条。
器皿与摆盘必须是：{bundle['plate']}
桌面与环境必须是：{bundle['table_setting']}
背景陪衬必须是：{bundle['background_props']}
主画面主体必须是：{bundle['main_food']}
酱汁或汤汁状态必须是：{bundle['sauce']}
质感重点必须是：{bundle['texture']}
色彩点缀必须是：{bundle['colors']}
{build_page01_dish_interaction_requirement()}
整张图的标题、字体、边框、配色、卡片排版和海报节奏必须严格沿用当前满意版本的爆款 VI，不要因为主菜更真实就把整张图改成普通手机纪实照片。
{build_iphone_food_photo_requirement('主菜成品图、背景里可见的小食材和桌面上的任何食物内容')}
{build_unbranded_prop_requirement('主菜成品图、左右卡片附近、桌面上和背景里的所有器皿、调料、瓶罐、包装与辅助道具')}
{build_page01_real_photo_requirement()}
主菜成品图里的食物本身要有真实随机差异：块面大小、切面厚薄、煎色或炖色深浅、挂汁厚薄、辅料分布、盘边汁痕和受热痕迹都允许自然不一致，不要像复制粘贴。
这些真实拍摄约束只作用于食物照片层，不改变整张图的 VI、版式、字体和色调逻辑。

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

下半部分“{step_section_title}”步骤条内容必须是：
{step_section_title}
{'\n'.join(steps)}

底部收藏关注横条内容必须是：
{bundle['collection_copy']}
{bundle['ad_copy']}

整体配色固定为暖奶白、橙红、金黄、焦糖棕，但桌面材质、辅助餐具和后景小物必须服从上面的器皿与摆盘、桌面与环境、背景陪衬说明，不要默认每道菜都套成木桌、陶盘、木托和小碗的固定组合。所有中文必须正确、自然、工整、无乱码、无错字。

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
不要商业棚拍感
不要电商主图感
不要爆款大片假光
不要 3D 渲染假光
不要 AI 渲染假光效果
不要上方纯色标题底加下方独立主图的两段式结构
不要把标题区和照片区横向切开
不要带框主图卡片
不要圆角主图相框
不要品牌器皿
不要带字调料瓶
不要带标签酱料罐
不要超市包装袋
不要假商标和假品牌字样
不要全图油亮发光
{dish_specific_negative_block}不要把主菜或配菜做成复制粘贴的完全同形块面
不要把真实拍摄理解为整张图改成普通纪实照片
不要把原来的海报 VI 改掉
不要过度饱和
不要无意义装饰

{build_page01_final_priority_sentence()}
""".strip()
