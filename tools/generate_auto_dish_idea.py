from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent / "auto_dish_ideas"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import generate_auto_dish_idea_file


def sanitize_file_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "未命名菜品"


def ask_generate_count() -> int:
    raw = input("请输入要生成的菜品创意数量（默认 1）：").strip()
    if not raw:
        return 1
    try:
        count = int(raw)
    except ValueError:
        raise ValueError("数量必须是整数。")
    if count < 1:
        raise ValueError("数量必须大于等于 1。")
    if count > 100:
        raise ValueError("数量过大，请输入 1~100。")
    return count


def save_idea_file(result: dict[str, str], index: int, total: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dish_name = str(result.get("dish_idea", "")).strip() or "未命名菜品"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{index:02d}_{sanitize_file_name(dish_name)}_{timestamp}.txt"
    target_file = OUTPUT_DIR / file_name
    content = (
        f"菜名：{dish_name}\n"
        f"菜系：{result.get('region_label', '')}\n"
        f"参考传统菜：{result.get('reference_dish', '')}\n"
        f"补充说明：{result.get('notes', '')}\n"
        f"记忆文件：{result.get('memory_file', '')}\n"
        f"进度：{index}/{total}\n"
    )
    target_file.write_text(content, encoding="utf-8")
    return target_file


def main() -> int:
    print("开始执行自动造菜流程，只刷新 dish_name.txt，不生成图片...")
    try:
        generate_count = ask_generate_count()
    except ValueError as exc:
        print(f"输入无效：{exc}")
        return 1

    print(f"即将生成 {generate_count} 个菜品创意，结果会分别保存到：{OUTPUT_DIR}")
    for i in range(1, generate_count + 1):
        print(f"\n===== 开始第 {i}/{generate_count} 个创意 =====")
        try:
            result = generate_auto_dish_idea_file(idea_file_name="dish_name.txt")
        except Exception as exc:
            print(f"第 {i} 个创意生成失败：{exc}")
            return 1

        saved_file = save_idea_file(result=result, index=i, total=generate_count)
        print("自动造菜完成")
        print(f"本轮菜系：{result['region_label']}")
        print(f"参考传统菜：{result['reference_dish']}")
        print(f"新菜名：{result['dish_idea']}")
        print(f"创意文件：{saved_file}")

    print(f"dish_name.txt：{ROOT_DIR / 'dish_name.txt'}")
    print(f"创意目录：{OUTPUT_DIR}")
    print("全部生成完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())