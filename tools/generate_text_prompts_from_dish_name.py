from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import generate_recipe_text_assets_from_idea_file


def main() -> int:
    print("开始执行一页厨文本与文生图 prompt 生成流程，不会调用图片模型...")

    try:
        result = generate_recipe_text_assets_from_idea_file(idea_file_name="dish_name.txt")
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("文本与文生图 prompt 生成完成，未调用图片模型。")
    print(f"本次创意输入：{result['dish_idea']}")
    print(f"本次最终菜名：{result['dish_name']}")
    if result.get("auto_generated") == "1":
        print(f"自动造菜：已启用")
        print(f"本轮参考菜：{result.get('reference_dish', '')}")
        print(f"本轮菜系：{result.get('region_label', '')}")
        if result.get("dish_memory_file"):
            print(f"历史记忆文件：{result['dish_memory_file']}")
    print(f"输出根目录：{result['output_root']}")
    print(f"图文标题文件：{result['publish_title_file']}")
    print(f"抖音图文描述文件：{result['publish_description_file']}")
    print(f"图文描述正文文件：{result['publish_description_body_file']}")
    for platform_key, topic_file in result.get("publish_platform_topic_files", {}).items():
        print(f"{platform_key} 话题文件：{topic_file}")
    for page in result["guide_pages"]:
        print(f"图解{page['page_number']:02d}：{page['page_name']}")
        print(f"图解文案文件：{page['text_file']}")
        print(f"图解 prompt 文件：{page['prompt_file']}")
    print(f"封面 prompt 文件：{result['cover_prompt_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())