from __future__ import annotations

from typing import Any

from guide_pages.shared import (
    build_iphone_food_photo_requirement,
    build_unbranded_prop_requirement,
)


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
            "封面背景直接沿用首图同一道菜的同场景，不要另起一套暖陶盘木桌模板。",
            f"参考场景要点：{bundle['plate']}；{bundle['table_setting']}；{bundle['background_props']}；{bundle['main_food']}；{bundle['sauce']}。",
            "只保留能帮助识别同一道菜的盘器、桌面、酱汁和后景关系，其它描述从简，不要把 prompt 写成过长清单。",
        ]
    )


def build_cover_prompt_system_prompt(
    style_reference: str,
    fixed_dish_name: str,
    vertical_dish_name: str,
    bundle: dict[str, Any],
) -> str:
    return f"""
你是阿叶造新菜账号的封面 prompt 导演。你的任务是把一份已经完成的菜谱文案，改写成一条更简洁直接、能给 gpt-image-2 使用的中文封面图 prompt。

这条封面 prompt 要短、准、单目标，不要把同一件事重复说三遍，也不要把首图场景拆成过长清单。

必须保留的硬约束只有这些：
1. 竖版 9:16 独立封面图，不是整张一页菜谱。
2. 菜名必须严格使用“{fixed_dish_name}”，按单列逐字竖排理解：
{vertical_dish_name}
3. 画布正中必须留完整竖向标题通道，菜名单列竖排压在中轴线上；背景主菜、餐盘和陪衬主动避开这条中轴，主要退到中下段、下半部、右下或左下。
4. 除菜名外，画面其它区域 0 文字；不要引导句、副标题、黄条卖点、收藏提示、关注文案、徽章、步骤卡、小字或任何装饰性文字。
5. 字体直接沿用一页菜谱主标题的红色手写刷字质感，并带明显白色勾边，文字必须是唯一最清晰主体。
6. 封面背景直接沿用首图同一道菜的同场景，不要另起模板；优先抓住参考图里的盘器、桌面、酱汁和后景关系，不要把 prompt 写得比需要的更复杂。

首图场景锚点如下，只保留最关键的信息，不要继续扩写成更长的列表：
{build_cover_scene_lock_description(bundle)}

当前满意版本的一页菜谱 prompt 只可作为字体笔触、配色气质和背景氛围的间接参考，绝不能把它里面的顶部引导句、副标题、卖点黄条、收藏提示条、关注横条、星标徽章、分隔线文案、小字说明搬到封面里。

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要解释。
2. 最终 prompt 尽量写成紧凑的一段或少量短句，不要重复堆约束。
3. 必须明确写出“竖版 9:16 封面图”。
4. 必须明确写出“封面背景沿用首图同一道菜的同场景”。
5. 必须明确写出“画布正中留一整条竖向标题通道，菜名单列竖排压在中轴线上”。
6. 必须明确写出“背景主菜和餐盘主动避开中轴标题通道，主要退到中下段、下半部、右下或左下”。
7. 必须明确写出“除菜名外画面其它区域 0 文字”。
8. 背景可以有轻微景深虚化和少量散景，但仍要看得出首图那套盘器、桌面、酱汁和后景关系，不能虚成一团暖光。
9. {build_iphone_food_photo_requirement('封面背景里出现的所有菜品和食材氛围')}
10. {build_unbranded_prop_requirement('封面背景里出现的所有器皿、调料、瓶罐、包装与辅助道具')}
""".strip()


def build_cover_prompt_user_prompt(
    bundle: dict[str, Any],
    fixed_dish_name: str,
    vertical_dish_name: str,
) -> str:
    return f"""
请写出最终封面图 prompt：

整张图必须是更高更窄的竖版 9:16 短视频封面，不是旧版 2:3。prompt 请写得简洁直接，不要把同一套要求重复堆很多次。

封面图唯一允许出现的文字只能是：{fixed_dish_name}
除这几个字之外，画面其它任何位置都禁止出现其它汉字、英文、数字、标点、短句、页眉、页脚、标签、徽章、收藏提示、关注文案、引导句、副标题、黄条卖点。
{build_cover_centered_vertical_title_requirement(fixed_dish_name, vertical_dish_name)}
封面背景必须像把首图主画面的同一道菜重新拍成封面版；不能换成另一套暖陶盘木桌模板。
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

参考图里的主菜照片为背景。整张图不是一页菜谱，而是一张独立封面；整体 VI 继续沿用一页菜谱主标题的红色手写刷字、暖奶白橙红金黄焦糖棕气质，但 prompt 要简洁直接。
菜名单列竖排居中于整幅画面，菜名严格为“{bundle['dish_name']}”，按逐字竖排：{vertical_dish_name}。
画布正中留一整条竖向标题通道，背景主菜、餐盘和陪衬主动避开中轴标题通道，主要退至中下段、下半部、右下或左下区域。
封面背景沿用首图同一道菜的同场景，不要另起模板：{build_cover_scene_lock_description(bundle)}
背景带轻微景深虚化和少量散景，仍看得出首图那套盘器、桌面、酱汁和后景关系，不要虚成一团泛黄暖光，也不要把食物铺满上半段。
{build_iphone_food_photo_requirement('背景食物')}
{build_unbranded_prop_requirement('背景里的器皿、调料、瓶罐、包装与辅助道具')}
不要生成清晰完整的整盘主菜大特写，但也不要把首图场景洗成只剩抽象暖光；文字仍是最清晰主体，背景只是退后半级的同场景主菜画面。
画面里唯一允许出现的文字就是 {bundle['dish_name']} 这几个字，除菜名外其它区域必须 0 文字。字体必须像一页菜谱主标题那样的红色手写刷字质感，带明显白色勾边和高对比描边，文字必须是唯一最清晰主体。
{build_cover_centered_vertical_title_requirement(bundle['dish_name'], vertical_dish_name)}
封面中不要食材卡，不要步骤卡，不要成败关键，不要顶部引导句，不要副标题，不要黄条卖点，不要底部收藏横条，不要关注文案，不要页脚说明，不要星标徽章，不要任何小字和额外长文字，也不要英文、乱码、品牌、人物脸部、低质假光和与首图无关的固定暖陶盘木桌模板。
""".strip()
