from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from guide_pages import GUIDE_PAGE_MODULES
from guide_pages.shared import (
    build_guide_page_image_system_prompt,
    build_guide_page_image_user_prompt,
    build_guide_page_text_system_prompt,
    build_guide_page_text_user_prompt,
    build_local_guide_page_image_prompt,
    format_page_output_name,
)


def generate_guide_pages(
    text_client: Any,
    dish_name: str,
    notes: str,
    recipe_text: str,
    style_reference: str,
    timestamp: str,
    output_text_dir: Path,
    output_prompt_dir: Path,
    bundle: dict[str, Any],
    request_text_generation: Callable[..., dict[str, str]],
    save_text_output: Callable[..., str],
    is_timeout_error: Callable[[Exception], bool],
    validate_page_text_content: Callable[[str, int, str], str],
    validate_page_prompt_content: Callable[[str, str, str], str],
) -> list[dict[str, Any]]:
    page_results: list[dict[str, Any]] = []

    for page_module in GUIDE_PAGE_MODULES:
        page_definition = page_module.PAGE_DEFINITION
        page_stage_prefix = f"图解{page_definition.page_number:02d}"
        page_output_name = format_page_output_name(dish_name, page_definition)

        try:
            page_text_result = request_text_generation(
                client=text_client,
                system_prompt=build_guide_page_text_system_prompt(
                    page_definition=page_definition,
                    fixed_dish_name=dish_name,
                ),
                user_prompt=build_guide_page_text_user_prompt(
                    page_definition=page_definition,
                    recipe_text=recipe_text,
                    notes=notes,
                ),
                stage_name=f"{page_stage_prefix}文案",
            )
            page_text = validate_page_text_content(
                page_text_result["content"],
                page_definition.page_number,
                page_definition.page_name,
            )
        except Exception as exc:
            if not is_timeout_error(exc) and not isinstance(exc, ValueError):
                raise
            page_text = page_module.build_local_page_text(bundle)
            page_text_result = {
                "model": "local-timeout-fallback",
                "content": page_text,
            }
            print(f"{page_stage_prefix}文案文本接口超时或内容异常，已切换为本地模板兜底。")

        page_text_file = save_text_output(
            content=page_text,
            output_dir=output_text_dir,
            timestamp=timestamp,
            base_name=dish_name,
            suffix=f"_{page_definition.file_label}_图解文案",
        )
        print(f"{page_stage_prefix}文案已保存：{page_text_file}")

        try:
            page_prompt_result = request_text_generation(
                client=text_client,
                system_prompt=build_guide_page_image_system_prompt(
                    page_definition=page_definition,
                    style_reference=style_reference,
                    fixed_dish_name=dish_name,
                    ad_copy=bundle["ad_copy"],
                ),
                user_prompt=build_guide_page_image_user_prompt(
                    page_definition=page_definition,
                    page_text=page_text,
                    fixed_dish_name=dish_name,
                    ad_copy=bundle["ad_copy"],
                ),
                stage_name=f"{page_stage_prefix}prompt",
            )
            page_prompt = validate_page_prompt_content(
                page_prompt_result["content"],
                dish_name,
                f"{page_stage_prefix}prompt",
            )
        except Exception as exc:
            if not is_timeout_error(exc) and not isinstance(exc, ValueError):
                raise
            page_prompt = build_local_guide_page_image_prompt(
                page_definition=page_definition,
                fixed_dish_name=dish_name,
                page_text=page_text,
                ad_copy=bundle["ad_copy"],
            )
            page_prompt_result = {
                "model": "local-timeout-fallback",
                "content": page_prompt,
            }
            print(f"{page_stage_prefix} prompt 文本接口超时或内容异常，已切换为本地模板兜底。")

        page_prompt_file = save_text_output(
            content=page_prompt,
            output_dir=output_prompt_dir,
            timestamp=timestamp,
            base_name=dish_name,
            suffix=f"_{page_definition.file_label}_文生图prompt",
        )
        print(f"{page_stage_prefix} prompt 已保存：{page_prompt_file}")

        page_results.append(
            {
                "page_number": page_definition.page_number,
                "page_name": page_definition.page_name,
                "file_label": page_definition.file_label,
                "text_model": page_text_result["model"],
                "prompt_model": page_prompt_result["model"],
                "text_file": page_text_file,
                "prompt_file": page_prompt_file,
                "prompt": page_prompt,
                "output_name": page_output_name,
            }
        )

    return page_results
