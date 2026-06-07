from __future__ import annotations

import argparse
import sys
from pathlib import Path

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mode2_flow import run_v2_mode2
from v2_core import ensure_runtime_config_loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V2：自动/手动造菜 -> 海报/细节/菜谱/封面四组图 -> 选优 -> 文案 -> PS"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "file"],
        default=None,
        help="auto 自动造菜；file 读取 V2/dish_name.txt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_runtime_config_loaded()
    print("开始执行 V2：海报 -> 细节图 -> 菜谱图 -> 封面图")
    try:
        result = run_v2_mode2(mode=args.mode)
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("V2 流程执行完成。")
    print(f"菜名：{result['dish_name']}")
    if result.get("region_label"):
        print(f"菜系范围：{result['region_label']}")
    if result.get("reference_dish"):
        print(f"参考传统菜：{result['reference_dish']}")
    if result.get("memory_file"):
        print(f"历史记忆文件：{result['memory_file']}")
    print(f"输出目录：{result['output_dir']}")
    if result.get("image_error"):
        print(f"生图状态：部分失败（{result['image_error']}）")
    elif result.get("saved_images") or result.get("poster_saved_images"):
        print("生图状态：已完成（详见输出目录）")
    if result.get("photoshop_processed_files"):
        print(f"PS 合成：{len(result['photoshop_processed_files'])} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
