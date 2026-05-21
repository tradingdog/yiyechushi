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
DEFAULT_IDEA_FILE = ROOT_DIR / "dish_name.txt"
OUTPUT_ROOT_DIR = ROOT_DIR / "output"
CREATIVE_OUTPUT_DIR = OUTPUT_ROOT_DIR / "chuangyi"
PROMPT_OUTPUT_DIR = OUTPUT_ROOT_DIR / "prompt"
IMAGE_OUTPUT_DIR = OUTPUT_ROOT_DIR / "image"
DEFAULT_OUTPUT_DIR = IMAGE_OUTPUT_DIR
DEFAULT_AD_COPY_FILE = ROOT_DIR / "guanggaoyu.txt"
DEFAULT_COLLECTION_HINT = "先收藏，想做时直接照着买照着煮"
DEFAULT_COLLECTION_COPY = "这张先收藏 原创新菜照着做更稳"


def build_recipe_system_prompt(ad_copy: str) -> str:
    return f"""
你是一页厨账号的原创融合新菜研发编辑，负责把用户给出的菜名想法或口味灵感，扩写成一张竖版一页菜谱海报所需的完整中文文案。

你的目标不是复述常见菜谱，而是做出“原创融合新菜研发”风格的新菜：
1. 成品必须像市面上少见但逻辑成立、家庭和小店都能复刻的原创菜。
2. 如果用户给的是常见菜名，也要基于口味、结构、配菜、器皿或场景做明显创新改造，不要直接输出常见版本。
3. 要优先吸收用户的补充说明，例如摆盘、器皿、核心调味、关键操作和口感方向。
4. 全部配方与步骤统一使用 g、ml、L、分钟 这类物理量单位，不要使用“适量、少许、1勺”这类模糊表达。
5. 默认按 2 人份来写，步骤控制在 5 步，适合做成高密度、易收藏的一页菜谱海报。
6. 成败关键固定输出 5 条短句，每条都不要使用逗号、句号、顿号等标点。
7. 底部关注文案必须固定为：{ad_copy}
8. 底部收藏文案必须固定为：{DEFAULT_COLLECTION_COPY}
9. 收藏提示语必须固定为：{DEFAULT_COLLECTION_HINT}

输出必须严格遵循下面这份纯文本结构，不要添加解释，不要使用 Markdown 代码块：

【基础定位】
创意来源：...
最终菜名：...
引导句：...
副标题：...
收藏提示：{DEFAULT_COLLECTION_HINT}
账号定位：原创融合新菜研发

【主画面说明】
器皿与摆盘：...
主画面食材：...
汤汁或酱体：...
质感重点：...
色彩点缀：...

【2人份食材】
主料
- 食材名 数量
- 食材名 数量

香料
- 食材名 数量
- 食材名 数量

调味料
- 食材名 数量
- 食材名 数量

【成败关键】
1. ...
2. ...
3. ...
4. ...
5. ...

【5步出锅】
1. 标题：...
内容：...
2. 标题：...
内容：...
3. 标题：...
内容：...
4. 标题：...
内容：...
5. 标题：...
内容：...

【底部文案】
收藏文案：{DEFAULT_COLLECTION_COPY}
关注文案：{ad_copy}

额外要求：
1. 标题要有明显带动性句式，菜名必须适合做海报主标题。
2. 副标题要突出这道原创菜最强的卖点、口味记忆点或出品场景。
3. 主画面说明要足够具体，让后续文生图阶段知道器皿、主体食材、汤汁或酱体状态、画面颜色重点。
4. 食材分组要清楚，数量要合理，不能互相打架。
5. 5 步必须前后顺序清晰，适合普通人照做。
6. 文字整体要像成熟抖音爆款图文海报，而不是教程论文或餐厅菜单。
""".strip()


def build_recipe_user_prompt(dish_idea: str, notes: str) -> str:
    note_text = notes or "无补充说明，请按原创融合新菜方向自行补齐。"
    return f"""
用户输入的菜名或创意：{dish_idea}
用户补充说明：{note_text}

请你基于这两部分信息，写出一整张一页菜谱海报所需的完整文案。
如果输入看起来像现有家常菜，也必须做出明显的新组合和新名字。
""".strip()


def build_prompt_system_prompt(style_reference: str) -> str:
    return f"""
你是一页厨账号的海报 prompt 导演。你的任务是把一份已经完成的中文菜谱文案，转换成一条给 gpt-image-2 使用的完整中文文生图 prompt。

你必须锁定以下 VI 与版式，不要自由发挥成别的风格：
1. 竖版 2:3 中文抖音美食图文海报。
2. 整体是成熟爆款菜谱海报风，不是极简风，不是杂志风，不要大面积留白。
3. 顶部是“引导句小于菜名”的双层标题结构，菜名是最大主标题，橙红或朱红手写刷字质感，带明显白边或高对比描边。
4. 标题下方要有一条高转化卖点副标题黄条，再下一条较细的收藏提醒条。
5. 中间偏左是主菜成品大图，必须是写实热门美食封面效果。
6. 左侧是“2人份食材”卡片，右侧是“成败关键”卡片。
7. 下半部分是“5步出锅”步骤条，5 个步骤卡横向排开，信息密度高但仍清楚。
8. 底部是一整条收藏与关注 CTA 横条，颜色醒目，像成熟爆款海报。
9. 主配色固定为暖奶白、橙红、金黄、焦糖棕，背景允许木桌、香料、小食材、暖色虚化氛围。
10. 所有中文必须自然工整，不要英文，不要乱码，不要错字。

请优先继承下面这份当前满意版本的参考 prompt 的风格取向，但不要照抄其中具体菜名和配方，只复用它的版式、密度、配色、语气、卡片结构和负面约束：

---参考 prompt 开始---
{style_reference}
---参考 prompt 结束---

输出要求：
1. 只输出一条可直接用于 gpt-image-2 的完整中文 prompt，不要加解释。
2. 必须把用户菜谱中的标题、副标题、食材、成败关键、5 步内容和底部文案全部吸收到 prompt 里。
3. 主画面必须以菜谱中的器皿与摆盘说明为准。
4. 强调整张图是“信息很多但一眼就想收藏”的成熟爆款海报。
5. 保留清晰的负面约束，避免极简、错误结构、食材畸形、塑料感、贴边、乱码和过度装饰。
""".strip()


def build_prompt_user_prompt(recipe_text: str) -> str:
    return f"""
请根据下面这份已经定稿的菜谱文字，写出最终文生图 prompt：

{recipe_text}
""".strip()


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


def load_dish_idea(idea_file: Path) -> dict[str, str]:
    if not idea_file.exists():
        raise FileNotFoundError(f"未找到菜品创意文件：{idea_file}")

    raw_lines = idea_file.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise ValueError(f"菜品创意文件为空：{idea_file}")

    dish_idea = raw_lines[0].strip()
    if not dish_idea:
        raise ValueError(f"菜品创意文件第一行为空：{idea_file}")

    notes = "\n".join(line.strip() for line in raw_lines[1:] if line.strip())
    return {
        "dish_idea": dish_idea,
        "notes": notes,
    }


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


def get_text_model() -> str:
    return os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


def get_image_settings() -> dict[str, Any]:
    return {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "size": os.getenv("OPENAI_IMAGE_SIZE", "1024x1536").strip() or "1024x1536",
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high",
        "image_count": int(os.getenv("OPENAI_IMAGE_COUNT", "2").strip() or "2"),
    }


def extract_text_output(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text and output_text.strip():
        return output_text.strip()

    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {}

    text_parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text") or ""
            if text:
                text_parts.append(text)

    return "\n".join(text_parts).strip()


def request_text_generation(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    stage_name: str,
) -> dict[str, str]:
    text_model = get_text_model()
    print(f"正在生成{stage_name}，调用文本模型：{text_model}")
    response = client.responses.create(
        model=text_model,
        instructions=system_prompt,
        input=user_prompt,
    )

    content = extract_text_output(response)
    if not content:
        raise RuntimeError(f"{stage_name}阶段未获得有效文本输出。")

    return {
        "model": text_model,
        "content": content,
    }


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
    revised_prompt_output_dir: Path | None = None,
    revised_prompt_stem: str | None = None,
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
            prompt_dir = revised_prompt_output_dir or output_dir
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_stem = revised_prompt_stem or f"{timestamp}_{safe_name}"
            revised_prompt_file = prompt_dir / f"{prompt_stem}_{index:02d}_revised_prompt.txt"
            revised_prompt_file.write_text(revised_prompt, encoding="utf-8")

    return saved_files


def save_text_output(
    content: str,
    output_dir: Path,
    timestamp: str,
    base_name: str,
    suffix: str,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{timestamp}_{sanitize_file_name(base_name)}{suffix}.txt"
    file_path.write_text(content.strip() + "\n", encoding="utf-8")
    return str(file_path)


def extract_generated_dish_name(recipe_text: str, fallback_name: str) -> str:
    for raw_line in recipe_text.splitlines():
        line = raw_line.strip()
        if line.startswith("最终菜名："):
            generated_name = line.split("：", 1)[1].strip()
            if generated_name:
                return generated_name

    return fallback_name


def generate_images_from_prompt_text(
    client: OpenAI,
    dish_name: str,
    prompt: str,
    timestamp: str,
) -> dict[str, Any]:
    image_settings = get_image_settings()

    print(f"正在调用模型：{image_settings['model']}")
    print(
        f"生成尺寸：{image_settings['size']}，质量：{image_settings['quality']}，数量：{image_settings['image_count']}"
    )
    response = client.images.generate(
        model=image_settings["model"],
        prompt=prompt,
        n=image_settings["image_count"],
        size=image_settings["size"],
        quality=image_settings["quality"],
    )

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("接口已返回响应，但未发现可保存的图片数据。")

    print("图片接口已返回，正在保存文件...")
    prompt_stem = f"{timestamp}_{sanitize_file_name(dish_name)}"
    saved_files = save_generated_images(
        image_items=image_items,
        dish_name=dish_name,
        output_dir=IMAGE_OUTPUT_DIR,
        timestamp=timestamp,
        revised_prompt_output_dir=PROMPT_OUTPUT_DIR,
        revised_prompt_stem=prompt_stem,
    )

    return {
        "model": image_settings["model"],
        "size": image_settings["size"],
        "quality": image_settings["quality"],
        "image_count": image_settings["image_count"],
        "saved_files": saved_files,
    }


def generate_recipe_assets_from_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, Any]:
    idea_file = ROOT_DIR / idea_file_name
    idea_payload = load_dish_idea(idea_file)
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    style_reference = render_prompt_template(load_prompt(DEFAULT_PROMPT_FILE))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"正在读取创意文件：{idea_file}")
    print(f"本次菜品创意：{idea_payload['dish_idea']}")
    if idea_payload["notes"]:
        print(f"补充说明：{idea_payload['notes']}")

    client = build_client()

    recipe_result = request_text_generation(
        client=client,
        system_prompt=build_recipe_system_prompt(ad_copy),
        user_prompt=build_recipe_user_prompt(
            dish_idea=idea_payload["dish_idea"],
            notes=idea_payload["notes"],
        ),
        stage_name="创意菜谱",
    )
    recipe_text = recipe_result["content"]
    generated_dish_name = extract_generated_dish_name(recipe_text, idea_payload["dish_idea"])
    creative_file = save_text_output(
        content=recipe_text,
        output_dir=CREATIVE_OUTPUT_DIR,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix="_一页菜谱",
    )
    print(f"创意菜谱已保存：{creative_file}")

    prompt_result = request_text_generation(
        client=client,
        system_prompt=build_prompt_system_prompt(style_reference),
        user_prompt=build_prompt_user_prompt(recipe_text),
        stage_name="文生图prompt",
    )
    image_prompt = prompt_result["content"]
    prompt_file = save_text_output(
        content=image_prompt,
        output_dir=PROMPT_OUTPUT_DIR,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix="_文生图prompt",
    )
    print(f"文生图 prompt 已保存：{prompt_file}")

    image_result = generate_images_from_prompt_text(
        client=client,
        dish_name=generated_dish_name,
        prompt=image_prompt,
        timestamp=timestamp,
    )

    return {
        "dish_idea": idea_payload["dish_idea"],
        "dish_name": generated_dish_name,
        "notes": idea_payload["notes"],
        "text_model": recipe_result["model"],
        "prompt_model": prompt_result["model"],
        "image_model": image_result["model"],
        "creative_file": creative_file,
        "prompt_file": prompt_file,
        "output_root": str(OUTPUT_ROOT_DIR),
        "creative_output_dir": str(CREATIVE_OUTPUT_DIR),
        "prompt_output_dir": str(PROMPT_OUTPUT_DIR),
        "image_output_dir": str(IMAGE_OUTPUT_DIR),
        "saved_files": image_result["saved_files"],
        "timestamp": timestamp,
    }


def generate_images_from_prompt_file(
    dish_name: str,
    prompt_file_name: str = "临时调试prompt.txt",
) -> dict[str, Any]:
    prompt_file = ROOT_DIR / prompt_file_name
    prompt_template = load_prompt(prompt_file)
    prompt = render_prompt_template(prompt_template)

    client = build_client()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_result = generate_images_from_prompt_text(
        client=client,
        dish_name=dish_name,
        prompt=prompt,
        timestamp=timestamp,
    )

    rendered_prompt_file = PROMPT_OUTPUT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}_原始prompt.txt"
    PROMPT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered_prompt_file.write_text(prompt, encoding="utf-8")

    return {
        "model": image_result["model"],
        "size": image_result["size"],
        "quality": image_result["quality"],
        "output_dir": str(IMAGE_OUTPUT_DIR),
        "saved_files": image_result["saved_files"],
        "rendered_prompt_file": str(rendered_prompt_file),
        "timestamp": timestamp,
    }


if __name__ == "__main__":
    result = generate_recipe_assets_from_idea_file()
    print("模块直接运行完成")
    print(f"输出根目录：{result['output_root']}")
    print(f"创意菜谱：{result['creative_file']}")
    print(f"文生图 prompt：{result['prompt_file']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")
