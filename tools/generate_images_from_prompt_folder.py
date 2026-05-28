from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import (
    build_image_client,
    build_text_client,
    generate_images_from_prompt_text,
    generate_publish_copy_assets,
    get_image_settings,
)


PROMPT_DIR = ROOT_DIR / "tools" / "prompt"


def load_prompt_files(prompt_dir: Path) -> list[Path]:
    if not prompt_dir.exists():
        return []
    return sorted(path for path in prompt_dir.glob("*.txt") if path.is_file())


def is_text_only_mode(argv: list[str]) -> bool:
    return "--text-only" in argv or "--publish-only" in argv


def main() -> int:
    prompt_files = load_prompt_files(PROMPT_DIR)
    text_only_mode = is_text_only_mode(sys.argv[1:])

    if not prompt_files:
        if text_only_mode:
            print("tools/prompt 目录下没有 txt prompt 文件，本次不生成抖音发布文案。")
        else:
            print("tools/prompt 目录下没有 txt prompt 文件，本次不生成图片。")
        return 0

    if text_only_mode:
        print(f"已找到 {len(prompt_files)} 个 prompt 文件，开始只生成抖音发布文案，不调用图片模型。")
        text_client = build_text_client()
        image_settings = None
        client = None
    else:
        image_settings = get_image_settings()
        print(f"已找到 {len(prompt_files)} 个 prompt 文件，开始按 config.env 生图参数处理。")
        print(
            f"当前生图模型：{image_settings['model']}，"
            f"尺寸：{image_settings['size']}，质量：{image_settings['quality']}，数量：{image_settings['image_count']}"
        )
        client = build_image_client()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    generated_files = 0
    skipped_files = 0
    failed_files: list[str] = []

    for prompt_file in prompt_files:
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()
        if not prompt_text:
            print(f"prompt 文件为空，跳过：{prompt_file.name}")
            skipped_files += 1
            continue

        print(f"正在处理 prompt：{prompt_file.name}")
        try:
            if text_only_mode:
                result = generate_publish_copy_assets(
                    client=text_client,
                    dish_name=prompt_file.stem,
                    source_text=prompt_text,
                    timestamp=timestamp,
                    output_name=prompt_file.stem,
                    source_label=f"tools/prompt 中的 {prompt_file.name}",
                )
                print(f"已保存图文标题：{result['title_file']}")
                print(f"已保存抖音图文描述：{result['description_file']}")
                print(f"已保存图文描述正文：{result['description_body_file']}")
            else:
                result = generate_images_from_prompt_text(
                    client=client,
                    dish_name=prompt_file.stem,
                    prompt=prompt_text,
                    timestamp=timestamp,
                    image_settings=image_settings,
                    output_name=prompt_file.stem,
                    stage_name=f"工具脚本 {prompt_file.name}",
                )
        except Exception as exc:
            print(f"生成失败：{prompt_file.name} -> {exc}")
            failed_files.append(prompt_file.name)
            continue

        generated_files += 1
        if not text_only_mode:
            for saved_file in result["saved_files"]:
                print(f"已保存图片：{saved_file}")

    if generated_files == 0 and skipped_files > 0 and not failed_files:
        if text_only_mode:
            print("找到的 txt 文件都为空，本次不生成抖音发布文案。")
        else:
            print("找到的 txt 文件都为空，本次不生成图片。")
        return 0

    print(
        f"处理完成：成功 {generated_files} 个，跳过 {skipped_files} 个，失败 {len(failed_files)} 个。"
    )

    if failed_files:
        print("失败文件：")
        for file_name in failed_files:
            print(f"- {file_name}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())