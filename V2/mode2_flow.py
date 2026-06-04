from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from v2_core import (
    CAIPU_TEMPLATE_FILE,
    CHARACTER_REFERENCE_FILE,
    FENGMIAN_TEMPLATE_FILE,
    HAIBAO_TEMPLATE_FILE,
    IDEA_FILE,
    XIJIETU_TEMPLATE_FILE,
    auto_generate_dish_idea,
    build_doubao_client,
    build_openai_image_client,
    build_run_output_dir,
    generate_cankao_prompt_by_template,
    generate_cankao_prompt_with_images,
    generate_images_by_prompt,
    generate_images_from_references,
    generate_poster_bubble_copy,
    get_mode2_group_settings,
    get_timestamp,
    load_cankao_group_template,
    load_manual_dish_idea,
    move_image_to_publish,
    parse_bool_env,
    save_generated_images,
    save_text_output,
    select_douyin_poster_image,
    write_dish_idea_file,
)


def resolve_auto_generate_enabled(mode: str | None) -> bool:
    if mode == "auto":
        return True
    if mode == "file":
        return False
    return parse_bool_env("AUTO_GENERATE_DISH_IDEA", default=False)


def prepare_dish_payload(mode: str | None, doubao_client) -> dict[str, str]:
    auto_generate_enabled = resolve_auto_generate_enabled(mode)
    raw_auto_flag = os.getenv("AUTO_GENERATE_DISH_IDEA", "").strip()
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
    return dish_payload


def generate_group_images(
    *,
    image_client,
    prompt_text: str,
    reference_paths: list[str | Path],
    settings: dict[str, Any],
    output_dir: Path,
    timestamp: str,
    dish_name: str,
    name_suffix: str,
    stage_label: str,
) -> tuple[list[str], str]:
    image_error = ""
    saved_images: list[str] = []
    try:
        if reference_paths:
            image_items = generate_images_from_references(
                client=image_client,
                prompt_text=prompt_text,
                reference_paths=reference_paths,
                settings=settings,
            )
        else:
            image_items = generate_images_by_prompt(
                client=image_client,
                prompt_text=prompt_text,
                settings=settings,
            )
        saved_images = save_generated_images(
            image_items=image_items,
            output_dir=output_dir,
            timestamp=timestamp,
            dish_name=dish_name,
            name_suffix=name_suffix,
        )
        for image_file in saved_images:
            print(f"已保存{stage_label}：{image_file}")
    except Exception as image_exc:
        image_error = f"{stage_label}生图失败：{image_exc}"
        save_text_output(
            f"{stage_label}生图失败。\n失败原因：{image_exc}",
            output_dir / f"{stage_label}生图失败原因.txt",
        )
        print(image_error)
    return saved_images, image_error


def run_v2_mode2(mode: str | None = None) -> dict[str, object]:
    from v2_core import ensure_runtime_config_loaded

    ensure_runtime_config_loaded()
    doubao_client = build_doubao_client()
    image_client = build_openai_image_client()

    dish_payload = prepare_dish_payload(mode, doubao_client)
    dish_name = dish_payload["dish_name"]
    notes = dish_payload.get("notes", "")
    timestamp = get_timestamp()
    run_output_dir = build_run_output_dir(timestamp, dish_name)
    publish_dir = run_output_dir / "publish"
    print(f"输出目录：{run_output_dir}")
    print("流程模式：mode2（海报 -> 细节图 -> 菜谱图 -> 封面图）")

    errors: list[str] = []
    all_saved_images: list[str] = []

    # 1) 海报提示词 + 生图
    poster_prompt_file = ""
    poster_saved_images: list[str] = []
    poster_selected_image = ""
    poster_selection_mode = ""
    poster_selection_result: dict[str, Any] = {}
    haibao_template = load_cankao_group_template(HAIBAO_TEMPLATE_FILE)
    try:
        poster_prompt_result = generate_cankao_prompt_by_template(
            client=doubao_client,
            dish_name=dish_name,
            notes=notes,
            template_text=haibao_template,
        )
    except Exception as exc:
        raise RuntimeError(f"海报模板改写失败：{exc}") from exc

    poster_prompt_path = run_output_dir / f"{dish_name}_海报_文生图prompt.txt"
    save_text_output(poster_prompt_result["prompt"], poster_prompt_path)
    poster_prompt_file = str(poster_prompt_path)
    print(f"海报提示词已生成：{poster_prompt_file}")

    poster_settings = get_mode2_group_settings("poster")
    print(
        f"开始生成海报：quality={poster_settings['quality']}，n={poster_settings['image_count']}，"
        f"model={poster_settings['model']}"
    )
    poster_saved_images, poster_error = generate_group_images(
        image_client=image_client,
        prompt_text=poster_prompt_result["prompt"],
        reference_paths=[],
        settings=poster_settings,
        output_dir=run_output_dir,
        timestamp=timestamp,
        dish_name=dish_name,
        name_suffix="海报",
        stage_label="海报图",
    )
    if poster_error:
        errors.append(poster_error)
    all_saved_images.extend(poster_saved_images)

    if poster_saved_images:
        if len(poster_saved_images) == 1:
            poster_selected_image = move_image_to_publish(poster_saved_images[0], publish_dir)
            poster_selection_mode = "direct"
            poster_saved_images = [poster_selected_image]
            print(f"海报数量=1，直接入 publish：{poster_selected_image}")
        else:
            try:
                poster_paths = [Path(path) for path in poster_saved_images]
                poster_selection_result = select_douyin_poster_image(doubao_client, poster_paths)
                winner_index = int(poster_selection_result.get("winner_index", 1))
                winner_path = poster_paths[max(0, min(winner_index - 1, len(poster_paths) - 1))]
                poster_selected_image = move_image_to_publish(str(winner_path), publish_dir)
                poster_selection_mode = "scored"
                poster_saved_images = [poster_selected_image]
                save_text_output(
                    json_dumps_safe(poster_selection_result),
                    publish_dir / "海报筛选结果.json",
                )
                print(f"海报筛选完成：{poster_selected_image}，理由：{poster_selection_result.get('winner_reason', '')}")
            except Exception as select_exc:
                poster_selected_image = move_image_to_publish(poster_saved_images[0], publish_dir)
                poster_selection_mode = "fallback_direct"
                poster_saved_images = [poster_selected_image]
                errors.append(f"海报筛选失败，已回退首图：{select_exc}")
                print(f"海报筛选失败，回退直通首图：{poster_selected_image}")

    bubble_text_file = ""
    detail_prompt_file = ""
    detail_saved_images: list[str] = []
    recipe_prompt_file = ""
    recipe_saved_images: list[str] = []
    cover_prompt_file = ""
    cover_saved_images: list[str] = []

    if poster_selected_image:
        poster_path = Path(poster_selected_image)
        bubble_text = ""

        # 2A) 气泡文案
        try:
            bubble_result = generate_poster_bubble_copy(doubao_client, poster_path)
            bubble_path = run_output_dir / f"{dish_name}_气泡文案.txt"
            save_text_output(bubble_result["content"], bubble_path)
            bubble_text_file = str(bubble_path)
            print(f"气泡文案已生成：{bubble_text_file}")
            bubble_text = bubble_result["content"]
        except Exception as bubble_exc:
            bubble_text = ""
            errors.append(f"气泡文案失败：{bubble_exc}")
            print(f"气泡文案失败：{bubble_exc}")

        # 2B/C) 细节图
        if not CHARACTER_REFERENCE_FILE.exists():
            errors.append(f"角色参考图不存在：{CHARACTER_REFERENCE_FILE}")
        else:
            xijietu_template = load_cankao_group_template(XIJIETU_TEMPLATE_FILE)
            try:
                detail_prompt_result = generate_cankao_prompt_with_images(
                    client=doubao_client,
                    dish_name=dish_name,
                    notes=notes,
                    template_text=xijietu_template,
                    image_paths=[poster_path, CHARACTER_REFERENCE_FILE],
                    bubble_text=bubble_text,
                    stage_name="细节图模板",
                )
                detail_prompt_path = run_output_dir / f"{dish_name}_细节图_文生图prompt.txt"
                save_text_output(detail_prompt_result["prompt"], detail_prompt_path)
                detail_prompt_file = str(detail_prompt_path)
                print(f"细节图提示词已生成：{detail_prompt_file}")

                detail_settings = get_mode2_group_settings("detail")
                detail_saved_images, detail_error = generate_group_images(
                    image_client=image_client,
                    prompt_text=detail_prompt_result["prompt"],
                    reference_paths=[poster_selected_image, str(CHARACTER_REFERENCE_FILE)],
                    settings=detail_settings,
                    output_dir=run_output_dir,
                    timestamp=timestamp,
                    dish_name=dish_name,
                    name_suffix="细节图",
                    stage_label="细节图",
                )
                if detail_error:
                    errors.append(detail_error)
                all_saved_images.extend(detail_saved_images)
            except Exception as detail_exc:
                errors.append(f"细节图流程失败：{detail_exc}")
                print(f"细节图流程失败：{detail_exc}")

        # 3) 菜谱图
        caipu_template = load_cankao_group_template(CAIPU_TEMPLATE_FILE)
        try:
            recipe_prompt_result = generate_cankao_prompt_by_template(
                client=doubao_client,
                dish_name=dish_name,
                notes=notes,
                template_text=caipu_template,
            )
            recipe_prompt_path = run_output_dir / f"{dish_name}_菜谱图_文生图prompt.txt"
            save_text_output(recipe_prompt_result["prompt"], recipe_prompt_path)
            recipe_prompt_file = str(recipe_prompt_path)
            print(f"菜谱图提示词已生成：{recipe_prompt_file}")

            recipe_settings = get_mode2_group_settings("recipe")
            recipe_saved_images, recipe_error = generate_group_images(
                image_client=image_client,
                prompt_text=recipe_prompt_result["prompt"],
                reference_paths=[poster_selected_image],
                settings=recipe_settings,
                output_dir=run_output_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                name_suffix="菜谱图",
                stage_label="菜谱图",
            )
            if recipe_error:
                errors.append(recipe_error)
            all_saved_images.extend(recipe_saved_images)
        except Exception as recipe_exc:
            errors.append(f"菜谱图流程失败：{recipe_exc}")
            print(f"菜谱图流程失败：{recipe_exc}")

        # 4) 封面图
        fengmian_template = load_cankao_group_template(FENGMIAN_TEMPLATE_FILE)
        try:
            cover_prompt_result = generate_cankao_prompt_by_template(
                client=doubao_client,
                dish_name=dish_name,
                notes=notes,
                template_text=fengmian_template,
            )
            cover_prompt_path = run_output_dir / f"{dish_name}_封面图_文生图prompt.txt"
            save_text_output(cover_prompt_result["prompt"], cover_prompt_path)
            cover_prompt_file = str(cover_prompt_path)
            print(f"封面图提示词已生成：{cover_prompt_file}")

            cover_settings = get_mode2_group_settings("cover")
            cover_saved_images, cover_error = generate_group_images(
                image_client=image_client,
                prompt_text=cover_prompt_result["prompt"],
                reference_paths=[poster_selected_image],
                settings=cover_settings,
                output_dir=run_output_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                name_suffix="封面图",
                stage_label="封面图",
            )
            if cover_error:
                errors.append(cover_error)
            all_saved_images.extend(cover_saved_images)
        except Exception as cover_exc:
            errors.append(f"封面图流程失败：{cover_exc}")
            print(f"封面图流程失败：{cover_exc}")

    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    image_error = "\n".join(errors).strip()
    return {
        "workflow_mode": "mode2",
        "dish_name": dish_name,
        "notes": notes,
        "region_label": dish_payload.get("region_label", ""),
        "reference_dish": dish_payload.get("reference_dish", ""),
        "memory_file": dish_payload.get("memory_file", ""),
        "output_dir": str(run_output_dir),
        "publish_dir": str(publish_dir),
        "poster_prompt_file": poster_prompt_file,
        "poster_saved_images": poster_saved_images,
        "poster_selected_image": poster_selected_image,
        "poster_selection_mode": poster_selection_mode,
        "poster_selection_result": poster_selection_result,
        "bubble_text_file": bubble_text_file,
        "detail_prompt_file": detail_prompt_file,
        "detail_saved_images": detail_saved_images,
        "recipe_prompt_file": recipe_prompt_file,
        "recipe_saved_images": recipe_saved_images,
        "cover_prompt_file": cover_prompt_file,
        "cover_saved_images": cover_saved_images,
        "saved_images": all_saved_images,
        "image_error": image_error,
        # 兼容模式1前端字段
        "prompt_file": poster_prompt_file,
        "primary_selected_image": poster_selected_image,
        "primary_selection_mode": poster_selection_mode,
        "cover_selected_image": cover_saved_images[0] if cover_saved_images else "",
        "cover_saved_images": cover_saved_images,
        "cover_image_error": "",
    }


def json_dumps_safe(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
