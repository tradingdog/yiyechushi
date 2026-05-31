from __future__ import annotations

import argparse

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
    generate_doubao_prompt_by_template,
    generate_images_by_prompt,
    get_image_settings,
    get_timestamp,
    load_cankao_template,
    load_manual_dish_idea,
    parse_bool_env,
    render_prompt_fallback,
    save_generated_images,
    save_text_output,
    write_dish_idea_file,
)


def resolve_auto_generate_enabled(mode: str | None) -> bool:
    if mode == "auto":
        return True
    if mode == "file":
        return False
    return parse_bool_env("AUTO_GENERATE_DISH_IDEA", default=False)


def run_v2_first_feature(mode: str | None = None) -> dict[str, object]:
    ensure_runtime_config_loaded()

    doubao_client = build_doubao_client()
    image_client = build_openai_image_client()

    auto_generate_enabled = resolve_auto_generate_enabled(mode)
    if auto_generate_enabled:
        dish_payload = auto_generate_dish_idea(doubao_client)
        write_dish_idea_file(dish_payload["dish_name"], dish_payload["notes"], idea_file=IDEA_FILE)
        print(f"自动造菜完成，已写回：{IDEA_FILE}")
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
    except Exception as first_image_exc:
        print(f"首轮生图失败，尝试使用精简降级提示词重试一次：{first_image_exc}")
        compact_prompt = render_prompt_fallback(template_text=template_text, dish_name=dish_name, notes=notes)
        save_text_output(compact_prompt, prompt_file)
        try:
            image_items = generate_images_by_prompt(
                client=image_client,
                prompt_text=compact_prompt,
                settings=image_settings,
            )
        except Exception as second_image_exc:
            image_error = f"生图两次调用均失败：{second_image_exc}"
            error_file = run_output_dir / "生图失败原因.txt"
            save_text_output(
                "外部图片接口暂时不可用，本轮已完成到生图调用点。\n"
                f"失败原因：{second_image_exc}\n"
                "建议：稍后重试，或先检查 OPENAI_API_KEY、网络代理与账号可用区。",
                error_file,
            )
            print(f"生图仍失败，已写入降级说明：{error_file}")

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

    close_doubao = getattr(doubao_client, "close", None)
    if callable(close_doubao):
        close_doubao()
    close_image = getattr(image_client, "close", None)
    if callable(close_image):
        close_image()

    return {
        "dish_name": dish_name,
        "notes": notes,
        "prompt_file": str(prompt_file),
        "output_dir": str(run_output_dir),
        "saved_images": saved_images,
        "image_error": image_error,
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
    print(f"输出目录：{result['output_dir']}")
    print(f"提示词文件：{result['prompt_file']}")
    if result.get("image_error"):
        print(f"生图状态：降级完成（{result['image_error']}）")
    elif result.get("saved_images"):
        print(f"生图状态：成功，共 {len(result['saved_images'])} 张")
    else:
        print("生图状态：未返回图片，但流程已完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
