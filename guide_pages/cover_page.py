from __future__ import annotations

from typing import Any

from guide_pages.shared import build_iphone_food_photo_requirement, build_unbranded_prop_requirement


COVER_NAME = "封面"


def build_cover_output_name(dish_name: str) -> str:
    return f"{dish_name}{COVER_NAME}"


def format_vertical_dish_name(dish_name: str) -> str:
    characters = [character for character in dish_name.strip() if not character.isspace()]
    return "\n".join(characters) or dish_name.strip()


def build_cover_centered_vertical_title_requirement(fixed_dish_name: str, vertical_dish_name: str) -> str:
    return "\n".join(
        [
            f"菜名必须严格使用“{fixed_dish_name}”，并按下面这种单列逐字竖排写法理解：",
            vertical_dish_name,
            "画布正中必须留出一整条竖向标题通道，这条通道从上方贯穿到下方，只给菜名使用。",
            "菜名必须单列竖排压在画布中轴线上，每个字的中心点都要落在同一条正中竖线上，不允许靠左贴边、靠右偏移、双列排布、蛇形排布、弧线排布或上下错位。",
            "背景里的主菜、餐盘和陪衬必须主动避开这条中轴标题通道，主要退到下半部、右下或左下区域，不能把中间竖排菜名挤到侧边。",
        ]
    )


def build_cover_scene_lock_description(bundle: dict[str, Any]) -> str:
    return "\n".join(
        [
            "封面背景必须沿用首图同一道菜的同一套上桌场景逻辑，只是改成更适合竖排封面的背景构图，不是另起一套新的暖光摆盘模板。",
            f"器皿与摆盘必须沿用：{bundle['plate']}",
            f"桌面与环境必须沿用：{bundle['table_setting']}",
            f"背景陪衬必须沿用：{bundle['background_props']}",
            f"主画面食材必须沿用：{bundle['main_food']}",
            f"酱汁或汤汁状态必须沿用：{bundle['sauce']}",
            f"质感重点必须沿用：{bundle['texture']}",
            f"色彩点缀必须沿用：{bundle['colors']}",
            "如果首图更适合长方盘、深色石面、暖油亮酱汁、小味碟或玻璃壶这类组合，封面也必须继续保留同类场景关系，只允许通过构图和景深把它们退到背景层。",
            "不要擅自改成暖色陶盘、木纹桌面、木勺、竹蒸笼、陶罐、小砂锅或泛黄暖光那套与首图无关的通用封面模板。",
        ]
    )


def build_cover_prompt_system_prompt(
    style_reference: str,
    fixed_dish_name: str,
    vertical_dish_name: str,
    bundle: dict[str, Any],
) -> str:
    return f"""
你是阿叶造新菜账号的封面 prompt 导演。你的任务是把一份已经完成的菜谱文案，转换成一条给 gpt-image-2 使用的中文封面图 prompt。

封面图不是整张一页菜谱，必须是独立封面：
1. 画幅必须是竖版 9:16，整体是更高更窄的短视频封面比例。
2. 整体 VI 要和当前一页菜谱海报统一，但背景食物的器皿、桌面、后景陪衬和色调关系也必须与首图同一道菜保持同一套场景逻辑，不允许封面另起一套固定暖陶盘或木桌模板。
3. 封面主体不是与文字抢焦的清晰英雄特写，但背景必须明显来自首图同一道菜的同场景版本；允许看出餐盘轮廓、桌面材质、酱汁光泽和少量后景陪衬，只是整体清晰度低于菜名字体。
4. 画面中唯一允许出现的可读文字只能是菜名本身，也就是“{fixed_dish_name}”这几个字。除这几个字以外，禁止出现任何其它汉字、英文、数字、标点、说明字、短句、页眉、页脚、角标、徽章、横条文案。
5. 菜名必须是严格单列竖排，压在画布正中竖轴上；不是左侧竖条，不是右侧竖条，不是双列竖排，也不是只是“大概居中”。字体风格要直接沿用一页菜谱主标题的红色手写刷字质感，并带明显白色勾边，文字必须是唯一最清晰的主体。
6. 菜名必须严格使用：{fixed_dish_name}
7. 推荐纵向排布写法如下，便于模型理解：
{vertical_dish_name}
8. 由于 9:16 画幅更高，背景主菜和餐盘必须进一步压缩到中下段、下半部、右下或左下，不允许向上铺满，也不允许把标题挤到边上。

首图场景锁定信息如下，最终 prompt 里必须把这些场景要求明确带进去，不要自行改景：
{build_cover_scene_lock_description(bundle)}

当前满意版本的一页菜谱 prompt 只可作为字体笔触、配色气质和背景氛围的间接参考，绝不能把它里面的顶部引导句、副标题、卖点黄条、收藏提示条、关注横条、星标徽章、分隔线文案、小字说明搬到封面里。

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要解释。
2. 必须明确写出“竖版 9:16 封面图”。
3. 必须明确写出“封面背景沿用首图同一道菜的器皿与摆盘、桌面与环境、背景陪衬、主画面食材和酱汁状态”，不要把这些场景重新设计成另一套模板。
4. 可以让背景带轻微景深虚化和少量散景，但不能把场景虚到只剩一团暖色；仍要看得出首图同一道菜的盘器、桌面和酱汁关系。9:16 构图里背景主体要更多落在中下段，不要把食物铺满上半段。
5. 必须明确写出“画布正中留一整条竖向标题通道，菜名单列竖排压在中轴线上”。
6. 必须明确写出“背景主菜和餐盘主动避开中轴标题通道，主要退到中下段、下半部、右下或左下”。
7. 必须明确写出“除菜名外画面其它区域 0 文字”。
8. 必须明确禁止顶部引导句、副标题、黄条卖点、收藏提示、关注文案、星标徽章、页脚说明、任何小字和任何装饰性文字。
9. {build_iphone_food_photo_requirement('封面背景里出现的所有菜品和食材氛围')}
10. {build_unbranded_prop_requirement('封面背景里出现的所有器皿、调料、瓶罐、包装与辅助道具')}
11. 保留清晰的负面约束，避免额外文字、额外模块、英文、乱码、人物、低质假光、清晰整盘主菜，以及与首图无关的固定暖陶盘木桌模板。
""".strip()


def build_cover_prompt_user_prompt(
    bundle: dict[str, Any],
    fixed_dish_name: str,
    vertical_dish_name: str,
) -> str:
    return f"""
请写出最终封面图 prompt：

整张图必须是更高更窄的竖版 9:16 短视频封面，不是旧版 2:3。画面上半段要给竖排菜名留出更长的呼吸空间，主要食物内容更多退到中下段。

封面图唯一允许出现的文字只能是：{fixed_dish_name}
除这几个字之外，画面其它任何位置都禁止出现其它汉字、英文、数字、标点、短句、页眉、页脚、标签、徽章、收藏提示、关注文案、引导句、副标题、黄条卖点。
{build_cover_centered_vertical_title_requirement(fixed_dish_name, vertical_dish_name)}
封面背景必须像把首图主画面的同一道菜、同一套器皿、同一张桌面和同一批后景陪衬重新拍成封面版；不能换成另一套暖陶盘木桌模板。
背景可以有轻微景深虚化和少量散景，但仍要看得出首图那套盘器、桌面、酱汁和后景关系，不要虚成一团泛黄暖光。9:16 构图里请把主要餐盘和主菜压到中下段。
{build_cover_scene_lock_description(bundle)}
{build_iphone_food_photo_requirement('封面背景中的食物内容')}
{build_unbranded_prop_requirement('封面背景中的器皿、调料、瓶罐、包装与辅助道具')}
文字必须是唯一最清晰的主体，整体色调与一页菜谱 VI 统一。
请在 prompt 里明确写出“除菜名外画面其它区域 0 文字”。
""".strip()


def build_local_cover_prompt(bundle: dict[str, Any]) -> str:
    vertical_dish_name = format_vertical_dish_name(bundle["dish_name"])
    return f"""
请生成一张竖版 9:16 的中文美食封面图，主题是：{bundle['dish_name']}。

这不是整张一页菜谱，而是一张独立封面。整体 VI 必须和一页菜谱统一，沿用暖奶白、橙红、金黄、焦糖棕的成熟爆款美食视觉。
这次封面必须采用更高更窄的 9:16 短视频封面构图，上半段给竖排菜名留出更长的视觉通道，主要食物内容更多落在中下段。

封面背景要求：
封面背景必须沿用首图同一道菜的同一套上桌场景，不要另起一套摆盘模板：
器皿与摆盘必须是：{bundle['plate']}
桌面与环境必须是：{bundle['table_setting']}
背景陪衬必须是：{bundle['background_props']}
主画面食材必须是：{bundle['main_food']}
酱汁或汤汁状态必须是：{bundle['sauce']}
质感重点必须是：{bundle['texture']}
色彩点缀必须是：{bundle['colors']}
背景可以有轻微景深虚化和少量散景，但仍要让人看得出首图那套盘器、桌面、酱汁和后景关系；不要虚成一团泛黄暖光，不要自动改成暖色陶盘、木纹桌面、陶罐或木勺那套固定封面模板。9:16 构图里主菜和餐盘不要向上铺满，更多压在中下段。
封面的文字设计、排版、描边字体、配色和整体 VI 必须严格沿用当前满意版本，只把背景菜品本身做得更像真实拍摄到的食物，不要把整张封面改成普通手机照片。
{build_iphone_food_photo_requirement('背景食物')}
{build_unbranded_prop_requirement('背景里的器皿、调料、瓶罐、包装与辅助道具')}
不要生成清晰完整的整盘主菜大特写，但也不要把首图场景洗成只剩抽象暖光；文字仍是最清晰主体，背景只是退后半级的同场景主菜画面。

文字要求：
菜名必须直接使用 {bundle['dish_name']}，并且严格单列竖排压在画布中轴线上。
画面里唯一允许出现的文字就是 {bundle['dish_name']} 这几个字，除菜名外其它区域必须 0 文字。
字体必须像一页菜谱主标题那样的红色手写刷字质感，带明显白色勾边和高对比描边，文字必须是唯一最清晰主体。
{build_cover_centered_vertical_title_requirement(bundle['dish_name'], vertical_dish_name)}
背景主菜、餐盘和陪衬必须主动避开中轴标题通道，主要落在中下段、下半部、右下或左下，不允许把竖排菜名挤到左边或右边。

封面中不要食材卡，不要步骤卡，不要成败关键，不要顶部引导句，不要副标题，不要黄条卖点，不要底部收藏横条，不要关注文案，不要页脚说明，不要星标徽章，不要任何小字，不要任何额外长文字。

强负面要求：
不要极简纯色底
不要英文
不要数字
不要标点小字
不要乱码
不要多余模块
不要人物脸部
不要手部特写
不要低质假光
不要塑料食物
不要品牌瓶罐
不要带标签包装
不要印字器皿
不要商业棚拍感
不要电商主图感
不要 3D 渲染假光
不要清晰完整餐盘
不要清晰主菜特写
不要把封面整体做成普通纪实照片
不要把原有封面 VI 改掉
不要和一页菜谱 VI 脱节
""".strip()
