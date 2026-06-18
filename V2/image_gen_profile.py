"""生图渠道、比例、分辨率档位与 PSD 模板映射。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHOTOSHOP_TEMPLATE_DIR = PROJECT_ROOT / "tools" / "photoshop_template"
DEFAULT_PHOTOSHOP_TEMPLATE = PHOTOSHOP_TEMPLATE_DIR / "template.psd"

IMAGE_PROVIDER_OFFICIAL = "official"
IMAGE_PROVIDER_SILKROAD = "silkroad"
DEFAULT_IMAGE_PROVIDER = IMAGE_PROVIDER_OFFICIAL

DEFAULT_ASPECT_RATIO = "2:3"
DEFAULT_RESOLUTION_TIER = "1k"
DEFAULT_IMAGE_QUALITY = "high"

# 各比例在 1K / 2K / 4K 档位下的竖版或常用尺寸（宽×高，均为 gpt-image-2 合法尺寸）
# 2:3 的 1K 沿用项目既有 1024×1536（与现有 template.psd 一致）
SIZE_BY_RATIO_TIER: dict[str, dict[str, str]] = {
    "2:3": {"1k": "1024x1536", "2k": "1376x2064", "4k": "2336x3504"},
    "3:4": {"1k": "768x1024", "2k": "1536x2048", "4k": "2448x3264"},
    "9:16": {"1k": "720x1280", "2k": "1152x2048", "4k": "2160x3840"},
    "1:1": {"1k": "1024x1024", "2k": "2048x2048", "4k": "2880x2880"},
    "3:2": {"1k": "1008x672", "2k": "2064x1376", "4k": "3504x2336"},
    "16:9": {"1k": "1280x720", "2k": "2048x1152", "4k": "3840x2160"},
    "4:3": {"1k": "1024x768", "2k": "2048x1536", "4k": "3264x2448"},
    "4:5": {"1k": "832x1040", "2k": "1664x2080", "4k": "2560x3200"},
}

ASPECT_RATIO_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "2:3", "label": "2:3 竖版"},
    {"value": "3:4", "label": "3:4 竖版"},
    {"value": "9:16", "label": "9:16 竖版"},
    {"value": "1:1", "label": "1:1 方形"},
    {"value": "4:5", "label": "4:5 竖版"},
    {"value": "3:2", "label": "3:2 横版"},
    {"value": "16:9", "label": "16:9 横版"},
    {"value": "4:3", "label": "4:3 横版"},
)

RESOLUTION_TIER_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "1k", "label": "1K"},
    {"value": "2k", "label": "2K"},
    {"value": "4k", "label": "4K"},
)

QUALITY_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "low", "label": "标准"},
    {"value": "medium", "label": "中等"},
    {"value": "high", "label": "高清"},
    {"value": "auto", "label": "自动"},
)

PROVIDER_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": IMAGE_PROVIDER_OFFICIAL, "label": "官方 GPT"},
    {"value": IMAGE_PROVIDER_SILKROAD, "label": "丝路"},
)


def normalize_image_provider(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {IMAGE_PROVIDER_OFFICIAL, "openai", "openaiofficial", "官方"}:
        return IMAGE_PROVIDER_OFFICIAL
    if value in {IMAGE_PROVIDER_SILKROAD, "silkroad", "丝路"}:
        return IMAGE_PROVIDER_SILKROAD
    return DEFAULT_IMAGE_PROVIDER


def normalize_aspect_ratio(raw: str) -> str:
    value = (raw or DEFAULT_ASPECT_RATIO).strip()
    if value in SIZE_BY_RATIO_TIER:
        return value
    return DEFAULT_ASPECT_RATIO


def normalize_resolution_tier(raw: str, *, provider: str) -> str:
    value = (raw or DEFAULT_RESOLUTION_TIER).strip().lower()
    if value not in {"1k", "2k", "4k"}:
        value = DEFAULT_RESOLUTION_TIER
    if normalize_image_provider(provider) == IMAGE_PROVIDER_SILKROAD and value != "1k":
        return "1k"
    return value


def normalize_image_quality(raw: str) -> str:
    value = (raw or DEFAULT_IMAGE_QUALITY).strip().lower()
    if value in {"low", "medium", "high", "auto"}:
        return value
    return DEFAULT_IMAGE_QUALITY


def resolve_image_size(*, provider: str, aspect_ratio: str, resolution_tier: str) -> str:
    ratio = normalize_aspect_ratio(aspect_ratio)
    tier = normalize_resolution_tier(resolution_tier, provider=provider)
    size = SIZE_BY_RATIO_TIER[ratio][tier]
    return size


def photoshop_template_filename_for_size(size: str) -> str:
    return f"template_{size.replace('x', 'x')}.psd"


def resolve_photoshop_template_path(image_size: str) -> Path:
    normalized = image_size.strip().lower()
    specific = PHOTOSHOP_TEMPLATE_DIR / f"template_{normalized}.psd"
    if specific.exists():
        return specific.resolve()
    if normalized == "1024x1536" and DEFAULT_PHOTOSHOP_TEMPLATE.exists():
        return DEFAULT_PHOTOSHOP_TEMPLATE.resolve()
    return specific.resolve()


def list_required_photoshop_templates() -> list[dict[str, str]]:
    """列出程序会查找的 PSD 模板清单（供用户手工制作）。"""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for ratio, tiers in SIZE_BY_RATIO_TIER.items():
        for tier, size in tiers.items():
            if size in seen:
                continue
            seen.add(size)
            filename = f"template_{size}.psd"
            path = PHOTOSHOP_TEMPLATE_DIR / filename
            exists = path.exists() or (size == "1024x1536" and DEFAULT_PHOTOSHOP_TEMPLATE.exists())
            rows.append(
                {
                    "size": size,
                    "ratio": ratio,
                    "tier": tier,
                    "filename": "template.psd" if size == "1024x1536" and not path.exists() else filename,
                    "directory": str(PHOTOSHOP_TEMPLATE_DIR),
                    "exists": "yes" if exists else "no",
                    "smart_object_layer": "input_image",
                    "note": f"智能对象 input_image 画布尺寸须为 {size.replace('x', '×')}",
                }
            )
    rows.sort(key=lambda item: (int(item["size"].split("x")[0]) * int(item["size"].split("x")[1]), item["size"]))
    return rows


def image_gen_controls_from_mapping(data: dict[str, Any] | None) -> dict[str, str]:
    payload = data or {}
    provider = normalize_image_provider(
        str(payload.get("image_provider", payload.get("provider", ""))).strip()
        or os.getenv("IMAGE_API_PROVIDER", DEFAULT_IMAGE_PROVIDER)
    )
    aspect_ratio = normalize_aspect_ratio(
        str(payload.get("image_aspect_ratio", payload.get("aspect_ratio", ""))).strip()
        or os.getenv("IMAGE_ASPECT_RATIO", DEFAULT_ASPECT_RATIO)
    )
    resolution_tier = normalize_resolution_tier(
        str(payload.get("image_resolution_tier", payload.get("resolution_tier", ""))).strip()
        or os.getenv("IMAGE_RESOLUTION_TIER", DEFAULT_RESOLUTION_TIER),
        provider=provider,
    )
    quality = normalize_image_quality(
        str(payload.get("image_quality", payload.get("quality", ""))).strip()
        or os.getenv("OPENAI_IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY)
    )
    size = resolve_image_size(provider=provider, aspect_ratio=aspect_ratio, resolution_tier=resolution_tier)
    template_path = resolve_photoshop_template_path(size)
    return {
        "provider": provider,
        "aspect_ratio": aspect_ratio,
        "resolution_tier": resolution_tier,
        "quality": quality,
        "size": size,
        "template_file": str(template_path),
    }


def apply_image_gen_controls(data: dict[str, Any] | None) -> dict[str, str]:
    controls = image_gen_controls_from_mapping(data)
    os.environ["IMAGE_API_PROVIDER"] = controls["provider"]
    os.environ["IMAGE_ASPECT_RATIO"] = controls["aspect_ratio"]
    os.environ["IMAGE_RESOLUTION_TIER"] = controls["resolution_tier"]
    os.environ["OPENAI_IMAGE_SIZE"] = controls["size"]
    os.environ["OPENAI_IMAGE_QUALITY"] = controls["quality"]
    os.environ["PHOTOSHOP_TEMPLATE_FILE"] = controls["template_file"]
    if controls["provider"] == IMAGE_PROVIDER_OFFICIAL:
        os.environ["OPENAI_IMAGE_MODEL"] = "gpt-image-2"
    return controls


def image_gen_options_for_frontend(current: dict[str, Any] | None = None) -> dict[str, Any]:
    controls = image_gen_controls_from_mapping(current or {})
    return {
        "current": controls,
        "providers": list(PROVIDER_OPTIONS),
        "aspect_ratios": list(ASPECT_RATIO_OPTIONS),
        "resolution_tiers": list(RESOLUTION_TIER_OPTIONS),
        "qualities": list(QUALITY_OPTIONS),
        "size_table": SIZE_BY_RATIO_TIER,
        "photoshop_templates": list_required_photoshop_templates(),
    }


def format_image_gen_controls_label(controls: dict[str, str]) -> str:
    provider_label = "官方 GPT" if controls["provider"] == IMAGE_PROVIDER_OFFICIAL else "丝路"
    return (
        f"生图={provider_label}，{controls['aspect_ratio']} {controls['resolution_tier'].upper()} "
        f"({controls['size']})，quality={controls['quality']}"
    )
