from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
import re

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from v2_core import (
    IDEA_FILE,
    auto_generate_dish_idea,
    build_three_card_script_fallback,
    build_doubao_client,
    build_openai_image_client,
    build_run_output_dir,
    ensure_runtime_config_loaded,
    extract_image_items,
    generate_three_card_script,
    get_cover_image_count,
    generate_images_by_prompt,
    get_content_track,
    get_image_settings,
    get_timestamp,
    load_cankao_template,
    load_cover_template,
    load_manual_dish_idea,
    parse_bool_env,
    parse_float_env,
    parse_int_env,
    render_cover_prompt_by_template,
    sanitize_file_name,
    save_generated_images,
    save_text_output,
    write_dish_idea_file,
)

from tools.select_publish_images import select_publish_images
from tools.apply_photoshop_template_batch import apply_photoshop_template_batch_to_dir


def resolve_auto_generate_enabled(mode: str | None) -> bool:
    if mode == "auto":
        return True
    if mode == "file":
        return False
    return parse_bool_env("AUTO_GENERATE_DISH_IDEA", default=False)


def find_selected_output_path(selection_result: dict[str, object], page_type: str) -> str:
    for group_result in selection_result.get("groups", []):
        if not isinstance(group_result, dict):
            continue
        if str(group_result.get("page_type", "")) != page_type:
            continue
        selected = str(group_result.get("selected_output_path", "")).strip()
        if selected:
            return selected
    return ""


def find_selected_output_path_by_group(selection_result: dict[str, object], group_keyword: str) -> str:
    for group_result in selection_result.get("groups", []):
        if not isinstance(group_result, dict):
            continue
        group_label = str(group_result.get("group_label", "")).strip()
        selected = str(group_result.get("selected_output_path", "")).strip()
        if selected and group_keyword in group_label:
            return selected
    return ""


def move_image_to_publish(source_image_path: str, publish_dir: Path) -> str:
    source = Path(source_image_path)
    publish_dir.mkdir(parents=True, exist_ok=True)
    target = publish_dir / source.name
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return str(target)


def remap_selected_image_path(image_paths: list[str], selected_path: str) -> list[str]:
    if not selected_path:
        return image_paths
    selected_name = Path(selected_path).name
    remapped: list[str] = []
    replaced = False
    for path in image_paths:
        if Path(path).name == selected_name:
            remapped.append(selected_path)
            replaced = True
        else:
            remapped.append(path)
    if not replaced:
        remapped.append(selected_path)
    return remapped


def build_three_card_prompts(dish_name: str, script_payload: dict[str, object]) -> list[dict[str, str]]:
    forbidden_overlay_terms = [
        "图解教程",
        "图解1/3",
        "图解2/3",
        "图解3/3",
        "步骤与转化页",
        "教程页",
        "转化页",
        "第1步",
        "第2步",
        "第3步",
        "Step 1",
        "Step 2",
        "Step 3",
    ]
    forbidden_overlay_text = "、".join(forbidden_overlay_terms)
    meta_text_cleaner = re.compile(r"(图解教程|图解\s*\d+\s*/\s*\d+|步骤与转化页|教程页|转化页|第\s*[一二三123]\s*步)", re.IGNORECASE)

    def clean_text_overlay(raw_text: str) -> str:
        text = meta_text_cleaner.sub("", raw_text).strip()
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"^[·•\-—\s]+", "", text)
        text = re.sub(r"[·•\-—\s]+$", "", text)
        return text

    card1_hook_text = clean_text_overlay(str(script_payload.get("card1_hook", "")).strip())
    card1_sub_text = clean_text_overlay(str(script_payload.get("card1_sub", "")).strip())
    if not card1_hook_text:
        card1_hook_text = f"{dish_name}这样做太香了"
    if not card1_sub_text:
        card1_sub_text = "家常做法，真能一次成功"

    card2_items = script_payload.get("card2_items", [])
    if not isinstance(card2_items, list):
        card2_items = []
    card2_lines = [f"- {str(item).strip()}" for item in card2_items if str(item).strip()]
    if not card2_lines:
        card2_lines = ["- 主料 300g", "- 辅料 200g", "- 食用油 15ml", "- 盐 1茶匙"]
    card2_text = "\n".join(card2_lines[:10])
    try:
        cankao_text = load_cankao_template()
    except Exception:
        cankao_text = ""

    realism_anchor = (
        "写实硬约束：必须是普通家庭餐桌场景（中国大陆家庭常见环境），"
        "iPhone主摄1x直拍、低饱和、轻微暖调；"
        "保留人为痕迹：刀工可略不整齐、摆盘可微凌乱、酱汁与餐具可有自然使用痕迹，"
        "整体像家里现做现吃，不像商业棚拍或AI机械拼贴。"
    )
    if cankao_text:
        realism_anchor = (
            f"{realism_anchor}"
            "同时遵循参考规范中的核心要求：家常真实、人与食物互动自然、禁品牌与logo。"
        )
    visual_consistency_block = (
        "三张图必须是同一套系列视觉：同一菜品、同一拍摄场景与器皿、同一暖色调、同一字体风格、同一排版骨架。"
        "可理解为同一模板拆成3页，不允许每页像不同账号/不同模板。"
    )
    fixed_ad_slogan = "关注@阿叶造新菜，开店家用都不赖！"

    card1_prompt = (
        f"为菜品“{dish_name}”生成竖版2:3系列图的首张封面钩子图。"
        "画面主体是刚出锅成品，真实手机拍摄质感，暖色家常氛围。"
        f"顶部大字标题：{card1_hook_text}"
        f"；副标题：{card1_sub_text}。"
        "文字要粗大清晰，排版稳定，突出“看完还想继续滑”的视觉冲击力。"
        f"{realism_anchor}"
        f"{visual_consistency_block}"
        "文字白名单：只允许出现“菜名、钩子标题、副标题”三类文字。"
        f"文字黑名单：禁止出现“{forbidden_overlay_text}”及任何流程标签。"
        "禁止品牌logo和无关英文。"
    )

    card2_prompt = (
        f"为菜品“{dish_name}”生成竖版2:3系列图的食材清单页。"
        f"标题：{script_payload.get('card2_title','食材清单')}。"
        "必须严格继承图解01的视觉设计（色温、配色、字体、装饰元素、边框样式、阴影强度）。"
        "画面采用两列清单布局，文字清晰易读，留白合理。"
        "每个食材条目都必须带明确计量单位（g/ml/汤匙/茶匙/个等），禁止只写“适量/少许”。"
        f"食材内容如下：\n{card2_text}\n"
        "这一页只做食材信息，不要步骤，不要大段营销文案。"
        f"{realism_anchor}"
        f"{visual_consistency_block}"
        "文字白名单：只允许出现“菜名（可选）、食材清单标题、食材条目”。"
        f"文字黑名单：禁止出现“{forbidden_overlay_text}”及任何流程标签。"
        "真实手机拍摄风格，无品牌logo。"
    )

    card3_step_text = clean_text_overlay(str(script_payload.get("card3_step", "")).strip())
    if not card3_step_text:
        card3_step_text = "食材处理好后大火快炒上色，转中火焖3分钟，收汁后立刻出锅"
    card3_cta_text = clean_text_overlay(str(script_payload.get("card3_cta", "")).strip())
    if "收藏" not in card3_cta_text:
        card3_cta_text = f"{card3_cta_text}，收藏起来下次做".strip("，")
    if fixed_ad_slogan not in card3_cta_text:
        card3_cta_text = f"{card3_cta_text}；{fixed_ad_slogan}".strip("；")

    card3_prompt = (
        f"为菜品“{dish_name}”生成竖版2:3系列图的做法页。"
        f"一句话做法（必须具体可执行，不要任何编号前缀）：{card3_step_text}。"
        f"底部两行引导文案：第一行“收藏起来，下次想吃直接做”；第二行“{fixed_ad_slogan}”。"
        f"若需要补充口吻，可参考：{card3_cta_text}。"
        "必须严格继承图解01/图解02的视觉设计（同色调、同字体、同版式系统）。"
        "画面主体为成品拌饭/出锅场景，突出食欲和可执行性。"
        f"{realism_anchor}"
        "底部引导文案必须完整可见，不可裁切，不可改字。"
        f"{visual_consistency_block}"
        "文字白名单：只允许出现“菜名（可选）、一句话做法、收藏引导、关注引导”四类文字。"
        f"文字黑名单：禁止出现“{forbidden_overlay_text}”及任何流程标签。"
        "真实手机拍摄风格，无品牌logo。"
    )

    return [
        {"group_name": "图解01_一页菜谱", "prompt": card1_prompt},
        {"group_name": "图解02_食材清单", "prompt": card2_prompt},
        {"group_name": "图解03_一步做法", "prompt": card3_prompt},
    ]


def select_or_direct_for_group(
    *,
    run_output_dir: Path,
    page_type: str,
    image_paths: list[str],
    publish_dir: Path,
) -> tuple[str, str, dict[str, object]]:
    if not image_paths:
        return "", "", {}
    if len(image_paths) == 1:
        selected = move_image_to_publish(image_paths[0], publish_dir)
        return selected, "direct", {}

    selection = select_publish_images(
        input_dir=run_output_dir,
        include_page_types=(page_type,),
    )
    selected = find_selected_output_path(selection, page_type=page_type)
    return selected, "scored" if selected else "", selection


def generate_cover_images_from_reference(
    *,
    image_client,
    reference_image_path: str,
    cover_prompt: str,
    cover_settings: dict[str, object],
) -> list[dict[str, str]]:
    request_timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    max_retry = parse_int_env("IMAGE_REQUEST_RETRY_COUNT", 2)
    response = None
    for attempt in range(1, max_retry + 1):
        try:
            with open(reference_image_path, "rb") as image_file:
                response = image_client.images.edit(
                    model=cover_settings["model"],
                    image=image_file,
                    prompt=cover_prompt,
                    size=cover_settings["size"],
                    quality=cover_settings["quality"],
                    n=cover_settings["image_count"],
                    timeout=request_timeout,
                )
            break
        except Exception as exc:
            if attempt >= max_retry:
                raise RuntimeError(f"封面生图失败：{exc}") from exc
            print(f"封面生图失败，正在重试第 {attempt + 1}/{max_retry} 次...")

    if response is None:
        raise RuntimeError("封面生图失败：接口未返回有效响应。")
    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("封面生图失败：接口未返回有效图片数据。")
    return image_items


def run_v2_first_feature(mode: str | None = None) -> dict[str, object]:
    ensure_runtime_config_loaded()

    doubao_client = build_doubao_client()
    image_client = build_openai_image_client()
    content_track = get_content_track()

    raw_auto_flag = os.getenv("AUTO_GENERATE_DISH_IDEA", "").strip()
    auto_generate_enabled = resolve_auto_generate_enabled(mode)
    print(
        "自动造菜开关："
        f"AUTO_GENERATE_DISH_IDEA={raw_auto_flag or '<未设置>'}，"
        f"命令行模式={mode or '按配置'}，最终模式={'auto' if auto_generate_enabled else 'file'}"
    )
    if auto_generate_enabled:
        dish_payload = auto_generate_dish_idea(doubao_client)
        write_dish_idea_file(dish_payload["dish_name"], dish_payload["notes"], idea_file=IDEA_FILE)
        print(f"自动造菜完成，已写回：{IDEA_FILE}")
        if dish_payload.get("region_label"):
            print(f"本轮菜系范围：{dish_payload['region_label']}")
        if dish_payload.get("reference_dish"):
            print(f"本轮参考传统菜：{dish_payload['reference_dish']}")
        if dish_payload.get("memory_file"):
            print(f"历史记忆文件：{dish_payload['memory_file']}")
    else:
        dish_payload = load_manual_dish_idea(idea_file=IDEA_FILE)
        print(f"使用手动录入菜名：{dish_payload['dish_name']}")

    dish_name = dish_payload["dish_name"]
    notes = dish_payload.get("notes", "")
    timestamp = get_timestamp()
    run_output_dir = build_run_output_dir(timestamp, dish_name)
    print(f"输出目录：{run_output_dir}")
    script_payload: dict[str, object]
    try:
        script_payload = generate_three_card_script(
            client=doubao_client,
            dish_name=dish_name,
            notes=notes,
            content_track=content_track,
        )
    except Exception as script_exc:
        print(f"三图脚本生成失败，切换本地降级脚本：{script_exc}")
        script_payload = build_three_card_script_fallback(dish_name=dish_name, notes=notes, content_track=content_track)

    script_file = run_output_dir / f"{timestamp}_{sanitize_file_name(dish_name)}_三图脚本.json"
    save_text_output(str(script_payload), script_file)
    print(f"三图脚本已生成：{script_file}")

    card_prompts = build_three_card_prompts(dish_name=dish_name, script_payload=script_payload)
    image_settings = get_image_settings()
    print(
        "开始生成固定3张正文图："
        f"model={image_settings['model']}，size={image_settings['size']}，"
        f"quality={image_settings['quality']}，每组候选n={image_settings['image_count']}"
    )

    card_results: list[dict[str, object]] = []
    image_error = ""
    for card in card_prompts:
        group_name = str(card["group_name"])
        prompt_text = str(card["prompt"])
        prompt_file = run_output_dir / f"{timestamp}_{sanitize_file_name(dish_name)}_{group_name}_文生图prompt.txt"
        save_text_output(prompt_text, prompt_file)
        try:
            image_items = generate_images_by_prompt(
                client=image_client,
                prompt_text=prompt_text,
                settings=image_settings,
            )
            saved = save_generated_images(
                image_items=image_items,
                output_dir=run_output_dir,
                timestamp=timestamp,
                dish_name=f"{dish_name}_{group_name}",
            )
            for image_file in saved:
                print(f"{group_name} 已保存：{image_file}")
            page_type = "page01" if "图解01_一页菜谱" in group_name else "guide_page"
            card_results.append(
                {
                    "group_name": group_name,
                    "page_type": page_type,
                    "prompt_file": str(prompt_file),
                    "image_paths": saved,
                    "selected_path": "",
                    "selection_mode": "",
                }
            )
        except Exception as card_exc:
            image_error = f"{image_error}\n{group_name} 生图失败：{card_exc}".strip()
            print(f"{group_name} 生图失败：{card_exc}")
            card_results.append(
                {
                    "group_name": group_name,
                    "page_type": "page01" if "图解01_一页菜谱" in group_name else "guide_page",
                    "prompt_file": str(prompt_file),
                    "image_paths": [],
                    "selected_path": "",
                    "selection_mode": "",
                }
            )

    saved_images: list[str] = []

    primary_publish_selection: dict[str, object] = {}
    primary_selection_mode = ""
    primary_selected_image = ""
    cover_prompt_file = ""
    cover_saved_images: list[str] = []
    cover_publish_selection: dict[str, object] = {}
    cover_selection_mode = ""
    cover_selected_image = ""
    cover_image_error = ""
    photoshop_processed_files: list[str] = []
    photoshop_error = ""
    publish_dir = run_output_dir / "publish"

    # 先处理数量=1直通入选
    need_score_page_types: set[str] = set()
    for card_result in card_results:
        image_paths = list(card_result.get("image_paths", []))
        page_type = str(card_result.get("page_type", ""))
        if len(image_paths) == 1:
            selected = move_image_to_publish(image_paths[0], publish_dir)
            card_result["selected_path"] = selected
            card_result["selection_mode"] = "direct"
            print(f"{card_result['group_name']} 数量=1，跳过豆包评分，直接入 publish：{selected}")
        elif len(image_paths) > 1:
            need_score_page_types.add(page_type)

    # 对数量>1的分组按页型评分
    for page_type in sorted(need_score_page_types):
        try:
            selection = select_publish_images(
                input_dir=run_output_dir,
                include_page_types=(page_type,),
            )
            if page_type == "page01":
                primary_publish_selection = selection
            for card_result in card_results:
                if str(card_result.get("page_type", "")) != page_type:
                    continue
                if str(card_result.get("selected_path", "")).strip():
                    continue
                selected = find_selected_output_path_by_group(selection, str(card_result.get("group_name", "")))
                if selected:
                    card_result["selected_path"] = selected
                    card_result["selection_mode"] = "scored"
                    print(f"{card_result['group_name']} 评分完成，首选图：{selected}")
        except Exception as select_exc:
            print(f"{page_type} 评分失败，跳过自动首选：{select_exc}")

    for card_result in card_results:
        selected = str(card_result.get("selected_path", "")).strip()
        if selected:
            saved_images.append(selected)
        if str(card_result.get("group_name", "")).startswith("图解01_"):
            primary_selected_image = selected
            primary_selection_mode = str(card_result.get("selection_mode", "")).strip()

    if primary_selected_image:
        try:
            cover_template = load_cover_template()
            cover_prompt = render_cover_prompt_by_template(template_text=cover_template, dish_name=dish_name)
            cover_group_key = f"{timestamp}_{sanitize_file_name(dish_name)}封面"
            cover_prompt_path = run_output_dir / f"{cover_group_key}_文生图prompt.txt"
            save_text_output(cover_prompt, cover_prompt_path)
            cover_prompt_file = str(cover_prompt_path)
            print(f"封面提示词已生成：{cover_prompt_file}")

            image_settings = get_image_settings()
            cover_settings = {
                "model": image_settings["model"],
                "size": image_settings["size"],
                "quality": image_settings["quality"],
                "image_count": get_cover_image_count(),
            }
            print(
                "开始生成封面："
                f"model={cover_settings['model']}，size={cover_settings['size']}，"
                f"quality={cover_settings['quality']}，n={cover_settings['image_count']}"
            )
            cover_items = generate_cover_images_from_reference(
                image_client=image_client,
                reference_image_path=primary_selected_image,
                cover_prompt=cover_prompt,
                cover_settings=cover_settings,
            )
            cover_saved_images = save_generated_images(
                image_items=cover_items,
                output_dir=run_output_dir,
                timestamp=timestamp,
                dish_name=f"{dish_name}封面",
            )
            for image_file in cover_saved_images:
                print(f"已保存封面：{image_file}")
        except Exception as cover_exc:
            cover_image_error = f"封面生图失败：{cover_exc}"
            save_text_output(
                "封面生成失败，本轮主图首选流程已完成。\n"
                f"失败原因：{cover_exc}",
                run_output_dir / "封面生图失败原因.txt",
            )
            print(cover_image_error)

    if cover_saved_images:
        if len(cover_saved_images) == 1:
            cover_selected_image = move_image_to_publish(cover_saved_images[0], publish_dir)
            cover_selection_mode = "direct"
            cover_saved_images = [cover_selected_image]
            print(f"封面数量=1，跳过豆包评分，直接入 publish：{cover_selected_image}")
        else:
            try:
                print("开始进行封面评分并选入 publish ...")
                cover_publish_selection = select_publish_images(
                    input_dir=run_output_dir,
                    include_page_types=("cover",),
                )
                cover_selected_image = find_selected_output_path(cover_publish_selection, page_type="cover")
                if cover_selected_image:
                    cover_selection_mode = "scored"
                    cover_saved_images = remap_selected_image_path(cover_saved_images, cover_selected_image)
                    print(f"封面评分完成，首选封面：{cover_selected_image}")
            except Exception as cover_select_exc:
                print(f"封面评分失败，跳过自动首选：{cover_select_exc}")

    selected_for_publish = [path for path in saved_images if path]
    if cover_selected_image:
        selected_for_publish.append(cover_selected_image)
    if len(selected_for_publish) >= 2:
        try:
            print("publish 已有主图与封面首选，开始执行 Photoshop 覆盖合成...")
            processed_file_map = apply_photoshop_template_batch_to_dir(input_dir=publish_dir)
            photoshop_processed_files = list(processed_file_map.values())
            print(f"Photoshop 合成完成：{len(photoshop_processed_files)} 张")
        except Exception as ps_exc:
            photoshop_error = f"Photoshop 合成失败：{ps_exc}"
            print(photoshop_error)

    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    return {
        "dish_name": dish_name,
        "notes": notes,
        "region_label": dish_payload.get("region_label", ""),
        "reference_dish": dish_payload.get("reference_dish", ""),
        "memory_file": dish_payload.get("memory_file", ""),
        "prompt_file": str(script_file),
        "output_dir": str(run_output_dir),
        "saved_images": saved_images,
        "image_error": image_error,
        "publish_dir": str(publish_dir),
        "primary_publish_selection": primary_publish_selection,
        "primary_selection_mode": primary_selection_mode,
        "primary_selected_image": primary_selected_image,
        "cover_prompt_file": cover_prompt_file,
        "cover_saved_images": cover_saved_images,
        "cover_image_error": cover_image_error,
        "cover_publish_selection": cover_publish_selection,
        "cover_selection_mode": cover_selection_mode,
        "cover_selected_image": cover_selected_image,
        "photoshop_processed_files": photoshop_processed_files,
        "photoshop_error": photoshop_error,
        "card_results": card_results,
        "content_track": script_payload.get("content_track", content_track),
        "caption": script_payload.get("caption", ""),
        "hashtags": script_payload.get("hashtags", []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V2 首功能：自动/手动菜名 -> 模板重写 -> gpt-image-2 生图")
    parser.add_argument("--mode", choices=["auto", "file"], default=None, help="auto 自动造菜；file 读取 V2/dish_name.txt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("开始执行 V2 第一个功能：自动/手动造菜 -> 豆包生成提示词 -> gpt-image-2 生图")
    try:
        result = run_v2_first_feature(mode=args.mode)
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("V2 第一个功能执行完成。")
    print(f"菜名：{result['dish_name']}")
    if result.get("region_label"):
        print(f"菜系范围：{result['region_label']}")
    if result.get("reference_dish"):
        print(f"参考传统菜：{result['reference_dish']}")
    if result.get("memory_file"):
        print(f"历史记忆文件：{result['memory_file']}")
    print(f"输出目录：{result['output_dir']}")
    print(f"提示词文件：{result['prompt_file']}")
    if result.get("image_error"):
        print(f"生图状态：降级完成（{result['image_error']}）")
    elif result.get("saved_images"):
        print(f"生图状态：成功，共 {len(result['saved_images'])} 张")
    else:
        print("生图状态：未返回图片，但流程已完成。")
    if result.get("primary_selected_image"):
        print(f"主图首选：{result['primary_selected_image']}")
    if result.get("cover_selected_image"):
        print(f"封面首选：{result['cover_selected_image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
