from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import generate_page01_prompt_only_from_idea_file


def main() -> int:
    print("开始执行图解01首图提示词轻量生成流程，仅使用 dish_name.txt，不会调用图片模型...")

    try:
        result = generate_page01_prompt_only_from_idea_file(idea_file_name="dish_name.txt")
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("图解01首图提示词生成完成，未调用图片模型。")
    print(f"本次创意输入：{result['dish_idea']}")
    print(f"本次最终菜名：{result['dish_name']}")
    print(f"创意菜谱文件：{result['creative_file']}")
    print(f"图解01提示词文件：{result['prompt_file']}")
    print(f"输出根目录：{result['output_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
