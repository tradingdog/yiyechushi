from __future__ import annotations

import re
from pathlib import Path

from v2_core import sanitize_file_name

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

FINAL_IMAGE_SLOT_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("01", "海报", ("海报", "poster")),
    ("02", "细节图", ("细节", "detail", "xijietu")),
    ("03", "菜谱图", ("菜谱", "recipe", "caipu")),
    ("04", "封面图", ("封面", "cover", "fengmian")),
)

PHOTOSHOP_SLOT_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("poster", "01", "海报", FINAL_IMAGE_SLOT_SPECS[0][2]),
    ("detail", "02", "细节图", FINAL_IMAGE_SLOT_SPECS[1][2]),
    ("recipe", "03", "菜谱图", FINAL_IMAGE_SLOT_SPECS[2][2]),
    ("cover", "04", "封面图", FINAL_IMAGE_SLOT_SPECS[3][2]),
)


def build_final_image_filename(*, timestamp: str, dish_name: str, sequence: str, kind_label: str) -> str:
    safe_name = sanitize_file_name(dish_name)
    return f"{sequence}_{timestamp}_{safe_name}_{kind_label}_01.jpg"


def collect_final_images(final_dir: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in final_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
    )


def find_final_image_by_slot(final_dir: Path, sequence: str, keywords: tuple[str, ...], *, label: str) -> Path:
    image_paths = collect_final_images(final_dir)
    sequence_prefix = f"{sequence}_"
    prefixed_candidates = [path for path in image_paths if path.name.startswith(sequence_prefix)]
    if len(prefixed_candidates) == 1:
        return prefixed_candidates[0]
    if len(prefixed_candidates) > 1:
        print(f"{label}按序列号 {sequence} 匹配到多张，取第一张：{prefixed_candidates[0]}")
        return prefixed_candidates[0]

    keyword_candidates = [
        path
        for path in image_paths
        if any(keyword.lower() in path.stem.lower() or keyword in path.stem for keyword in keywords)
    ]
    if not keyword_candidates:
        all_names = "\n".join(path.name for path in image_paths) or "（没有图片）"
        raise RuntimeError(
            f"final 目录未找到{label}图片。序列号：{sequence}，关键词：{keywords}\n当前图片：\n{all_names}"
        )
    if len(keyword_candidates) > 1:
        print(f"{label}按关键词匹配到多张，取第一张：{keyword_candidates[0]}")
    return keyword_candidates[0]


def find_existing_final_for_slot(final_dir: Path, sequence: str, keywords: tuple[str, ...]) -> Path | None:
    if not final_dir.exists():
        return None
    image_paths = collect_final_images(final_dir)
    sequence_prefix = f"{sequence}_"
    prefixed_candidates = [path for path in image_paths if path.name.startswith(sequence_prefix)]
    if prefixed_candidates:
        return prefixed_candidates[0]
    keyword_candidates = [
        path
        for path in image_paths
        if any(keyword.lower() in path.stem.lower() or keyword in path.stem for keyword in keywords)
    ]
    return keyword_candidates[0] if keyword_candidates else None


def is_photoshop_output_up_to_date(source_path: str, publish_final_dir: Path, sequence: str, keywords: tuple[str, ...]) -> bool:
    source = Path(source_path)
    if not source_path.strip() or not source.exists():
        return False
    final_path = find_existing_final_for_slot(publish_final_dir, sequence, keywords)
    if final_path is None or not final_path.exists():
        return False
    return final_path.stat().st_mtime >= source.stat().st_mtime


def resolve_photoshop_sources_to_process(
    *,
    publish_final_dir: Path,
    poster_source: str,
    detail_source: str,
    recipe_source: str,
    cover_source: str,
    only_kinds: set[str] | None = None,
    force_all: bool = False,
) -> tuple[list[str], list[str]]:
    source_by_kind = {
        "poster": poster_source,
        "detail": detail_source,
        "recipe": recipe_source,
        "cover": cover_source,
    }
    to_process: list[str] = []
    skipped_labels: list[str] = []
    for kind_key, sequence, kind_label, keywords in PHOTOSHOP_SLOT_SPECS:
        if only_kinds is not None and kind_key not in only_kinds:
            continue
        source_path = str(source_by_kind.get(kind_key, "") or "").strip()
        if not source_path:
            continue
        if not force_all and is_photoshop_output_up_to_date(source_path, publish_final_dir, sequence, keywords):
            skipped_labels.append(kind_label)
            print(f"跳过已合成{kind_label}：publish/final 已是最新，源图未变更。")
            continue
        to_process.append(source_path)
    return to_process, skipped_labels


def collect_existing_final_paths(
    *,
    publish_final_dir: Path,
    poster_source: str,
    detail_source: str,
    recipe_source: str,
    cover_source: str,
) -> list[str]:
    source_by_kind = {
        "poster": poster_source,
        "detail": detail_source,
        "recipe": recipe_source,
        "cover": cover_source,
    }
    existing_paths: list[str] = []
    for kind_key, sequence, _kind_label, keywords in PHOTOSHOP_SLOT_SPECS:
        source_path = str(source_by_kind.get(kind_key, "") or "").strip()
        if not source_path:
            continue
        final_path = find_existing_final_for_slot(publish_final_dir, sequence, keywords)
        if final_path is not None and final_path.exists():
            existing_paths.append(str(final_path.resolve()))
    return existing_paths


def resolve_processed_output_path(processed_file_map: dict[str, str], source_path: str) -> Path | None:
    if not source_path:
        return None
    source_resolved = Path(source_path).resolve()
    for input_path_text, output_path_text in processed_file_map.items():
        if Path(input_path_text).resolve() == source_resolved:
            return Path(output_path_text)
    return None


def apply_final_image_sequence_names(
    *,
    publish_final_dir: Path,
    timestamp: str,
    dish_name: str,
    processed_file_map: dict[str, str],
    poster_source: str,
    detail_source: str,
    recipe_source: str,
    cover_source: str,
) -> list[str]:
    publish_final_dir.mkdir(parents=True, exist_ok=True)
    slot_sources = (
        ("01", "海报", poster_source),
        ("02", "细节图", detail_source),
        ("03", "菜谱图", recipe_source),
        ("04", "封面图", cover_source),
    )
    renamed_paths: list[str] = []
    keywords_by_sequence = {sequence: keywords for sequence, _label, keywords in FINAL_IMAGE_SLOT_SPECS}
    for sequence, kind_label, source_path in slot_sources:
        output_path = resolve_processed_output_path(processed_file_map, source_path)
        if output_path is None or not output_path.exists():
            existing_final = find_existing_final_for_slot(
                publish_final_dir,
                sequence,
                keywords_by_sequence.get(sequence, ()),
            )
            if existing_final is not None and existing_final.exists():
                renamed_paths.append(str(existing_final.resolve()))
            continue
        target_name = build_final_image_filename(
            timestamp=timestamp,
            dish_name=dish_name,
            sequence=sequence,
            kind_label=kind_label,
        )
        target_path = publish_final_dir / target_name
        if output_path.resolve() != target_path.resolve():
            if target_path.exists():
                target_path.unlink()
            output_path.replace(target_path)
        renamed_paths.append(str(target_path.resolve()))
        print(f"final 图片已命名：{target_path.name}")
    return renamed_paths


def resolve_publish_image_triplet(final_dir: Path) -> tuple[Path, Path, Path]:
    poster = find_final_image_by_slot(final_dir, "01", FINAL_IMAGE_SLOT_SPECS[0][2], label="海报图")
    detail = find_final_image_by_slot(final_dir, "02", FINAL_IMAGE_SLOT_SPECS[1][2], label="细节图")
    recipe = find_final_image_by_slot(final_dir, "03", FINAL_IMAGE_SLOT_SPECS[2][2], label="菜谱图")
    return poster, detail, recipe


def resolve_publish_cover_image(final_dir: Path) -> Path:
    return find_final_image_by_slot(final_dir, "04", FINAL_IMAGE_SLOT_SPECS[3][2], label="封面图")


def split_wechat_description_body(description_text: str) -> str:
    body, _topics = split_wechat_description_parts(description_text)
    return body


def split_wechat_description_parts(description_text: str) -> tuple[str, str]:
    lines = [line.strip() for line in description_text.splitlines() if line.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        only_line = lines[0]
        if len(re.findall(r"#\S+", only_line)) >= 2:
            return "", only_line
        return only_line, ""
    return lines[0], " ".join(lines[1:])
