from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from image_generator import generate_auto_dish_idea_file


def main() -> int:
    print("开始执行自动造菜流程，只刷新 dish_name.txt，不生成图片...")

    try:
        result = generate_auto_dish_idea_file(idea_file_name="dish_name.txt")
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("自动造菜完成")
    print(f"本轮菜系：{result['region_label']}")
    print(f"参考传统菜：{result['reference_dish']}")
    print(f"新菜名：{result['dish_idea']}")
    print(f"dish_name.txt：{ROOT_DIR / 'dish_name.txt'}")
    print(f"记忆文件：{result['memory_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())