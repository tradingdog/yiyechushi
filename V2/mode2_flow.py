from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from v2_core import (
    AllCandidatesDefectiveError,
    CAIPU_TEMPLATE_FILE,
    CHARACTER_REFERENCE_FILE,
    snapshot_mode2_image_settings,
    FENGMIAN_TEMPLATE_FILE,
    HAIBAO_TEMPLATE_FILE,
    IDEA_FILE,
    XIJIETU_TEMPLATE_FILE,
    auto_generate_dish_idea,
    build_openai_image_client,
    build_run_output_dir,
    build_text_client,
    dedupe_archive_duplicate_dish_folders,
    format_text_runtime_label,
    generate_cankao_prompt_by_template,
    generate_cankao_prompt_with_images,
    generate_haibao_prompt_by_template,
    MAX_POSTER_INGREDIENT_REFERENCE_COUNT,
    POSTER_INGREDIENT_REFERENCE_HINT,
    generate_images_by_prompt,
    generate_images_from_references,
    generate_poster_bubble_copy,
    get_mode2_group_settings,
    get_timestamp,
    is_moderation_blocked_error,
    persist_v2_dish_record,
    persist_v2_publish_copy_assets,
    load_cankao_group_template,
    load_dish_idea_record_from_dir,
    load_manual_dish_idea,
    parse_bool_env,
    save_generated_images,
    save_text_output,
    select_and_publish_image_group,
    soften_detail_image_prompt,
    finalize_mode2_image_prompt,
    write_dish_idea_file,
)

from publish_final_assets import PHOTOSHOP_SLOT_SPECS, find_existing_final_for_slot


def resolve_auto_generate_enabled(mode: str | None) -> bool:
    if mode == "auto":
        return True
    if mode in {"file", "target"}:
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
    cuisine_mode = os.getenv("AUTO_DISH_CUISINE_MODE", "1").strip() or "1"
    print(f"自动造菜菜系模式：AUTO_DISH_CUISINE_MODE={cuisine_mode}")
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


def build_detail_reference_paths(poster_selected_image: str) -> list[str]:
    poster_path = str(poster_selected_image or "").strip()
    if not poster_path:
        raise RuntimeError("细节图生图缺少海报参考图。")
    if not Path(poster_path).exists():
        raise RuntimeError(f"细节图生图海报参考图不存在：{poster_path}")
    if not CHARACTER_REFERENCE_FILE.exists():
        raise RuntimeError(f"角色参考图不存在：{CHARACTER_REFERENCE_FILE}")
    refs = [poster_path, str(CHARACTER_REFERENCE_FILE.resolve())]
    print(f"细节图生图参考图：海报={refs[0]}，角色={refs[1]}")
    return refs


def build_image_generation_attempts(
    *,
    prompt_text: str,
    reference_paths: list[str | Path],
    moderation_fallback: bool,
) -> list[tuple[str, list[str | Path]]]:
    full_refs = list(reference_paths)
    attempts: list[tuple[str, list[str | Path]]] = [(prompt_text, full_refs)]
    if not moderation_fallback:
        return attempts

    poster_only_refs = [
        path
        for path in reference_paths
        if "juese" not in Path(str(path)).name.lower()
    ]
    softened_prompt = soften_detail_image_prompt(prompt_text)
    if softened_prompt != prompt_text:
        attempts.append((softened_prompt, full_refs))
    if poster_only_refs and poster_only_refs != full_refs:
        attempts.append((softened_prompt if softened_prompt != prompt_text else prompt_text, list(poster_only_refs)))
    return attempts


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
    moderation_fallback: bool = False,
) -> tuple[list[str], str]:
    prompt_text = finalize_mode2_image_prompt(prompt_text, stage_label=stage_label)
    image_error = ""
    saved_images: list[str] = []
    attempts = build_image_generation_attempts(
        prompt_text=prompt_text,
        reference_paths=reference_paths,
        moderation_fallback=moderation_fallback,
    )
    last_exc: Exception | None = None
    for attempt_idx, (attempt_prompt, attempt_refs) in enumerate(attempts):
        try:
            if attempt_refs:
                image_items = generate_images_from_references(
                    client=image_client,
                    prompt_text=attempt_prompt,
                    reference_paths=attempt_refs,
                    settings=settings,
                )
            else:
                image_items = generate_images_by_prompt(
                    client=image_client,
                    prompt_text=attempt_prompt,
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
            if attempt_idx > 0:
                print(f"{stage_label}已通过降级策略生成（策略 {attempt_idx + 1}/{len(attempts)}）")
            return saved_images, ""
        except Exception as image_exc:
            last_exc = image_exc
            can_retry = (
                moderation_fallback
                and is_moderation_blocked_error(image_exc)
                and attempt_idx < len(attempts) - 1
            )
            if can_retry:
                print(
                    f"{stage_label}内容审核拦截，正在降级重试"
                    f"（{attempt_idx + 2}/{len(attempts)}）..."
                )
                continue
            image_error = f"{stage_label}生图失败：{image_exc}"
            save_text_output(
                f"{stage_label}生图失败。\n失败原因：{image_exc}",
                output_dir / f"{stage_label}生图失败原因.txt",
            )
            print(image_error)
            break
    if last_exc is not None and not image_error:
        image_error = f"{stage_label}生图失败：{last_exc}"
    return saved_images, image_error


def select_publish_group_with_defect_regeneration(
    doubao_client,
    *,
    publish_dir: Path,
    candidate_paths: list[str],
    image_kind: str,
    selection_report_name: str,
    regenerate_fn=None,
    max_regenerate_rounds: int = 1,
) -> tuple[str, str, dict[str, Any]]:
    """豆包缺陷审核选图；若全部不合格则重新生图后再选。"""
    paths = list(candidate_paths)
    for round_idx in range(max_regenerate_rounds + 1):
        try:
            return select_and_publish_image_group(
                doubao_client,
                publish_dir=publish_dir,
                candidate_paths=paths,
                image_kind=image_kind,
                selection_report_name=selection_report_name,
            )
        except AllCandidatesDefectiveError as exc:
            if regenerate_fn is None or round_idx >= max_regenerate_rounds:
                raise
            print(f"{image_kind}候选均含明显缺陷，开始第 {round_idx + 1} 次重新生图…")
            new_paths = regenerate_fn()
            if not new_paths:
                raise RuntimeError(f"{image_kind}重新生图未返回新候选。") from exc
            paths = list(new_paths)
    raise RuntimeError(f"{image_kind}选图失败。")


def persist_poster_ingredient_references(
    run_output_dir: Path,
    dish_name: str,
    reference_paths: list[Path],
) -> list[str]:
    saved: list[str] = []
    for index, ref_path in enumerate(reference_paths[:MAX_POSTER_INGREDIENT_REFERENCE_COUNT], start=1):
        suffix = ref_path.suffix or ".png"
        dest = run_output_dir / f"{dish_name}_海报参考图_{index:02d}{suffix}"
        shutil.copy2(ref_path, dest)
        saved.append(str(dest))
        print(f"已存档海报参考图：{dest}")
    return saved


def run_v2_mode2(
    mode: str | None = None,
    *,
    target_output_dir: str | Path | None = None,
    poster_ingredient_reference_paths: list[str | Path] | None = None,
) -> dict[str, object]:
    from v2_core import ensure_runtime_config_loaded

    ensure_runtime_config_loaded()
    doubao_client = build_text_client()
    image_client = build_openai_image_client()
    print(format_text_runtime_label())

    if mode == "target" or target_output_dir:
        run_output_dir = Path(target_output_dir).resolve()
        dish_payload = load_dish_idea_record_from_dir(run_output_dir)
        write_dish_idea_file(dish_payload["dish_name"], dish_payload.get("notes", ""), idea_file=IDEA_FILE)
        print(f"指定造菜：复用已有目录 {run_output_dir}")
        print(f"造菜信息来源：{dish_payload.get('record_file', '')}")
    else:
        dish_payload = prepare_dish_payload(mode, doubao_client)
        timestamp = get_timestamp()
        run_output_dir = build_run_output_dir(timestamp, dish_payload["dish_name"])
    dish_name = dish_payload["dish_name"]
    notes = dish_payload.get("notes", "")
    timestamp = get_timestamp()
    publish_dir = run_output_dir / "publish"
    print(f"输出目录：{run_output_dir}")
    print("流程：海报 -> 细节图 -> 菜谱图 -> 封面图")

    dish_idea_record_file = persist_v2_dish_record(run_output_dir, dish_payload)
    publish_copy_assets: dict[str, Any] = {}

    errors: list[str] = []
    all_saved_images: list[str] = []

    # 1) 海报提示词 + 生图
    poster_prompt_file = ""
    poster_saved_images: list[str] = []
    poster_selected_image = ""
    poster_selection_mode = ""
    poster_selection_result: dict[str, Any] = {}
    poster_reference_paths = [
        Path(item).resolve()
        for item in (poster_ingredient_reference_paths or [])
        if str(item).strip() and Path(item).is_file()
    ][:MAX_POSTER_INGREDIENT_REFERENCE_COUNT]
    if poster_reference_paths:
        persist_poster_ingredient_references(run_output_dir, dish_name, poster_reference_paths)
        print(
            f"海报主食材参考图：{len(poster_reference_paths)} 张，"
            f"提示词将注入「{POSTER_INGREDIENT_REFERENCE_HINT}」"
        )

    haibao_template = load_cankao_group_template(HAIBAO_TEMPLATE_FILE)
    try:
        poster_prompt_result = generate_haibao_prompt_by_template(
            client=doubao_client,
            dish_name=dish_name,
            notes=notes,
            template_text=haibao_template,
            ingredient_reference_paths=poster_reference_paths,
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
    if poster_reference_paths:
        print("海报参考图生图输入：" + "，".join(str(path) for path in poster_reference_paths))
    poster_saved_images, poster_error = generate_group_images(
        image_client=image_client,
        prompt_text=poster_prompt_result["prompt"],
        reference_paths=poster_reference_paths,
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

    detail_selection_mode = ""
    detail_selection_result: dict[str, Any] = {}
    detail_selected_image = ""
    recipe_selection_mode = ""
    recipe_selection_result: dict[str, Any] = {}
    recipe_selected_image = ""
    cover_selection_mode = ""
    cover_selection_result: dict[str, Any] = {}
    cover_selected_image = ""

    if poster_saved_images:
        poster_selected_image, poster_selection_mode, poster_selection_result = publish_image_group_safe(
            doubao_client,
            publish_dir=publish_dir,
            candidate_paths=poster_saved_images,
            image_kind="图文海报",
            selection_report_name="海报筛选结果.json",
            errors=errors,
        )
        if poster_selected_image:
            poster_saved_images = [poster_selected_image]

    if poster_selected_image:
        publish_copy_assets = persist_v2_publish_copy_assets(
            client=doubao_client,
            output_dir=run_output_dir,
            timestamp=timestamp,
            dish_payload=dish_payload,
            poster_image_path=poster_selected_image,
        )
        publish_copy_error = str(publish_copy_assets.get("publish_copy_error", "")).strip()
        if publish_copy_error:
            errors.append(f"平台文案生成失败：{publish_copy_error}")

    bubble_text_file = ""
    detail_prompt_file = ""
    detail_saved_images: list[str] = []
    detail_candidate_images: list[str] = []
    recipe_prompt_file = ""
    recipe_saved_images: list[str] = []
    recipe_candidate_images: list[str] = []
    cover_prompt_file = ""
    cover_saved_images: list[str] = []
    cover_candidate_images: list[str] = []

    if poster_selected_image:
        poster_path = Path(poster_selected_image)
        bubble_text = ""

        # 2A) 气泡文案
        try:
            bubble_path = run_output_dir / f"{dish_name}_气泡文案.txt"
            bubble_result = generate_poster_bubble_copy(
                doubao_client,
                poster_path,
                dish_name=dish_name,
                notes=notes,
                current_bubble_file=bubble_path,
            )
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
                    reference_paths=build_detail_reference_paths(poster_selected_image),
                    settings=detail_settings,
                    output_dir=run_output_dir,
                    timestamp=timestamp,
                    dish_name=dish_name,
                    name_suffix="细节图",
                    stage_label="细节图",
                    moderation_fallback=True,
                )
                if detail_error:
                    errors.append(detail_error)
                all_saved_images.extend(detail_saved_images)
                detail_candidate_images = list(detail_saved_images)
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
            recipe_candidate_images = list(recipe_saved_images)
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
            cover_candidate_images = list(cover_saved_images)
        except Exception as cover_exc:
            errors.append(f"封面图流程失败：{cover_exc}")
            print(f"封面图流程失败：{cover_exc}")

    if detail_candidate_images:
        detail_selected_image, detail_selection_mode, detail_selection_result = publish_image_group_safe(
            doubao_client,
            publish_dir=publish_dir,
            candidate_paths=detail_candidate_images,
            image_kind="细节图",
            selection_report_name="细节图筛选结果.json",
            errors=errors,
        )

    if recipe_candidate_images:
        recipe_selected_image, recipe_selection_mode, recipe_selection_result = publish_image_group_safe(
            doubao_client,
            publish_dir=publish_dir,
            candidate_paths=recipe_candidate_images,
            image_kind="菜谱图",
            selection_report_name="菜谱图筛选结果.json",
            errors=errors,
        )

    if cover_candidate_images:
        cover_selected_image, cover_selection_mode, cover_selection_result = publish_image_group_safe(
            doubao_client,
            publish_dir=publish_dir,
            candidate_paths=cover_candidate_images,
            image_kind="封面图",
            selection_report_name="封面图筛选结果.json",
            errors=errors,
        )

    photoshop_processed_files: list[str] = []
    photoshop_error = ""
    publish_final_dir = publish_dir / "final"
    publish_selected_for_ps = [
        path
        for path in [poster_selected_image, detail_selected_image, recipe_selected_image, cover_selected_image]
        if path
    ]
    if publish_selected_for_ps:
        try:
            photoshop_processed_files = rerun_photoshop_for_publish_dir(
                publish_dir=publish_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                poster_source=poster_selected_image,
                detail_source=detail_selected_image,
                recipe_source=recipe_selected_image,
                cover_source=cover_selected_image,
            )
            print(f"Photoshop 合成完成：{len(photoshop_processed_files)} 张 -> publish/final（已加 01-04 序列号）")
        except Exception as ps_exc:
            photoshop_error = f"Photoshop 合成失败：{ps_exc}"
            save_text_output(
                f"Photoshop 合成失败。\n失败原因：{ps_exc}",
                run_output_dir / "Photoshop合成失败原因.txt",
            )
            errors.append(photoshop_error)
            print(photoshop_error)

    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    is_new_output_dir = not (mode == "target" or target_output_dir)
    if is_new_output_dir and dish_name.strip():
        try:
            archived = dedupe_archive_duplicate_dish_folders(dish_name, keep_dir=run_output_dir)
            if archived:
                print(
                    f"菜品去重完成：保留最新目录 {run_output_dir.name}，"
                    f"已归档 {len(archived)} 个同名旧目录。"
                )
        except Exception as dedupe_exc:
            print(f"菜品去重失败：{dedupe_exc}")

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
        "detail_selected_image": detail_selected_image,
        "detail_selection_mode": detail_selection_mode,
        "detail_selection_result": detail_selection_result,
        "recipe_prompt_file": recipe_prompt_file,
        "recipe_saved_images": recipe_saved_images,
        "recipe_selected_image": recipe_selected_image,
        "recipe_selection_mode": recipe_selection_mode,
        "recipe_selection_result": recipe_selection_result,
        "cover_prompt_file": cover_prompt_file,
        "cover_saved_images": cover_saved_images,
        "cover_selected_image": cover_selected_image,
        "cover_selection_mode": cover_selection_mode,
        "cover_selection_result": cover_selection_result,
        "publish_final_dir": str(publish_final_dir),
        "photoshop_processed_files": photoshop_processed_files,
        "photoshop_error": photoshop_error,
        "saved_images": all_saved_images,
        "image_error": image_error,
        # 兼容模式1前端字段
        "prompt_file": poster_prompt_file,
        "primary_selected_image": poster_selected_image,
        "primary_selection_mode": poster_selection_mode,
        "cover_image_error": "",
        "dish_idea_record_file": dish_idea_record_file,
        "publish_title_file": publish_copy_assets.get("publish_title_file", ""),
        "publish_description_file": publish_copy_assets.get("publish_description_file", ""),
        "publish_description_body_file": publish_copy_assets.get("publish_description_body_file", ""),
        "publish_platform_topic_files": publish_copy_assets.get("publish_platform_topic_files", {}),
        "publish_platform_description_files": publish_copy_assets.get("publish_platform_description_files", {}),
        "publish_copy_prompt_file": publish_copy_assets.get("publish_copy_prompt_file", ""),
        "publish_copy_error": str(publish_copy_assets.get("publish_copy_error", "")).strip(),
        "image_generation_settings": snapshot_mode2_image_settings(),
    }


_STAGE_IMAGE_SUFFIXES = {
    "poster": "海报",
    "detail": "细节图",
    "recipe": "菜谱图",
    "cover": "封面图",
}


def collect_existing_stage_images(run_output_dir: Path, name_suffix: str) -> list[str]:
    """收集输出目录根下已生成、尚未入 publish 的阶段图。"""
    publish_dir = run_output_dir / "publish"
    found: list[str] = []
    for pattern in (f"*_{name_suffix}_*.png", f"*_{name_suffix}_*.jpg", f"*_{name_suffix}_*.jpeg"):
        for path in sorted(run_output_dir.glob(pattern)):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if publish_dir in path.parents:
                continue
            found.append(resolved)
    return found


def publish_image_group_safe(
    client,
    *,
    publish_dir: Path,
    candidate_paths: list[str],
    image_kind: str,
    selection_report_name: str,
    errors: list[str],
) -> tuple[str, str, dict[str, Any]]:
    try:
        return select_and_publish_image_group(
            client,
            publish_dir=publish_dir,
            candidate_paths=candidate_paths,
            image_kind=image_kind,
            selection_report_name=selection_report_name,
        )
    except AllCandidatesDefectiveError as exc:
        paths = [str(path) for path in candidate_paths if str(path).strip() and Path(path).is_file()]
        if not paths:
            errors.append(str(exc))
            print(str(exc))
            return "", "", {}
        from v2_core import move_image_to_publish

        selected = move_image_to_publish(paths[0], publish_dir)
        msg = f"{image_kind}缺陷审核全部剔除，已回退首张入 publish：{exc}"
        errors.append(msg)
        print(msg)
        return (
            selected,
            "fallback_defect",
            {
                "auto_selected": True,
                "winner_index": 1,
                "winner_image_name": Path(paths[0]).name,
                "winner_reason": msg,
            },
        )
    except Exception as exc:
        paths = [str(path) for path in candidate_paths if str(path).strip() and Path(path).is_file()]
        if not paths:
            errors.append(f"{image_kind}选图失败：{exc}")
            print(f"{image_kind}选图失败：{exc}")
            return "", "", {}
        from v2_core import move_image_to_publish

        selected = move_image_to_publish(paths[0], publish_dir)
        msg = f"{image_kind}选图失败，已回退首张：{exc}"
        errors.append(msg)
        print(msg)
        return (
            selected,
            "fallback_direct",
            {
                "auto_selected": True,
                "winner_index": 1,
                "winner_image_name": Path(paths[0]).name,
                "winner_reason": msg,
            },
        )


def find_publish_image_by_kind(publish_dir: Path, kind_keyword: str) -> str:
    matches = sorted(
        path
        for path in publish_dir.iterdir()
        if path.is_file() and kind_keyword in path.stem and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    return str(matches[0].resolve()) if matches else ""


def extract_timestamp_from_publish_name(path_text: str) -> str:
    import re

    match = re.search(r"(\d{8}_\d{6})_", Path(path_text).name)
    return match.group(1) if match else get_timestamp()


def rerun_photoshop_for_publish_dir(
    *,
    publish_dir: Path,
    timestamp: str,
    dish_name: str,
    poster_source: str,
    detail_source: str,
    recipe_source: str,
    cover_source: str,
    only_kinds: set[str] | None = None,
    force_all: bool = False,
) -> list[str]:
    from tools.apply_photoshop_template_batch import apply_photoshop_template_batch_to_files
    from publish_final_assets import (
        apply_final_image_sequence_names,
        collect_existing_final_paths,
        resolve_photoshop_sources_to_process,
    )

    publish_final_dir = publish_dir / "final"
    sources_to_process, skipped_labels = resolve_photoshop_sources_to_process(
        publish_final_dir=publish_final_dir,
        poster_source=poster_source,
        detail_source=detail_source,
        recipe_source=recipe_source,
        cover_source=cover_source,
        only_kinds=only_kinds,
        force_all=force_all,
    )
    if not sources_to_process:
        existing_finals = collect_existing_final_paths(
            publish_final_dir=publish_final_dir,
            poster_source=poster_source,
            detail_source=detail_source,
            recipe_source=recipe_source,
            cover_source=cover_source,
        )
        if skipped_labels:
            print(f"无需 Photoshop 合成，已跳过：{', '.join(skipped_labels)}。")
        return existing_finals

    print(
        f"本次待 Photoshop 合成 {len(sources_to_process)} 张"
        f"{'（仅新生成/变更图）' if only_kinds else ''} -> publish/final"
    )
    template_file = os.getenv("PHOTOSHOP_TEMPLATE_FILE", "").strip()
    if template_file:
        print(f"当前 PSD 模板：{template_file}")
    processed_file_map = apply_photoshop_template_batch_to_files(
        sources_to_process,
        output_dir=publish_final_dir,
        template_file=template_file or None,
    )
    return apply_final_image_sequence_names(
        publish_final_dir=publish_final_dir,
        timestamp=timestamp,
        dish_name=dish_name,
        processed_file_map=processed_file_map,
        poster_source=poster_source,
        detail_source=detail_source,
        recipe_source=recipe_source,
        cover_source=cover_source,
    )


def retry_detail_stage_for_output_dir(run_output_dir: str | Path) -> dict[str, object]:
    """仅补跑细节图：生图 -> 豆包筛选 -> 重新 PS 合成四张发布图。"""
    from v2_core import ensure_runtime_config_loaded

    ensure_runtime_config_loaded()
    run_output_dir = Path(run_output_dir).resolve()
    dish_payload = load_dish_idea_record_from_dir(run_output_dir)
    dish_name = dish_payload["dish_name"]
    publish_dir = run_output_dir / "publish"
    if not publish_dir.exists():
        raise RuntimeError(f"publish 目录不存在：{publish_dir}")

    poster_selected_image = find_publish_image_by_kind(publish_dir, "海报")
    recipe_selected_image = find_publish_image_by_kind(publish_dir, "菜谱图")
    cover_selected_image = find_publish_image_by_kind(publish_dir, "封面图")
    if not poster_selected_image:
        raise RuntimeError(f"publish 目录缺少海报图：{publish_dir}")

    timestamp = extract_timestamp_from_publish_name(poster_selected_image)
    detail_prompt_path = run_output_dir / f"{dish_name}_细节图_文生图prompt.txt"
    if not detail_prompt_path.exists():
        raise RuntimeError(f"细节图提示词不存在：{detail_prompt_path}")
    prompt_text = soften_detail_image_prompt(detail_prompt_path.read_text(encoding="utf-8"))
    save_text_output(prompt_text, detail_prompt_path)

    doubao_client = build_text_client()
    image_client = build_openai_image_client()
    print(format_text_runtime_label())
    detail_settings = get_mode2_group_settings("detail")
    print(
        f"补跑细节图：quality={detail_settings['quality']}，"
        f"n={detail_settings['image_count']}，目录={run_output_dir}"
    )
    detail_saved_images, detail_error = generate_group_images(
        image_client=image_client,
        prompt_text=prompt_text,
        reference_paths=build_detail_reference_paths(poster_selected_image),
        settings=detail_settings,
        output_dir=run_output_dir,
        timestamp=timestamp,
        dish_name=dish_name,
        name_suffix="细节图",
        stage_label="细节图",
        moderation_fallback=True,
    )
    if detail_error:
        raise RuntimeError(detail_error)
    if not detail_saved_images:
        raise RuntimeError("细节图生图未返回任何图片。")

    detail_selected_image, detail_selection_mode, detail_selection_result = select_and_publish_image_group(
        doubao_client,
        publish_dir=publish_dir,
        candidate_paths=detail_saved_images,
        image_kind="细节图",
        selection_report_name="细节图筛选结果.json",
    )
    if not detail_selected_image:
        raise RuntimeError("细节图筛选未选出发布图。")

    photoshop_processed_files = rerun_photoshop_for_publish_dir(
        publish_dir=publish_dir,
        timestamp=timestamp,
        dish_name=dish_name,
        poster_source=poster_selected_image,
        detail_source=detail_selected_image,
        recipe_source=recipe_selected_image,
        cover_source=cover_selected_image,
        only_kinds={"detail"},
    )
    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    return {
        "dish_name": dish_name,
        "output_dir": str(run_output_dir),
        "detail_saved_images": detail_saved_images,
        "detail_selected_image": detail_selected_image,
        "detail_selection_mode": detail_selection_mode,
        "detail_selection_result": detail_selection_result,
        "photoshop_processed_files": photoshop_processed_files,
    }


VALID_SUPPLEMENT_TARGETS = frozenset({"poster", "detail", "recipe", "cover", "copy", "photoshop"})

_PUBLISH_KIND_KEYWORDS = (
    ("poster", "海报"),
    ("detail", "细节图"),
    ("recipe", "菜谱图"),
    ("cover", "封面图"),
)


def _folder_has_dish_idea_record(folder: Path) -> bool:
    return any(path.is_file() and "造菜信息" in path.name for path in folder.iterdir())


def _folder_has_publish_copy(folder: Path) -> bool:
    return any(path.is_file() and path.name.endswith("_图文标题.txt") for path in folder.iterdir())


def read_image_failure_hint(folder: Path) -> str:
    patterns = ("*生图失败原因.txt", "*图生图失败原因.txt")
    for pattern in patterns:
        for path in sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if text:
                return text[:500]
    return ""


def infer_continue_supplement_targets(run_output_dir: str | Path) -> dict[str, Any]:
    """推断「余额/网络中断后继续生图」应补生的项目。"""
    folder = Path(run_output_dir).resolve()
    if not folder.is_dir():
        return {
            "can_continue_images": False,
            "continue_supplement_targets": [],
            "image_failure_hint": "",
            "continue_reason": "",
        }

    if not _folder_has_dish_idea_record(folder):
        return {
            "can_continue_images": False,
            "continue_supplement_targets": [],
            "image_failure_hint": read_image_failure_hint(folder),
            "continue_reason": "",
        }

    publish_dir = folder / "publish"
    publish_dir.mkdir(parents=True, exist_ok=True)
    final_dir = publish_dir / "final"
    missing: list[str] = []
    for target, keyword in _PUBLISH_KIND_KEYWORDS:
        if not find_publish_image_by_kind(publish_dir, keyword):
            missing.append(target)

    targets: list[str] = list(missing)
    has_copy = _folder_has_publish_copy(folder)
    if not has_copy:
        targets.append("copy")

    ps_count = len(list(final_dir.glob("*"))) if final_dir.is_dir() else 0
    need_photoshop = bool(missing) or (not missing and ps_count < 4)
    if need_photoshop and "photoshop" not in targets:
        targets.append("photoshop")

    # 四组图 + 文案 + PS 都齐，则无需继续。
    if not missing and has_copy and ps_count >= 4:
        return {
            "can_continue_images": False,
            "continue_supplement_targets": [],
            "image_failure_hint": read_image_failure_hint(folder),
            "continue_reason": "",
        }

    failure_hint = read_image_failure_hint(folder)
    reason_parts: list[str] = []
    if failure_hint:
        reason_parts.append("上次生图未完成")
    elif missing:
        labels = {"poster": "海报", "detail": "细节", "recipe": "菜谱", "cover": "封面"}
        reason_parts.append("缺少" + "、".join(labels[item] for item in missing if item in labels))
    elif not has_copy:
        reason_parts.append("缺少平台文案")
    elif ps_count < 4:
        reason_parts.append("缺少 PS 合成图")
    else:
        reason_parts.append("流程未完成")

    normalized_targets = [item for item in targets if item in VALID_SUPPLEMENT_TARGETS]
    return {
        "can_continue_images": bool(normalized_targets),
        "continue_supplement_targets": normalized_targets,
        "image_failure_hint": failure_hint,
        "continue_reason": "，".join(reason_parts),
    }


def _assert_path_under_dir(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"路径须在目录内：{resolved_root}")


def validate_anchor_poster_path(anchor_poster_path: str | Path, run_output_dir: str | Path) -> Path:
    run_dir = Path(run_output_dir).resolve()
    anchor = Path(anchor_poster_path).resolve()
    if not anchor.is_file():
        raise FileNotFoundError(f"锚点海报不存在：{anchor}")
    _assert_path_under_dir(anchor, run_dir)
    publish_dir = (run_dir / "publish").resolve()
    history_dir = (run_dir / "history").resolve()
    if publish_dir in anchor.parents or anchor.parent == publish_dir:
        raise ValueError("请选择 publish 文件夹外的候选海报（看图栏「非发布图」中的根目录候选图）。")
    if history_dir in anchor.parents or anchor.parent == history_dir:
        raise ValueError("请勿选择 history 存档目录内的图片。")
    if anchor.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("锚点海报须为 png/jpg/jpeg/webp 图片。")
    return anchor


def archive_publish_to_history(run_output_dir: Path, *, reason: str = "anchor_poster_regenerate") -> str:
    """将 publish/ 与 publish/final/ 内现有文件移入 history/{timestamp}/ 存档。"""
    publish_dir = run_output_dir / "publish"
    final_dir = publish_dir / "final"
    if not publish_dir.is_dir():
        return ""

    root_files = [path for path in publish_dir.iterdir() if path.is_file()]
    final_files = [path for path in final_dir.iterdir() if path.is_file()] if final_dir.is_dir() else []
    if not root_files and not final_files:
        return ""

    timestamp = get_timestamp()
    archive_dir = run_output_dir / "history" / timestamp
    archive_publish_dir = archive_dir / "publish"
    archive_final_dir = archive_dir / "final"
    archive_publish_dir.mkdir(parents=True, exist_ok=True)
    archive_final_dir.mkdir(parents=True, exist_ok=True)

    for path in root_files:
        shutil.move(str(path), str(archive_publish_dir / path.name))
    for path in final_files:
        shutil.move(str(path), str(archive_final_dir / path.name))

    meta = {
        "archived_at": timestamp,
        "reason": reason,
        "archive_dir": str(archive_dir.resolve()),
        "publish_file_count": len(root_files),
        "final_file_count": len(final_files),
    }
    save_text_output(json.dumps(meta, ensure_ascii=False, indent=2), archive_dir / "_archive_meta.json")
    publish_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"已将原有 publish/final 存档至：{archive_dir}"
        f"（publish {len(root_files)} 个，final {len(final_files)} 个）"
    )
    return str(archive_dir.resolve())


def install_manual_poster_to_publish(anchor_poster_path: str | Path, publish_dir: Path) -> str:
    source = Path(anchor_poster_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"锚点海报不存在：{source}")

    publish_dir.mkdir(parents=True, exist_ok=True)
    for path in publish_dir.iterdir():
        if path.is_file() and "海报" in path.stem:
            path.unlink()

    target_name = source.name if "海报" in source.stem else f"{source.stem}_海报{source.suffix}"
    target = publish_dir / target_name
    if target.exists():
        target.unlink()
    shutil.copy2(str(source), str(target))

    selection_result = {
        "auto_selected": False,
        "manual_selected": True,
        "winner_index": 1,
        "winner_image_name": source.name,
        "winner_reason": "用户在非发布图中手动指定为海报锚点。",
        "anchor_source_path": str(source.resolve()),
    }
    save_text_output(
        json.dumps(selection_result, ensure_ascii=False, indent=2),
        publish_dir / "海报筛选结果.json",
    )
    print(f"已设手动海报锚点：{target.resolve()}（来源 {source.name}）")
    return str(target.resolve())


def run_anchor_poster_regenerate(
    run_output_dir: str | Path,
    *,
    anchor_poster_path: str,
) -> dict[str, object]:
    """以用户指定的候选海报为锚点：存档旧 publish/final → 补生细节/菜谱/封面 → PS 合成。"""
    run_output_dir = Path(run_output_dir).resolve()
    anchor = validate_anchor_poster_path(anchor_poster_path, run_output_dir)

    archive_dir = archive_publish_to_history(run_output_dir, reason="anchor_poster_regenerate")
    publish_dir = run_output_dir / "publish"
    poster_selected_image = install_manual_poster_to_publish(anchor, publish_dir)

    supplement_result = run_supplement_for_output_dir(
        run_output_dir,
        targets=["detail", "recipe", "cover", "photoshop"],
        force_photoshop_all=True,
    )
    supplement_result["workflow_mode"] = "anchor_poster_regenerate"
    supplement_result["anchor_poster_path"] = str(anchor.resolve())
    supplement_result["archive_dir"] = archive_dir
    return supplement_result


PUBLISH_REPLACE_SELECTION_REPORTS: dict[str, str] = {
    "poster": "海报筛选结果.json",
    "detail": "细节图筛选结果.json",
    "recipe": "菜谱图筛选结果.json",
    "cover": "封面图筛选结果.json",
}

PUBLISH_REPLACE_KIND_KEYWORDS: dict[str, str] = {
    "poster": "海报",
    "detail": "细节图",
    "recipe": "菜谱",
    "cover": "封面",
}


def infer_publish_kind_from_filename(path: str | Path) -> str | None:
    stem = Path(path).stem
    stem_lower = stem.lower()
    for kind_key, _sequence, _kind_label, keywords in PHOTOSHOP_SLOT_SPECS:
        for keyword in keywords:
            if keyword.lower() in stem_lower or keyword in stem:
                return kind_key
    for kind_key, keyword in PUBLISH_REPLACE_KIND_KEYWORDS.items():
        if keyword in stem:
            return kind_key
    return None


def validate_publish_replace_candidate_path(candidate_path: str | Path, run_output_dir: str | Path) -> Path:
    run_dir = Path(run_output_dir).resolve()
    candidate = Path(candidate_path).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"替换图片不存在：{candidate}")
    _assert_path_under_dir(candidate, run_dir)
    publish_dir = (run_dir / "publish").resolve()
    history_dir = (run_dir / "history").resolve()
    if publish_dir in candidate.parents or candidate.parent == publish_dir:
        raise ValueError("请从「非发布图」中选择菜品目录或 history 外的候选图。")
    if history_dir in candidate.parents or candidate.parent == history_dir:
        raise ValueError("请勿选择 history 存档目录内的图片。")
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("替换图片须为 png/jpg/jpeg/webp。")
    kind = infer_publish_kind_from_filename(candidate)
    if not kind:
        raise ValueError(
            f"无法识别图片类型：{candidate.name}（文件名须含 海报 / 细节图 / 菜谱 / 封面图 等关键词）。"
        )
    return candidate


def copy_image_to_publish_slot(source_path: str | Path, publish_dir: Path, kind_key: str) -> str:
    if kind_key not in PUBLISH_REPLACE_KIND_KEYWORDS:
        raise ValueError(f"未知发布图类型：{kind_key}")

    source = Path(source_path).resolve()
    publish_dir.mkdir(parents=True, exist_ok=True)
    kind_keyword = PUBLISH_REPLACE_KIND_KEYWORDS[kind_key]

    for path in publish_dir.iterdir():
        if path.is_file() and kind_keyword in path.stem and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            path.unlink()

    target = publish_dir / source.name
    if kind_keyword not in source.stem:
        target = publish_dir / f"{source.stem}_{kind_keyword}{source.suffix}"
    if target.exists():
        target.unlink()
    shutil.copy2(str(source), str(target))

    selection_result = {
        "auto_selected": False,
        "manual_selected": True,
        "winner_index": 1,
        "winner_image_name": source.name,
        "winner_reason": "用户在非发布图中手动替换发布图。",
        "replace_source_path": str(source.resolve()),
        "replace_publish_path": str(target.resolve()),
    }
    report_name = PUBLISH_REPLACE_SELECTION_REPORTS[kind_key]
    save_text_output(json.dumps(selection_result, ensure_ascii=False, indent=2), publish_dir / report_name)
    print(f"已替换 publish/{kind_keyword}：{target.name}（来源 {source.name}）")
    return str(target.resolve())


def remove_final_slot_if_exists(publish_final_dir: Path, kind_key: str) -> None:
    for slot_kind, sequence, _kind_label, keywords in PHOTOSHOP_SLOT_SPECS:
        if slot_kind != kind_key:
            continue
        existing = find_existing_final_for_slot(publish_final_dir, sequence, keywords)
        if existing is not None and existing.exists():
            existing.unlink()
            print(f"已删除旧 final 图：{existing.name}")
        return


def run_publish_image_replace(
    run_output_dir: str | Path,
    *,
    replacement_paths: list[str],
) -> dict[str, object]:
    """将非发布图候选复制到 publish/，PS 合成并更新 publish/final/ 对应槽位。"""
    run_output_dir = Path(run_output_dir).resolve()
    raw_paths = [str(item).strip() for item in replacement_paths if str(item).strip()]
    if not raw_paths:
        raise ValueError("请至少选择一张要替换的图片。")

    validated_paths: list[Path] = []
    kind_by_path: dict[str, str] = {}
    for path_text in raw_paths:
        candidate = validate_publish_replace_candidate_path(path_text, run_output_dir)
        resolved = str(candidate.resolve())
        if resolved in kind_by_path:
            continue
        kind = infer_publish_kind_from_filename(candidate)
        if not kind:
            raise ValueError(f"无法识别图片类型：{candidate.name}")
        if kind in kind_by_path.values():
            duplicate_label = PUBLISH_REPLACE_KIND_KEYWORDS[kind]
            raise ValueError(f"同类型发布图只能选一张（{duplicate_label}）。")
        kind_by_path[resolved] = kind
        validated_paths.append(candidate)

    publish_dir = run_output_dir / "publish"
    publish_final_dir = publish_dir / "final"
    publish_dir.mkdir(parents=True, exist_ok=True)
    publish_final_dir.mkdir(parents=True, exist_ok=True)

    dish_payload = load_dish_idea_record_from_dir(run_output_dir)
    dish_name = dish_payload["dish_name"]

    replaced_publish_paths: dict[str, str] = {}
    for candidate in validated_paths:
        kind = kind_by_path[str(candidate.resolve())]
        replaced_publish_paths[kind] = copy_image_to_publish_slot(candidate, publish_dir, kind)
        remove_final_slot_if_exists(publish_final_dir, kind)

    poster_selected_image = replaced_publish_paths.get("poster") or find_publish_image_by_kind(publish_dir, "海报")
    detail_selected_image = replaced_publish_paths.get("detail") or find_publish_image_by_kind(publish_dir, "细节图")
    recipe_selected_image = replaced_publish_paths.get("recipe") or find_publish_image_by_kind(publish_dir, "菜谱")
    cover_selected_image = replaced_publish_paths.get("cover") or find_publish_image_by_kind(publish_dir, "封面")

    timestamp = resolve_output_dir_timestamp(
        run_output_dir,
        dish_name,
        poster_selected_image or next(iter(replaced_publish_paths.values()), ""),
    )

    only_kinds = set(replaced_publish_paths.keys())
    photoshop_processed_files = rerun_photoshop_for_publish_dir(
        publish_dir=publish_dir,
        timestamp=timestamp,
        dish_name=dish_name,
        poster_source=poster_selected_image,
        detail_source=detail_selected_image,
        recipe_source=recipe_selected_image,
        cover_source=cover_selected_image,
        only_kinds=only_kinds,
        force_all=True,
    )

    replaced_labels = [PUBLISH_REPLACE_KIND_KEYWORDS[kind] for kind in sorted(only_kinds)]
    print(f"发布图手动替换完成：{', '.join(replaced_labels)}；PS 合成 {len(photoshop_processed_files)} 张。")

    return {
        "workflow_mode": "publish_image_replace",
        "dish_name": dish_name,
        "output_dir": str(run_output_dir),
        "replaced_kinds": sorted(only_kinds),
        "replaced_publish_paths": replaced_publish_paths,
        "replacement_source_paths": [str(path.resolve()) for path in validated_paths],
        "poster_selected_image": poster_selected_image,
        "detail_selected_image": detail_selected_image,
        "recipe_selected_image": recipe_selected_image,
        "cover_selected_image": cover_selected_image,
        "photoshop_processed_files": photoshop_processed_files,
        "photoshop_error": "",
    }


def resolve_output_dir_timestamp(run_output_dir: Path, dish_name: str, poster_path: str = "") -> str:
    if poster_path:
        return extract_timestamp_from_publish_name(poster_path)
    import re

    pattern = re.compile(rf"(\d{{8}}_\d{{6}})_{re.escape(dish_name)}")
    for path in sorted(run_output_dir.iterdir(), reverse=True):
        if not path.is_file():
            continue
        match = pattern.search(path.name)
        if match:
            return match.group(1)
    return get_timestamp()


def run_supplement_for_output_dir(
    run_output_dir: str | Path,
    *,
    targets: list[str],
    force_photoshop_all: bool = False,
) -> dict[str, object]:
    """按用户勾选项补生指定图片、平台文案或 PS 合成。"""
    from v2_core import ensure_runtime_config_loaded

    ensure_runtime_config_loaded()
    selected_targets = [str(item).strip() for item in targets if str(item).strip()]
    normalized_targets = [item for item in selected_targets if item in VALID_SUPPLEMENT_TARGETS]
    if not normalized_targets:
        raise ValueError("请至少选择一项可补生内容。")

    run_output_dir = Path(run_output_dir).resolve()
    dish_payload = load_dish_idea_record_from_dir(run_output_dir)
    dish_name = dish_payload["dish_name"]
    notes = dish_payload.get("notes", "")
    publish_dir = run_output_dir / "publish"
    publish_dir.mkdir(parents=True, exist_ok=True)

    doubao_client = build_text_client()
    image_client = build_openai_image_client()
    print(format_text_runtime_label())
    errors: list[str] = []
    all_saved_images: list[str] = []

    poster_selected_image = find_publish_image_by_kind(publish_dir, "海报")
    detail_selected_image = find_publish_image_by_kind(publish_dir, "细节图")
    recipe_selected_image = find_publish_image_by_kind(publish_dir, "菜谱图")
    cover_selected_image = find_publish_image_by_kind(publish_dir, "封面图")
    timestamp = resolve_output_dir_timestamp(run_output_dir, dish_name, poster_selected_image)

    print(f"补生目录：{run_output_dir}")
    print(f"补生项目：{', '.join(normalized_targets)}")

    image_targets = {"poster", "detail", "recipe", "cover"}
    if image_targets.intersection(normalized_targets) and not poster_selected_image and "poster" not in normalized_targets:
        raise RuntimeError("补生图片需要 publish 中已有海报图，或同时勾选「海报图」。")

    if "poster" in normalized_targets:
        try:
            poster_saved_images = collect_existing_stage_images(run_output_dir, "海报")
            if poster_saved_images and not poster_selected_image:
                poster_selected_image, _, _ = publish_image_group_safe(
                    doubao_client,
                    publish_dir=publish_dir,
                    candidate_paths=poster_saved_images,
                    image_kind="图文海报",
                    selection_report_name="海报筛选结果.json",
                    errors=errors,
                )
                all_saved_images.extend(poster_saved_images)
                print(f"复用已有海报图：{poster_selected_image}")
            if not poster_selected_image:
                haibao_template = load_cankao_group_template(HAIBAO_TEMPLATE_FILE)
                poster_prompt_result = generate_cankao_prompt_by_template(
                    client=doubao_client,
                    dish_name=dish_name,
                    notes=notes,
                    template_text=haibao_template,
                )
                poster_prompt_path = run_output_dir / f"{dish_name}_海报_文生图prompt.txt"
                save_text_output(poster_prompt_result["prompt"], poster_prompt_path)
                poster_settings = get_mode2_group_settings("poster")
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
                    poster_selected_image, _, _ = publish_image_group_safe(
                        doubao_client,
                        publish_dir=publish_dir,
                        candidate_paths=poster_saved_images,
                        image_kind="图文海报",
                        selection_report_name="海报筛选结果.json",
                        errors=errors,
                    )
            if not poster_selected_image:
                raise RuntimeError("海报图补生失败，未选出发布图。")
        except Exception as poster_exc:
            errors.append(f"海报图流程失败：{poster_exc}")
            print(f"海报图流程失败：{poster_exc}")

    if not poster_selected_image:
        raise RuntimeError("缺少海报参考图，无法继续补生。")

    poster_path = Path(poster_selected_image)
    bubble_text = ""
    bubble_path = run_output_dir / f"{dish_name}_气泡文案.txt"
    if "detail" in normalized_targets:
        try:
            bubble_result = generate_poster_bubble_copy(
                doubao_client,
                poster_path,
                dish_name=dish_name,
                notes=notes,
                current_bubble_file=bubble_path,
            )
            save_text_output(bubble_result["content"], bubble_path)
            bubble_text = bubble_result["content"]
            print(f"气泡文案已生成：{bubble_path}")
        except Exception as bubble_exc:
            errors.append(f"气泡文案失败：{bubble_exc}")
            if bubble_path.exists():
                bubble_text = bubble_path.read_text(encoding="utf-8").strip()

    if "detail" in normalized_targets:
        if not CHARACTER_REFERENCE_FILE.exists():
            errors.append(f"角色参考图不存在：{CHARACTER_REFERENCE_FILE}")
        else:
            try:
                detail_saved_images = collect_existing_stage_images(run_output_dir, "细节图")
                if detail_saved_images and not detail_selected_image:
                    detail_selected_image, _, _ = publish_image_group_safe(
                        doubao_client,
                        publish_dir=publish_dir,
                        candidate_paths=detail_saved_images,
                        image_kind="细节图",
                        selection_report_name="细节图筛选结果.json",
                        errors=errors,
                    )
                    all_saved_images.extend(detail_saved_images)
                    print(f"复用已有细节图：{detail_selected_image}")
                if not detail_selected_image:
                    xijietu_template = load_cankao_group_template(XIJIETU_TEMPLATE_FILE)
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
                    detail_settings = get_mode2_group_settings("detail")
                    detail_saved_images, detail_error = generate_group_images(
                        image_client=image_client,
                        prompt_text=detail_prompt_result["prompt"],
                        reference_paths=build_detail_reference_paths(poster_selected_image),
                        settings=detail_settings,
                        output_dir=run_output_dir,
                        timestamp=timestamp,
                        dish_name=dish_name,
                        name_suffix="细节图",
                        stage_label="细节图",
                        moderation_fallback=True,
                    )
                    if detail_error:
                        errors.append(detail_error)
                    all_saved_images.extend(detail_saved_images)
                    if detail_saved_images:
                        detail_selected_image, _, _ = publish_image_group_safe(
                            doubao_client,
                            publish_dir=publish_dir,
                            candidate_paths=detail_saved_images,
                            image_kind="细节图",
                            selection_report_name="细节图筛选结果.json",
                            errors=errors,
                        )
            except Exception as detail_exc:
                errors.append(f"细节图流程失败：{detail_exc}")
                print(f"细节图流程失败：{detail_exc}")

    if "recipe" in normalized_targets:
        try:
            recipe_saved_images = collect_existing_stage_images(run_output_dir, "菜谱图")
            if recipe_saved_images and not recipe_selected_image:
                recipe_selected_image, _, _ = publish_image_group_safe(
                    doubao_client,
                    publish_dir=publish_dir,
                    candidate_paths=recipe_saved_images,
                    image_kind="菜谱图",
                    selection_report_name="菜谱图筛选结果.json",
                    errors=errors,
                )
                all_saved_images.extend(recipe_saved_images)
                print(f"复用已有菜谱图：{recipe_selected_image}")
            if not recipe_selected_image:
                caipu_template = load_cankao_group_template(CAIPU_TEMPLATE_FILE)
                recipe_prompt_result = generate_cankao_prompt_by_template(
                    client=doubao_client,
                    dish_name=dish_name,
                    notes=notes,
                    template_text=caipu_template,
                )
                recipe_prompt_path = run_output_dir / f"{dish_name}_菜谱图_文生图prompt.txt"
                save_text_output(recipe_prompt_result["prompt"], recipe_prompt_path)
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
                if recipe_saved_images:
                    recipe_selected_image, _, _ = publish_image_group_safe(
                        doubao_client,
                        publish_dir=publish_dir,
                        candidate_paths=recipe_saved_images,
                        image_kind="菜谱图",
                        selection_report_name="菜谱图筛选结果.json",
                        errors=errors,
                    )
        except Exception as recipe_exc:
            errors.append(f"菜谱图流程失败：{recipe_exc}")
            print(f"菜谱图流程失败：{recipe_exc}")

    if "cover" in normalized_targets:
        try:
            cover_saved_images = collect_existing_stage_images(run_output_dir, "封面图")
            if cover_saved_images and not cover_selected_image:
                cover_selected_image, _, _ = publish_image_group_safe(
                    doubao_client,
                    publish_dir=publish_dir,
                    candidate_paths=cover_saved_images,
                    image_kind="封面图",
                    selection_report_name="封面图筛选结果.json",
                    errors=errors,
                )
                all_saved_images.extend(cover_saved_images)
                print(f"复用已有封面图：{cover_selected_image}")
            if not cover_selected_image:
                fengmian_template = load_cankao_group_template(FENGMIAN_TEMPLATE_FILE)
                cover_prompt_result = generate_cankao_prompt_by_template(
                    client=doubao_client,
                    dish_name=dish_name,
                    notes=notes,
                    template_text=fengmian_template,
                )
                cover_prompt_path = run_output_dir / f"{dish_name}_封面图_文生图prompt.txt"
                save_text_output(cover_prompt_result["prompt"], cover_prompt_path)
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
                if cover_saved_images:
                    cover_selected_image, _, _ = publish_image_group_safe(
                        doubao_client,
                        publish_dir=publish_dir,
                        candidate_paths=cover_saved_images,
                        image_kind="封面图",
                        selection_report_name="封面图筛选结果.json",
                        errors=errors,
                    )
        except Exception as cover_exc:
            errors.append(f"封面图流程失败：{cover_exc}")
            print(f"封面图流程失败：{cover_exc}")

    publish_copy_assets: dict[str, Any] = {}
    if "copy" in normalized_targets:
        publish_copy_assets = persist_v2_publish_copy_assets(
            client=doubao_client,
            output_dir=run_output_dir,
            timestamp=timestamp,
            dish_payload=dish_payload,
            poster_image_path=poster_selected_image,
        )
        publish_copy_error = str(publish_copy_assets.get("publish_copy_error", "")).strip()
        if publish_copy_error:
            errors.append(f"平台文案生成失败：{publish_copy_error}")

    photoshop_processed_files: list[str] = []
    image_targets = {"poster", "detail", "recipe", "cover"}
    regenerated_kinds = image_targets.intersection(normalized_targets)
    need_photoshop = "photoshop" in normalized_targets or bool(regenerated_kinds)
    if need_photoshop:
        try:
            only_kinds: set[str] | None
            if force_photoshop_all:
                only_kinds = None
            elif "photoshop" in normalized_targets and not regenerated_kinds:
                only_kinds = None
            else:
                only_kinds = set(regenerated_kinds) if regenerated_kinds else None
            photoshop_processed_files = rerun_photoshop_for_publish_dir(
                publish_dir=publish_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                poster_source=poster_selected_image,
                detail_source=detail_selected_image,
                recipe_source=recipe_selected_image,
                cover_source=cover_selected_image,
                only_kinds=only_kinds,
                force_all=force_photoshop_all,
            )
        except Exception as ps_exc:
            errors.append(f"Photoshop 合成失败：{ps_exc}")

    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    return {
        "workflow_mode": "supplement",
        "dish_name": dish_name,
        "output_dir": str(run_output_dir),
        "supplement_targets": normalized_targets,
        "poster_selected_image": poster_selected_image,
        "detail_selected_image": detail_selected_image,
        "recipe_selected_image": recipe_selected_image,
        "cover_selected_image": cover_selected_image,
        "photoshop_processed_files": photoshop_processed_files,
        "saved_images": all_saved_images,
        "publish_title_file": publish_copy_assets.get("publish_title_file", ""),
        "publish_description_file": publish_copy_assets.get("publish_description_file", ""),
        "publish_description_body_file": publish_copy_assets.get("publish_description_body_file", ""),
        "publish_platform_topic_files": publish_copy_assets.get("publish_platform_topic_files", {}),
        "publish_platform_description_files": publish_copy_assets.get("publish_platform_description_files", {}),
        "image_error": "\n".join(errors).strip(),
        "image_generation_settings": snapshot_mode2_image_settings(),
    }

