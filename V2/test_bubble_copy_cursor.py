"""用 Cursor 文本模型测试气泡文案口语化（不跑生图）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

V2_DIR = Path(__file__).resolve().parent
ROOT = V2_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(V2_DIR))

os.environ.setdefault("TEXT_PROVIDER", "cursor")

from v2_core import (  # noqa: E402
    OUTPUT_DIR,
    build_text_client,
    ensure_runtime_config_loaded,
    generate_poster_bubble_copy,
    validate_bubble_copy_naturalness,
    validate_bubble_copy_text,
    BUBBLE_COPY_BAD_EXAMPLES,
)

TEST_DISHES = (
    "豆酱焗沼虾",
    "野菌",
    "南乳焖蹄筋",
)


def find_output_dir(dish_name: str) -> Path | None:
    if not OUTPUT_DIR.exists():
        return None
    matches = sorted(
        (p for p in OUTPUT_DIR.iterdir() if p.is_dir() and dish_name in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return matches[0]
    # 兼容菜名与目录后缀略有出入（如「酱/烧」）
    keyword = dish_name[:3] if len(dish_name) >= 3 else dish_name
    fuzzy = sorted(
        (p for p in OUTPUT_DIR.iterdir() if p.is_dir() and keyword in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return fuzzy[0] if fuzzy else None


def find_poster_image(output_dir: Path) -> Path | None:
    candidates = list(output_dir.glob("publish/final/*海报*.jpg"))
    candidates += list(output_dir.glob("*海报*.jpg"))
    candidates += list(output_dir.glob("publish/*海报*.jpg"))
    return candidates[0] if candidates else None


def read_dish_notes(output_dir: Path, dish_name: str) -> str:
    for path in output_dir.glob(f"*{dish_name}*造菜信息*.txt"):
        try:
            return path.read_text(encoding="utf-8")[:300]
        except OSError:
            continue
    return ""


def main() -> int:
    ensure_runtime_config_loaded()
    print("校验：坏例子应被拦截")
    for bad in BUBBLE_COPY_BAD_EXAMPLES[:3]:
        err = validate_bubble_copy_naturalness(bad)
        print(f"  {bad!r} -> {err or 'PASS(意外)'}")

    client = build_text_client()
    results: list[tuple[str, str, str]] = []
    for dish_name in TEST_DISHES:
        output_dir = find_output_dir(dish_name)
        if output_dir is None:
            print(f"[跳过] 未找到输出目录：{dish_name}")
            continue
        poster = find_poster_image(output_dir)
        if poster is None:
            print(f"[跳过] 未找到海报：{dish_name} @ {output_dir}")
            continue
        notes = read_dish_notes(output_dir, dish_name)
        old_bubble = output_dir / f"{dish_name}_气泡文案.txt"
        old_text = old_bubble.read_text(encoding="utf-8").strip() if old_bubble.exists() else ""
        print(f"\n=== {dish_name} ===")
        print(f"旧气泡：{old_text or '（无）'}")
        result = generate_poster_bubble_copy(
            client,
            poster,
            dish_name=dish_name,
            notes=notes,
            current_bubble_file=old_bubble if old_bubble.exists() else None,
        )
        new_text = result["content"]
        validate_bubble_copy_text(new_text)
        print(f"新气泡：{new_text}（model={result['model']}）")
        results.append((dish_name, old_text, new_text))

    if len(results) < 3:
        print(f"\n仅完成 {len(results)} 道菜，请检查输出目录或 API 配置。")
        return 1
    print("\n=== 汇总 ===")
    for dish, old, new in results:
        print(f"{dish}：{old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
