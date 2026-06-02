from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from v2_core import (
    IDEA_FILE,
    auto_generate_dish_idea,
    build_doubao_client,
    build_openai_image_client,
    build_run_output_dir,
    ensure_runtime_config_loaded,
    extract_image_items,
    get_cover_image_count,
    generate_doubao_prompt_by_template,
    generate_images_by_prompt,
    get_image_settings,
    get_timestamp,
    load_cankao_template,
    load_cover_template,
    load_manual_dish_idea,
    parse_bool_env,
    parse_float_env,
    parse_int_env,
    render_prompt_fallback,
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

    template_text = load_cankao_template()
    try:
        prompt_result = generate_doubao_prompt_by_template(
            client=doubao_client,
            dish_name=dish_name,
            notes=notes,
            template_text=template_text,
        )
    except Exception as exc:
        print(f"豆包重写模板失败，切换本地降级模板：{exc}")
        prompt_result = {
            "model": "fallback",
            "prompt": render_prompt_fallback(template_text=template_text, dish_name=dish_name, notes=notes),
        }
    prompt_file = run_output_dir / f"{dish_name}_豆包提示词.txt"
    save_text_output(prompt_result["prompt"], prompt_file)
    print(f"豆包提示词已生成：{prompt_file}")

    image_settings = get_image_settings()
    print(
        f"开始调用生图模型：{image_settings['model']}，"
        f"size={image_settings['size']}，quality={image_settings['quality']}，n={image_settings['image_count']}"
    )
    image_items: list[dict[str, str]] = []
    image_error = ""
    try:
        image_items = generate_images_by_prompt(
            client=image_client,
            prompt_text=prompt_result["prompt"],
            settings=image_settings,
        )
    except Exception as image_exc:
        image_error = f"生图失败：{image_exc}"
        error_file = run_output_dir / "生图失败原因.txt"
        save_text_output(
            "外部图片接口暂时不可用，本轮已完成到生图调用点。\n"
            f"失败原因：{image_exc}\n"
            "建议：稍后重试，或先检查 OPENAI_API_KEY、网络代理与账号可用区。",
            error_file,
        )
        print(f"生图失败，已写入说明：{error_file}")

    saved_images: list[str] = []
    if image_items:
        saved_images = save_generated_images(
            image_items=image_items,
            output_dir=run_output_dir,
            timestamp=timestamp,
            dish_name=dish_name,
        )
        for image_file in saved_images:
            print(f"已保存图片：{image_file}")

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

    if saved_images:
        if len(saved_images) == 1:
            primary_selected_image = move_image_to_publish(saved_images[0], publish_dir)
            primary_selection_mode = "direct"
            saved_images = [primary_selected_image]
            print(f"主图数量=1，跳过豆包评分，直接入 publish：{primary_selected_image}")
        else:
            try:
                print("开始进行主图评分并选入 publish ...")
                primary_publish_selection = select_publish_images(
                    input_dir=run_output_dir,
                    include_page_types=("other",),
                )
                primary_selected_image = find_selected_output_path(primary_publish_selection, page_type="other")
                if primary_selected_image:
                    primary_selection_mode = "scored"
                    saved_images = remap_selected_image_path(saved_images, primary_selected_image)
                    print(f"主图评分完成，首选图：{primary_selected_image}")
                else:
                    primary_selected_image = move_image_to_publish(saved_images[0], publish_dir)
                    primary_selection_mode = "fallback_direct"
                    saved_images = [primary_selected_image]
                    print(f"主图评分未产出首选，回退直通首图：{primary_selected_image}")
            except Exception as select_exc:
                print(f"主图评分失败，回退直通首图：{select_exc}")
                primary_selected_image = move_image_to_publish(saved_images[0], publish_dir)
                primary_selection_mode = "fallback_direct"
                saved_images = [primary_selected_image]
                print(f"主图回退入 publish：{primary_selected_image}")

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
                else:
                    cover_selected_image = move_image_to_publish(cover_saved_images[0], publish_dir)
                    cover_selection_mode = "fallback_direct"
                    cover_saved_images = [cover_selected_image]
                    print(f"封面评分未产出首选，回退直通首图：{cover_selected_image}")
            except Exception as cover_select_exc:
                print(f"封面评分失败，回退直通首图：{cover_select_exc}")
                cover_selected_image = move_image_to_publish(cover_saved_images[0], publish_dir)
                cover_selection_mode = "fallback_direct"
                cover_saved_images = [cover_selected_image]
                print(f"封面回退入 publish：{cover_selected_image}")

    # 兜底补偿：主图已入选但封面缺失时，自动补跑一次封面流程，避免出现“目录只落一张图”的半成品结果。
    if primary_selected_image and not cover_selected_image:
        try:
            print("检测到封面缺失，开始自动补偿封面生成 ...")
            cover_template = load_cover_template()
            cover_prompt = render_cover_prompt_by_template(template_text=cover_template, dish_name=dish_name)
            if not cover_prompt_file:
                cover_group_key = f"{timestamp}_{sanitize_file_name(dish_name)}封面补偿"
                cover_prompt_path = run_output_dir / f"{cover_group_key}_文生图prompt.txt"
                save_text_output(cover_prompt, cover_prompt_path)
                cover_prompt_file = str(cover_prompt_path)
                print(f"封面补偿提示词已生成：{cover_prompt_file}")

            image_settings = get_image_settings()
            cover_settings = {
                "model": image_settings["model"],
                "size": image_settings["size"],
                "quality": image_settings["quality"],
                "image_count": max(1, get_cover_image_count()),
            }
            cover_items = generate_cover_images_from_reference(
                image_client=image_client,
                reference_image_path=primary_selected_image,
                cover_prompt=cover_prompt,
                cover_settings=cover_settings,
            )
            compensated_cover_images = save_generated_images(
                image_items=cover_items,
                output_dir=run_output_dir,
                timestamp=timestamp,
                dish_name=f"{dish_name}封面补偿",
            )
            if len(compensated_cover_images) == 1:
                cover_selected_image = move_image_to_publish(compensated_cover_images[0], publish_dir)
                cover_selection_mode = "direct"
                cover_saved_images = [cover_selected_image]
            elif compensated_cover_images:
                cover_publish_selection = select_publish_images(
                    input_dir=run_output_dir,
                    include_page_types=("cover",),
                )
                cover_selected_image = find_selected_output_path(cover_publish_selection, page_type="cover")
                if cover_selected_image:
                    cover_selection_mode = "scored"
                    cover_saved_images = remap_selected_image_path(compensated_cover_images, cover_selected_image)
            if cover_selected_image:
                print(f"封面补偿成功，首选封面：{cover_selected_image}")
            else:
                print("封面补偿结束：未选出封面首选图。")
        except Exception as cover_retry_exc:
            retry_text = f"封面补偿失败：{cover_retry_exc}"
            cover_image_error = f"{cover_image_error}；{retry_text}".strip("；")
            print(retry_text)
            save_text_output(
                "检测到主图已完成但封面缺失，已触发自动补偿封面流程。\n"
                f"补偿失败原因：{cover_retry_exc}",
                run_output_dir / "封面补偿失败原因.txt",
            )

    selected_for_publish = [path for path in [primary_selected_image, cover_selected_image] if path]
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
        "prompt_file": str(prompt_file),
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
