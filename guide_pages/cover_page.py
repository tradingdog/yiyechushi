from __future__ import annotations

from typing import Any


COVER_NAME = "封面"
COVER_ASPECT_RATIO_LABEL = "2:3"


def build_cover_output_name(dish_name: str) -> str:
    return f"{dish_name}{COVER_NAME}"


def format_vertical_dish_name(dish_name: str) -> str:
    characters = [character for character in dish_name.strip() if not character.isspace()]
    return "\n".join(characters) or dish_name.strip()


def format_inline_vertical_dish_name(dish_name: str) -> str:
    characters = [character for character in dish_name.strip() if not character.isspace()]
    return "、".join(characters) or dish_name.strip()


def build_cover_centered_vertical_title_requirement(fixed_dish_name: str, vertical_dish_name: str) -> str:
    return "\n".join(
        [
            f"菜名必须严格使用“{fixed_dish_name}”，并按下面这种单列逐字竖排写法理解：",
            vertical_dish_name,
            "菜名单列竖排居中于整幅画面，画布正中留一整条竖向标题通道。",
            "背景主菜、餐盘和陪衬必须主动避开中轴标题通道，不能把竖排菜名挤到侧边。",
        ]
    )


def build_cover_scene_lock_description(bundle: dict[str, Any]) -> str:
    return (
        f"{bundle['plate']}；{bundle['table_setting']}；{bundle['background_props']}；"
        f"{bundle['main_food']}；{bundle['sauce']}"
    )


def build_cover_fixed_prompt_template(
    bundle: dict[str, Any],
    fixed_dish_name: str,
    vertical_dish_name: str,
) -> str:
    inline_vertical_name = format_inline_vertical_dish_name(fixed_dish_name)
    return (
        f"参考图里的主菜照片为背景。整张图为竖版{COVER_ASPECT_RATIO_LABEL}封面图。"
        f"菜名单列竖排居中于整幅画面，菜名严格为“{fixed_dish_name}”，按逐字竖排：{inline_vertical_name}，"
        "字体沿用一页菜谱主标题的红色手写刷字质感并带明显白色勾边。"
        "画布正中留一整条竖向标题通道，背景主菜、餐盘和陪衬主动避开中轴标题通道。"
        "除菜名外画面其它区域0文字。"
        f"封面背景沿用参考图同一道菜的器皿与摆盘、桌面与环境、背景陪衬、主画面食材和酱汁状态：{build_cover_scene_lock_description(bundle)}。"
        "背景带轻微景深虚化和少量散景，仍看得出参考图里那套盘器、桌面、酱汁和后景关系。"
        "背景中的菜品、食材和器皿为iPhone主摄1x默认相机直拍的真实照片感，30到45度轻微俯拍，暖调低饱和，无商业棚拍假光。"
        "背景中的器皿、调料、瓶罐和辅助道具均为无品牌、无商标、无logo、无可读标签的通用素面版本。"
        "不要引导句、副标题、黄条卖点、收藏提示、关注文案、步骤卡、徽章、小字、英文、乱码、人物脸部和与参考图无关的模板背景。"
    )


def build_cover_prompt_system_prompt(
    style_reference: str,
    fixed_dish_name: str,
    vertical_dish_name: str,
    bundle: dict[str, Any],
) -> str:
    return f"""
你是阿叶造新菜账号的封面 prompt 导演。你的任务是把一份已经完成的菜谱文案，改写成一条更简洁直接、句式固定、尽量贴近用户手写简化版的中文封面图 prompt。

不要自行增加复杂条件、长清单、重复限制或额外模块。输出时尽量直接沿用下面这条固定模板的句式和信息顺序，只允许把背景场景细节改得更贴近当前这道菜：

{build_cover_fixed_prompt_template(bundle, fixed_dish_name, vertical_dish_name)}

必须保留的硬约束只有这些：
1. 竖版 {COVER_ASPECT_RATIO_LABEL} 独立封面图，不是整张一页菜谱。
2. 菜名必须严格使用“{fixed_dish_name}”，并保持单列逐字竖排居中。
3. 画布正中留一整条竖向标题通道，背景主菜、餐盘和陪衬主动避开中轴标题通道。
4. 除菜名外画面其它区域 0 文字。
5. 封面背景沿用首图同一道菜的同场景，不要另起模板。

当前满意版本的一页菜谱 prompt 只可作为字体笔触和整体气质的间接参考，绝不能把它里面的顶部引导句、副标题、卖点黄条、收藏提示条、关注横条、星标徽章、分隔线文案、小字说明搬到封面里。

只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要解释。
""".strip()


def build_cover_prompt_user_prompt(
    bundle: dict[str, Any],
    fixed_dish_name: str,
    vertical_dish_name: str,
) -> str:
    return f"""
请按下面这条固定模板直接写出最终封面图 prompt，尽量贴近这个句式和密度，不要扩写成更复杂的条件限制：

{build_cover_fixed_prompt_template(bundle, fixed_dish_name, vertical_dish_name)}

如果需要根据首图微调，只改背景场景细节，不要改变这条模板的句式风格、信息顺序和简洁程度。
""".strip()


def build_local_cover_prompt(bundle: dict[str, Any]) -> str:
    vertical_dish_name = format_vertical_dish_name(bundle["dish_name"])
    return build_cover_fixed_prompt_template(bundle, bundle["dish_name"], vertical_dish_name)
