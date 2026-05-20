from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_FILE = ROOT_DIR / "临时调试prompt.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "image"
DEFAULT_AD_COPY_FILE = ROOT_DIR / "guanggaoyu.txt"


def load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_client() -> OpenAI:
    load_env_file(ROOT_DIR / ".env")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请先在 .env 文件中配置。")

    return OpenAI(api_key=api_key)


def load_prompt(prompt_file: Path) -> str:
    if not prompt_file.exists():
        raise FileNotFoundError(f"未找到调试 prompt 文件：{prompt_file}")

    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"调试 prompt 文件为空：{prompt_file}")

    return prompt


def load_text_variable(text_file: Path, variable_name: str) -> str:
    if not text_file.exists():
        raise FileNotFoundError(f"未找到变量文件 {variable_name}：{text_file}")

    value = text_file.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"变量文件 {variable_name} 为空：{text_file}")

    return value


def render_prompt_template(prompt: str) -> str:
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    return prompt.replace("{{GUANGGAOYU}}", ad_copy)


def sanitize_file_name(name: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return sanitized or "生成图片"


def extract_image_items(response: Any) -> list[dict[str, str]]:
    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {"data": getattr(response, "data", [])}

    image_items: list[dict[str, str]] = []
    for item in payload.get("data", []):
        if isinstance(item, dict):
            image_base64 = item.get("b64_json") or item.get("result") or ""
            revised_prompt = item.get("revised_prompt") or ""
        else:
            image_base64 = getattr(item, "b64_json", None) or getattr(item, "result", None) or ""
            revised_prompt = getattr(item, "revised_prompt", None) or ""

        if image_base64:
            image_items.append(
                {
                    "image_base64": image_base64,
                    "revised_prompt": revised_prompt,
                }
            )

    return image_items


def save_generated_images(
    image_items: list[dict[str, str]],
    dish_name: str,
    output_dir: Path,
    timestamp: str,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_file_name(dish_name)
    saved_files: list[str] = []

    for index, item in enumerate(image_items, start=1):
        image_file = output_dir / f"{timestamp}_{safe_name}_{index:02d}.png"
        image_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_files.append(str(image_file))

        revised_prompt = item["revised_prompt"].strip()
        if revised_prompt:
            revised_prompt_file = output_dir / f"{timestamp}_{safe_name}_{index:02d}_revised_prompt.txt"
            revised_prompt_file.write_text(revised_prompt, encoding="utf-8")

    return saved_files


def generate_images_from_prompt_file(
    dish_name: str,
    prompt_file_name: str = "临时调试prompt.txt",
) -> dict[str, Any]:
    prompt_file = ROOT_DIR / prompt_file_name
    prompt_template = load_prompt(prompt_file)
    prompt = render_prompt_template(prompt_template)

    client = build_client()
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
    size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1536").strip() or "1024x1536"
    quality = os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high"
    image_count = int(os.getenv("OPENAI_IMAGE_COUNT", "2").strip() or "2")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"正在调用模型：{model}")
    print(f"生成尺寸：{size}，质量：{quality}，数量：{image_count}")
    response = client.images.generate(
        model=model,
        prompt=prompt,
        n=image_count,
        size=size,
        quality=quality,
    )

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("接口已返回响应，但未发现可保存的图片数据。")

    print("图片接口已返回，正在保存文件...")
    saved_files = save_generated_images(
        image_items=image_items,
        dish_name=dish_name,
        output_dir=DEFAULT_OUTPUT_DIR,
        timestamp=timestamp,
    )

    rendered_prompt_file = DEFAULT_OUTPUT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}_prompt.txt"
    rendered_prompt_file.write_text(prompt, encoding="utf-8")

    return {
        "model": model,
        "size": size,
        "quality": quality,
        "output_dir": str(DEFAULT_OUTPUT_DIR),
        "saved_files": saved_files,
        "rendered_prompt_file": str(rendered_prompt_file),
        "timestamp": timestamp,
    }


if __name__ == "__main__":
    result = generate_images_from_prompt_file(dish_name="冬阴功蹄花虾汤")
    print("模块直接运行完成")
    print(f"输出目录：{result['output_dir']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")
