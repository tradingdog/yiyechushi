from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INGREDIENT_LIBRARY_FILE = ROOT_DIR / "V2" / "cankao" / "zhushicai.txt"

MEAT_TOP_SECTION = "荤食材"
VEG_TOP_SECTIONS = ("素食材",)
CUT_TOP_SECTION = "改刀法"
ANCHOR_TOP_SECTION = "烹饪锚点"
DISH_TYPE_TOP_SECTION = "菜式类型"


@dataclass
class IngredientEntry:
    name: str
    part: str
    trait: str
    usage: str
    species: str
    rank: int


@dataclass
class MarketIngredientLibrary:
    meat_ingredients: list[IngredientEntry] = field(default_factory=list)
    veg_ingredients: list[IngredientEntry] = field(default_factory=list)
    cut_styles: dict[str, list[str]] = field(default_factory=dict)
    texture_anchors: list[str] = field(default_factory=list)
    flavor_anchors: list[str] = field(default_factory=list)
    carrier_anchors: list[str] = field(default_factory=list)
    dish_types: list[str] = field(default_factory=list)
    ingredient_families: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


def _is_comment_line(line: str) -> bool:
    return line.startswith("#")


def _is_top_section_line(line: str) -> bool:
    return bool(re.fullmatch(r"\[[^\[\]]+\]", line))


def _parse_second_section_line(line: str) -> tuple[str, int]:
    match = re.fullmatch(r"\[\[([^|\]]+)(?:\|(\d+))?\]\]", line)
    if not match:
        raise ValueError(f"食材库二级分类格式异常：{line}")
    label = match.group(1).strip()
    rank = int(match.group(2)) if match.group(2) else 99
    return label, rank


def _parse_data_line(line: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in line.split("|")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def load_market_ingredient_library(library_file: Path | None = None) -> MarketIngredientLibrary:
    path = library_file or DEFAULT_INGREDIENT_LIBRARY_FILE
    if not path.exists():
        raise FileNotFoundError(f"未找到菜市场食材库：{path}")

    library = MarketIngredientLibrary()
    current_top = ""
    current_second = ""
    current_rank = 99

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or _is_comment_line(line):
            continue

        if _is_top_section_line(line):
            current_top = line[1:-1].strip()
            current_second = ""
            current_rank = 99
            continue

        if line.startswith("[[") and line.endswith("]]"):
            current_second, current_rank = _parse_second_section_line(line)
            if current_top == CUT_TOP_SECTION:
                library.cut_styles.setdefault(current_second, [])
            continue

        name, part, trait, usage = _parse_data_line(line)
        if not name:
            continue

        if current_top == MEAT_TOP_SECTION:
            library.meat_ingredients.append(
                IngredientEntry(
                    name=name,
                    part=part,
                    trait=trait,
                    usage=usage,
                    species=current_second,
                    rank=current_rank,
                )
            )
            continue

        if current_top in VEG_TOP_SECTIONS:
            library.veg_ingredients.append(
                IngredientEntry(
                    name=name,
                    part=part,
                    trait=trait,
                    usage=usage,
                    species=current_second,
                    rank=current_rank,
                )
            )
            continue

        if current_top == CUT_TOP_SECTION:
            library.cut_styles.setdefault(current_second, []).append(name)
            continue

        if current_top == ANCHOR_TOP_SECTION:
            if current_second == "口感目标":
                library.texture_anchors.append(name)
            elif current_second == "风味方向":
                library.flavor_anchors.append(name)
            elif current_second == "呈现载体":
                library.carrier_anchors.append(name)
            continue

        if current_top == DISH_TYPE_TOP_SECTION:
            library.dish_types.append(name)

    if not library.meat_ingredients or not library.veg_ingredients:
        raise ValueError(f"菜市场食材库缺少荤或素主材：{path}")

    library.ingredient_families = build_ingredient_families(library)
    return library


def build_ingredient_families(library: MarketIngredientLibrary) -> list[tuple[str, tuple[str, ...]]]:
    families: dict[str, set[str]] = {}

    for entry in library.meat_ingredients + library.veg_ingredients:
        families.setdefault(entry.species, set()).add(entry.name)
        families.setdefault(entry.name, set()).add(entry.name)

    return [(family_name, tuple(sorted(keywords))) for family_name, keywords in sorted(families.items())]


def get_all_ingredient_names(library: MarketIngredientLibrary) -> list[str]:
    names = {entry.name for entry in library.meat_ingredients + library.veg_ingredients}
    return sorted(names)


def pick_cut_style(library: MarketIngredientLibrary, *, dish_type: str) -> str:
    pool_name = "肉类" if dish_type == "荤主配素" else "蔬菜"
    styles = library.cut_styles.get(pool_name) or []
    if not styles:
        merged: list[str] = []
        for items in library.cut_styles.values():
            merged.extend(items)
        styles = merged
    return random.choice(styles) if styles else "切块"


def _pick_entry(
    entries: list[IngredientEntry],
    *,
    banned_names: set[str],
) -> IngredientEntry:
    available = [entry for entry in entries if entry.name not in banned_names]
    return random.choice(available or entries)


def pick_creation_bundle(
    library: MarketIngredientLibrary,
    *,
    banned_main_ingredients: set[str] | None = None,
) -> dict[str, str]:
    banned_names = banned_main_ingredients or set()
    dish_type = random.choice(library.dish_types or ["荤主配素", "纯素菜"])
    if dish_type == "纯素菜":
        main_entry = _pick_entry(library.veg_ingredients, banned_names=banned_names)
    else:
        main_entry = _pick_entry(library.meat_ingredients, banned_names=banned_names)

    side_hint = ""
    if dish_type == "荤主配素":
        side_entry = _pick_entry(library.veg_ingredients, banned_names=banned_names)
        side_hint = f"{side_entry.name}（{side_entry.part}）"

    return {
        "dish_type": dish_type,
        "main_ingredient": main_entry.name,
        "main_part": main_entry.part,
        "main_trait": main_entry.trait,
        "main_usage": main_entry.usage,
        "main_species": main_entry.species,
        "side_hint": side_hint,
        "cut_style": pick_cut_style(library, dish_type=dish_type),
        "texture_anchor": random.choice(library.texture_anchors) if library.texture_anchors else "鲜香弹牙",
        "flavor_anchor": random.choice(library.flavor_anchors) if library.flavor_anchors else "咸鲜",
        "carrier_anchor": random.choice(library.carrier_anchors) if library.carrier_anchors else "盖饭",
    }


def render_ingredient_library_text(
    library: MarketIngredientLibrary,
    *,
    dish_type: str | None = None,
    compact: bool = False,
) -> str:
    lines: list[str] = ["【菜市场食材库】"]

    def render_entries(title: str, entries: list[IngredientEntry]) -> None:
        lines.append(f"[{title}]")
        current_species = ""
        for entry in entries:
            if entry.species != current_species:
                current_species = entry.species
                lines.append(f"[[{current_species}|{entry.rank}]]")
            if compact:
                lines.append(f"{entry.name}|{entry.part}|{entry.trait}")
            else:
                lines.append(f"{entry.name}|{entry.part}|{entry.trait}|{entry.usage}")

    if dish_type == "纯素菜":
        render_entries("素食材", library.veg_ingredients)
    elif dish_type == "荤主配素":
        render_entries("荤食材", library.meat_ingredients)
        lines.append("[搭配素材参考]")
        render_entries("素食材", library.veg_ingredients[:36])
    else:
        render_entries("荤食材", library.meat_ingredients)
        render_entries("素食材", library.veg_ingredients)

    lines.append(f"[{CUT_TOP_SECTION}]")
    for group_name, styles in library.cut_styles.items():
        lines.append(f"[[{group_name}]]")
        lines.extend(styles)

    lines.append(f"[{ANCHOR_TOP_SECTION}]")
    for anchor_name, values in (
        ("口感目标", library.texture_anchors),
        ("风味方向", library.flavor_anchors),
        ("呈现载体", library.carrier_anchors),
    ):
        lines.append(f"[[{anchor_name}]]")
        lines.extend(values)

    return "\n".join(lines)


def ingredient_appears_in_text(text: str, ingredient_name: str) -> bool:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", ingredient_name)
    if not key:
        return False
    return key in normalized


def validate_main_ingredient_usage(
    dish_name: str,
    notes: str,
    main_ingredient: str,
    *,
    library: MarketIngredientLibrary | None = None,
) -> str:
    combined = f"{dish_name}{notes}"
    if ingredient_appears_in_text(combined, main_ingredient):
        return ""

    if library:
        for entry in library.meat_ingredients + library.veg_ingredients:
            if entry.name == main_ingredient:
                for token in (entry.part, entry.species):
                    if token and ingredient_appears_in_text(combined, token):
                        return ""
                break

    return f"菜名或描述中未体现指定主食材「{main_ingredient}」，请让主材成为菜名记忆点或描述第一句。"


def find_used_library_ingredient_families(
    text: str,
    library: MarketIngredientLibrary,
) -> list[str]:
    matches: list[str] = []
    for family_name, keywords in library.ingredient_families:
        if any(ingredient_appears_in_text(text, keyword) for keyword in keywords):
            matches.append(family_name)
    return matches


def resolve_ingredient_library_file(raw_path: str = "") -> Path:
    candidate = Path(raw_path.strip()) if raw_path.strip() else DEFAULT_INGREDIENT_LIBRARY_FILE
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate


def library_summary(library: MarketIngredientLibrary) -> dict[str, Any]:
    return {
        "meat_count": len(library.meat_ingredients),
        "veg_count": len(library.veg_ingredients),
        "cut_style_groups": len(library.cut_styles),
        "families": len(library.ingredient_families),
    }
