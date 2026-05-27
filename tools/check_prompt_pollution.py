from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


TEXT_MODULES = [
    "guide_pages.page02_selection",
    "guide_pages.page03_key_details",
    "guide_pages.page04_efficiency_tools",
    "guide_pages.page05_substitutions",
    "guide_pages.page06_taste_adjustment",
]

VISUAL_MODULES = [
    "guide_pages.page02_selection",
    "guide_pages.page03_key_details",
    "guide_pages.page04_efficiency_tools",
]

POLLUTION_KEYWORDS = ["豆腐", "肉沫", "黄豌豆"]


def build_sample_bundle() -> dict[str, object]:
    """返回与 build_local_recipe_bundle 真实 shape 对齐的最小完整 bundle。"""
    return {
        "dish_name": "青柠椒麻鸡腿年糕",
        "guide_line": "椒香酸爽配米饭",
        "subtitle": "外香里嫩 青麻酸香 层次丰富",
        "collection_hint": "",
        "collection_copy": "这张先收藏 原创新菜照着做更稳",
        "ad_copy": "关注@阿叶造新菜，家用开店都不赖！",
        "plate": "厚边石瓷餐盘，保留烤后焦边和堆叠高度",
        "table_setting": "浅灰石面台面，干净克制，不抢主菜",
        "background_props": "后景只保留少量与本菜直接相关的失焦陪衬，例如少量切圈红椒或辣椒段自然散在后景，不要默认摆整排干辣椒、整头蒜、木勺、香料碗和无关摆件",
        "main_food": "主菜堆叠有层次，鸡肉表面油亮，年糕边缘微焦，酱汁包裹均匀",
        "sauce": "酱汁薄薄挂住主料",
        "texture": "外香里嫩 层次清楚",
        "dynamic_action": "一双木筷从画面侧上方夹起一块主菜悬在半空 带轻微挂汁与热气",
        "colors": "主色为暖奶白、橙金和焦糖棕",
        "main_ingredients": [("鸡腿肉", "420g"), ("年糕", "260g"), ("青柠", "60g")],
        "spices": [("小米辣", "12g"), ("青花椒", "4g")],
        "seasonings": [("生抽", "18ml"), ("蚝油", "12ml"), ("盐", "3g")],
        "tips": [
            "年糕先泡软再下锅",
            "鸡腿肉大火煎出焦边",
            "青柠汁最后下增鲜",
            "酱汁要能挂住主料",
            "出锅前补香再装盘",
        ],
        "steps": [
            {"title": "备料腌制", "content": "鸡腿肉切块加盐生抽腌15分钟"},
            {"title": "煎香主料", "content": "热锅下鸡肉大火煎至两面焦香"},
            {"title": "下年糕", "content": "年糕入锅与鸡肉同炒至微焦"},
            {"title": "调酱收汁", "content": "蚝油青柠汁加水翻炒至挂汁"},
            {"title": "装盘出锅", "content": "撒青花椒和葱花即可上桌"},
        ],
        "notes": "酸辣 清香",
    }


def check_text_modules(bundle: dict[str, object]) -> tuple[bool, dict[str, list[str]]]:
    any_hit = False
    details: dict[str, list[str]] = {}

    for module_name in TEXT_MODULES:
        module = importlib.import_module(module_name)
        text = module.build_local_page_text(bundle)
        hits = [keyword for keyword in POLLUTION_KEYWORDS if keyword in text]
        details[module_name] = hits
        if hits:
            any_hit = True

    return any_hit, details


def check_visual_focus_modules() -> tuple[bool, dict[str, list[str]]]:
    any_hit = False
    details: dict[str, list[str]] = {}

    for module_name in VISUAL_MODULES:
        module = importlib.import_module(module_name)
        visual_focus = str(getattr(getattr(module, "PAGE_DEFINITION", object()), "visual_focus", ""))
        hits = [keyword for keyword in POLLUTION_KEYWORDS if keyword in visual_focus]
        details[module_name] = hits
        if hits:
            any_hit = True

    return any_hit, details


def run_compile_check() -> bool:
    files = ["image_generator.py", "guide_generator.py"]
    files.extend(str(path) for path in Path("guide_pages").glob("*.py"))
    command = [sys.executable, "-m", "py_compile", *files]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0


def main() -> int:
    bundle = build_sample_bundle()

    text_polluted, text_details = check_text_modules(bundle)
    visual_polluted, visual_details = check_visual_focus_modules()
    compile_ok = run_compile_check()

    print("Prompt pollution regression check")
    print("1. local page text polluted:", "YES" if text_polluted else "NO")
    for module_name, hits in text_details.items():
        print(f"   - {module_name}: {','.join(hits) if hits else 'NONE'}")

    print("2. visual_focus polluted:", "YES" if visual_polluted else "NO")
    for module_name, hits in visual_details.items():
        print(f"   - {module_name}: {','.join(hits) if hits else 'NONE'}")

    print("3. py_compile passed:", "YES" if compile_ok else "NO")

    if text_polluted or visual_polluted or not compile_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
