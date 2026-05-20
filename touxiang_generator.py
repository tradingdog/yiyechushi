from __future__ import annotations

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from image_generator import ROOT_DIR, build_client, extract_image_items, sanitize_file_name


TOUXIANG_OUTPUT_DIR = ROOT_DIR / "touxiang"

AVATAR_PROMPTS: list[dict[str, str]] = [
    {
        "name": "一页折成厨师帽",
        "prompt": """为“一页厨”设计一个极具记忆点的账号头像，正方形构图，适合抖音、小红书、微信等平台头像使用。核心概念是“一页菜谱”变成一个标志性形象：一张白色纸页折叠成一顶夸张又利落的厨师帽，纸页边缘带有一点菜单排版的感觉，帽子下面不是完整人物，而是一个极简、神秘、很有辨识度的厨师脸部轮廓。整体风格现代、干净、强识别度、极简但不普通，背景使用高对比暖橙红与奶油白撞色，头像必须在很小尺寸下也能一眼识别。避免普通卡通厨师，避免廉价萌系，避免复杂场景，避免过多文字。视觉感觉：原创、聪明、利落、让人一眼忘不掉。""",
    },
    {
        "name": "菜谱变火焰",
        "prompt": """为“一页厨”设计一个强视觉冲击的头像，核心创意是“一页纸菜谱”正在燃烧成一团漂亮的火焰，火焰形状里隐约形成一个锅和勺子的轮廓，象征把一页菜谱点燃成一道新菜。头像需要极简、图形化、品牌感强，适合平台头像。风格偏高级 logo 插画风，色彩以炽热橙红、金黄、黑色少量点缀为主，背景简洁纯净。构图必须集中，主体图形占据头像大部分面积，小图也清楚。不要写实人物，不要传统厨师形象，不要过度复杂。整体要有一种“创造新菜”的能量感和危险的吸引力，让人一眼记住。""",
    },
    {
        "name": "一页纸做成锅盖",
        "prompt": """设计“一页厨”账号头像，核心设定是一张页面像金属锅盖一样盖在一口小锅上，页面上有简洁的菜谱排版线条，锅里冒出橙红热气，热气形成一个独特符号。头像风格要像成熟品牌图标，简洁、强符号化、记忆点强，不要平庸。背景建议深暖棕或奶白，主体颜色用橙红、米白、深棕，整体有烟火气但不土。头像需要兼顾“菜谱”“烹饪”“原创新菜”三个含义。禁止普通可爱风，禁止复杂写实场景，禁止低幼贴纸感。""",
    },
    {
        "name": "勺子切开一页纸",
        "prompt": """为“一页厨”设计一个极具品牌感的头像，画面主体是一把夸张的金属勺子切开一张悬浮的纸页，纸页断面不是普通纸，而是像食材层次一样丰富，里面有汤汁、虾、香料和菜谱线条。整体像一个强概念视觉符号，适合做长期头像。画风偏现代插画加轻海报感，中心构图，高对比，背景干净。色彩使用橙红、象牙白、金属银、深炭灰。目标是让人感觉“一页厨”不是普通做饭账号，而是把菜谱和菜品融合成一个新世界。小尺寸下主体依然清楚。""",
    },
    {
        "name": "神秘的一页面具",
        "prompt": """设计“一页厨”的头像，核心不是普通厨师，而是一个戴着“纸页面具”的神秘厨师。面具是一张极简白色页面，上面有少量菜谱排版符号和一个开口，露出一只很有神的眼睛。整体非常有记忆点，带一点神秘感和创造感，像一个专门研发新菜的人物化身。背景简洁，主体近景，色彩以奶白、暖橙、深棕为主，局部有红色点睛。画风要求精致、现代、强品牌感，不要吓人，不要普通卡通，不要全身像，只做头像级近景。""",
    },
    {
        "name": "印章感头像",
        "prompt": """为“一页厨”设计一个非常强识别度的印章式头像。核心概念：一个圆形徽章里，把“一页”“锅”“火”“勺”四个元素压缩成一个极简但强烈的图形标志。风格像高端餐饮品牌和独立主厨品牌之间的结合，既有东方气质，又有现代感。色彩以朱红、奶白、深褐、金橙为主，线条简洁但有张力。整体要像一个一旦看熟就永远认得出的品牌印记。适合平台头像，适合小图标显示，不需要复杂细节，不要写实人物。""",
    },
    {
        "name": "菜谱页面长出虾和火",
        "prompt": """设计“一页厨”的头像，核心创意是一张漂浮的菜谱页面，页面边缘长出食材和火焰，像它自己正在变成一道菜。要有超强的创意感和记忆点。主体必须集中在画面中心，适合圆形裁切。视觉上页面是白色或奶油色，边缘延展出橙红火焰、鲜虾弧线、绿色香草和汤汁飞溅，但整体仍要克制，不要太乱。风格偏创意品牌插画，高级、有冲击力、区别于普通美食博主头像。不要真实照片感，要更像独特品牌符号。""",
    },
    {
        "name": "锅里只有一张发光的纸",
        "prompt": """为“一页厨”设计一个极具反差记忆点的头像：一口黑色小锅里没有菜，只有一张发光的纸页，纸页发出橙金色热光，像一页菜谱本身就是灵魂。热气从纸页升起，形成一个极简标志。整个头像要传达“用一页，做一道别人没见过的菜”的品牌精神。风格高级、神秘、简洁、有概念，不要常规可爱厨师，不要堆砌食材，不要俗气。背景建议深色衬托发光主体，小图也能强识别。""",
    },
    {
        "name": "一页图腾大字",
        "prompt": """设计“一页厨”账号头像，主体是一个图腾化、视觉冲击极强的“一页”二字变形设计，将文字和锅、勺、火焰的元素融合成一个图形 logo。不要做普通书法字，要做成年轻、强势、独特、带一点街头感和品牌感的视觉符号。色彩用橙红、奶白、深棕，强对比，背景简单。整体必须像一个能独立成立的品牌头像，不依赖完整文字说明，小图状态也有很强辨识度。适合抖音头像，强调记忆点和原创感。""",
    },
    {
        "name": "异想天开版",
        "prompt": """为“一页厨”设计一个让人一眼忘不了的头像。核心画面是一页纸像鱼尾或火焰一样弯曲，悬浮在锅上方，纸页的一角滴下汤汁，像菜谱本身被煮成了新菜。构图大胆，主体居中，画风精致、奇异、品牌化，有一点超现实但仍然适合大众平台头像。颜色使用暖橙、番茄红、象牙白、焦糖棕。整体感觉是：新鲜、怪得刚刚好、很有想象力、别人没有。不要普通厨师，不要廉价 cartoon，不要复杂背景。""",
    },
]


def save_avatar_file(image_items: list[dict[str, str]], output_file: Path) -> list[Path]:
    saved_files: list[Path] = []

    for index, item in enumerate(image_items, start=1):
        target_file = output_file if index == 1 else output_file.with_stem(f"{output_file.stem}_{index:02d}")
        target_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_files.append(target_file)

        revised_prompt = item["revised_prompt"].strip()
        if revised_prompt:
            revised_file = target_file.with_name(f"{target_file.stem}_revised_prompt.txt")
            revised_file.write_text(revised_prompt, encoding="utf-8")

    return saved_files


def generate_avatar_set() -> dict[str, Any]:
    client = build_client()
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
    size = os.getenv("OPENAI_AVATAR_SIZE", "1024x1024").strip() or "1024x1024"
    quality = os.getenv("OPENAI_AVATAR_QUALITY", "high").strip() or "high"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    TOUXIANG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_lines = [
        f"生成时间：{timestamp}",
        f"模型：{model}",
        f"尺寸：{size}",
        f"质量：{quality}",
        "",
    ]
    saved_files: list[str] = []

    for index, item in enumerate(AVATAR_PROMPTS, start=1):
        print(f"正在生成第 {index}/{len(AVATAR_PROMPTS)} 个头像：{item['name']}")

        response = client.images.generate(
            model=model,
            prompt=item["prompt"],
            n=1,
            size=size,
            quality=quality,
        )

        image_items = extract_image_items(response)
        if not image_items:
            raise RuntimeError(f"头像 {item['name']} 生成失败，接口未返回图片数据。")

        safe_name = sanitize_file_name(item["name"])
        output_file = TOUXIANG_OUTPUT_DIR / f"{timestamp}_{index:02d}_{safe_name}.png"
        current_files = save_avatar_file(image_items, output_file)
        saved_files.extend(str(path) for path in current_files)

        prompt_file = TOUXIANG_OUTPUT_DIR / f"{timestamp}_{index:02d}_{safe_name}_prompt.txt"
        prompt_file.write_text(item["prompt"], encoding="utf-8")

        manifest_lines.append(f"{index:02d}. {item['name']}")
        manifest_lines.append(item["prompt"])
        manifest_lines.append("")

    manifest_file = TOUXIANG_OUTPUT_DIR / f"{timestamp}_头像prompt清单.txt"
    manifest_file.write_text("\n".join(manifest_lines), encoding="utf-8")

    return {
        "model": model,
        "size": size,
        "quality": quality,
        "output_dir": str(TOUXIANG_OUTPUT_DIR),
        "saved_files": saved_files,
        "manifest_file": str(manifest_file),
        "timestamp": timestamp,
    }


if __name__ == "__main__":
    result = generate_avatar_set()
    print("头像生成完成")
    print(f"输出目录：{result['output_dir']}")
    print(f"Prompt 清单：{result['manifest_file']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")
