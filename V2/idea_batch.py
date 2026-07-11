from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from v2_core import (
    auto_generate_dish_idea,
    build_text_client,
    ensure_runtime_config_loaded,
    format_text_runtime_label,
    get_timestamp,
    load_manual_dish_idea,
    save_dish_idea_record_file,
    sanitize_file_name,
    write_dish_idea_file,
    IDEA_FILE,
)

DISH_POOL_DIR = Path(__file__).resolve().parent / "dish_pool"


def _prepare_single_payload(
    *,
    mode: str,
    dish_name: str,
    notes: str,
    doubao_client: Any,
    session_banned_main_ingredients: list[str] | None = None,
) -> dict[str, str]:
    if mode == "file":
        if not dish_name.strip():
            raise ValueError("手动模式下，菜名不能为空。")
        write_dish_idea_file(dish_name.strip(), notes.strip(), idea_file=IDEA_FILE)
        return load_manual_dish_idea(idea_file=IDEA_FILE)
    payload = auto_generate_dish_idea(
        doubao_client,
        session_banned_main_ingredients=session_banned_main_ingredients or [],
    )
    write_dish_idea_file(payload["dish_name"], payload.get("notes", ""), idea_file=IDEA_FILE)
    return payload


def run_idea_batch(
    *,
    count: int,
    mode: str | None = None,
    dish_name: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """仅调用豆包生成造菜信息 txt，写入 dish_pool/{时间戳}_batch/0001_菜名/ 结构。"""
    ensure_runtime_config_loaded()
    if mode in {"auto", "file"}:
        resolved_mode = mode
    else:
        raw = os.getenv("AUTO_GENERATE_DISH_IDEA", "1").strip().lower()
        resolved_mode = "auto" if raw in {"1", "true", "yes", "auto"} else "file"

    if count < 1:
        raise ValueError("生成数量至少为 1。")
    if count > 30:
        raise ValueError("单次最多生成 30 条造菜信息。")
    if resolved_mode == "file" and count > 1:
        raise ValueError("手动点名模式下每次只能生成 1 条，请将数量设为 1。")

    batch_dir = DISH_POOL_DIR / f"{get_timestamp()}_batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    print(f"批量造菜信息目录：{batch_dir}")

    doubao_client = build_text_client()
    print(format_text_runtime_label())
    entries: list[dict[str, str]] = []
    session_banned_main_ingredients: list[str] = []
    try:
        for index in range(1, count + 1):
            print(f"--- 第 {index}/{count} 条 ---")
            dish_payload = _prepare_single_payload(
                mode=resolved_mode,
                dish_name=dish_name,
                notes=notes,
                doubao_client=doubao_client,
                session_banned_main_ingredients=session_banned_main_ingredients,
            )
            main_ingredient = str(dish_payload.get("main_ingredient", "")).strip()
            if main_ingredient:
                session_banned_main_ingredients.append(main_ingredient)
            name = str(dish_payload.get("dish_name", "")).strip() or f"新菜{index}"
            folder = batch_dir / f"{index:04d}_{sanitize_file_name(name)}"
            folder.mkdir(parents=True, exist_ok=True)
            record_file = save_dish_idea_record_file(folder, dish_payload)
            print(f"已写入：{record_file}")
            entries.append(
                {
                    "index": index,
                    "dish_name": name,
                    "folder": str(folder),
                    "idea_file": record_file,
                }
            )
            if resolved_mode == "file":
                break
    finally:
        close_client = getattr(doubao_client, "close", None)
        if callable(close_client):
            close_client()

    return {
        "run_kind": "idea_batch",
        "batch_dir": str(batch_dir),
        "output_dir": str(batch_dir),
        "dish_name": entries[-1]["dish_name"] if entries else "",
        "entries": entries,
        "count": len(entries),
    }
