from __future__ import annotations

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

import base64
import json
import os
import random
import re
import shutil
import ssl
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import httpcore
import httpx
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI

from guide_generator import generate_guide_pages
from guide_pages import cover_page, page01_recipe
from guide_pages.shared import GUIDE_PAGE_MACHINE_LABELS


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = ROOT_DIR / "config.env"
DEFAULT_PROMPT_FILE = ROOT_DIR / "临时调试prompt.txt"
DEFAULT_IDEA_FILE = ROOT_DIR / "dish_name.txt"
DEFAULT_TRADITIONAL_DISH_FILE = ROOT_DIR / "chuantongcaipu.txt"
DEFAULT_AUTO_DISH_MEMORY_FILE = ROOT_DIR / "dish_idea_memory.jsonl"
OUTPUT_ROOT_DIR = ROOT_DIR / "output"
DEFAULT_OUTPUT_DIR = OUTPUT_ROOT_DIR
DEFAULT_AD_COPY_FILE = ROOT_DIR / "guanggaoyu.txt"
DEFAULT_COLLECTION_HINT = ""
DEFAULT_COLLECTION_COPY = "这张先收藏 原创新菜照着做更稳"
DEFAULT_DYNAMIC_ACTION = "按菜品结构自行安排最合适的餐具和互动动作 餐具数量不限 可一只手或多只手自然入镜 例如舀汤 切开 夹起 叉起 捞起 翻面或蘸汁 不固定成筷子模板"
DEFAULT_REQUEST_RETRY_COUNT = 2
DEFAULT_TEXT_REQUEST_RETRY_COUNT = 3
DEFAULT_TEXT_PROVIDER = "doubao"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-mini-260428"
DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_TEXT_CONNECT_TIMEOUT_SECONDS = 20.0
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2-2026-04-21"
DEFAULT_AUTO_DISH_GENERATION_ENABLED = True
DEFAULT_AUTO_DISH_REGION_CODE = "0"
DEFAULT_PHOTOSHOP_AUTO_COMPOSITE_ENABLED = True
DEFAULT_PUBLISH_AUTO_SELECT_ENABLED = True
AUTO_DISH_GENERATION_RETRY_COUNT = 4
AUTO_DISH_RECENT_HISTORY_LIMIT = 10
PUBLISH_ACTIVITY_TOPICS: tuple[str, ...] = (
    "抖音美食推荐官",
    "跟着抖音学做菜",
    "我的厨房日记",
)
PUBLISH_GENERAL_TOPICS: tuple[str, ...] = (
    "家常菜谱",
    "晚饭吃什么",
    "下饭菜",
    "美食教程",
    "厨房日常",
    "原创菜谱",
    "快手家常菜",
    "跟着做不翻车",
    "小白学做菜",
)
PUBLISH_BANNED_TOPIC_TOKENS: tuple[str, ...] = (
    "阿叶造新菜",
)
PUBLISH_PLATFORM_TOPIC_SPECS: tuple[tuple[str, str, int], ...] = (
    ("douyin", "抖音", 5),
    ("xiaohongshu", "小红书", 10),
    ("wechat", "微信视频号和公众号", 30),
    ("kuaishou", "快手", 4),
)
PUBLISH_PLATFORM_REQUIRED_TOPIC_ENV: dict[str, str] = {
    "douyin": "PUBLISH_REQUIRED_TOPICS",
    "xiaohongshu": "PUBLISH_REQUIRED_TOPICS_XIAOHONGSHU",
    "wechat": "PUBLISH_REQUIRED_TOPICS_WECHAT",
    "kuaishou": "PUBLISH_REQUIRED_TOPICS_KUAISHOU",
}
PUBLISH_PLATFORM_TOPIC_FALLBACKS: dict[str, tuple[str, ...]] = {
    "douyin": (
        "抖音美食推荐官",
        "跟着抖音学做菜",
        "抖音美食",
        "抖音热门美食",
        "今日份晚饭",
        "下饭菜",
        "家常美食",
        "美食教程",
        "快手家常菜",
        "厨房日常",
        "新手做菜",
        "一周不重样家常菜",
    ),
    "xiaohongshu": (
        "小红书美食",
        "小红书爆款菜谱",
        "今日晚饭",
        "家常菜",
        "下饭菜",
        "好吃到停不下来",
        "厨房小白",
        "懒人食谱",
        "一人食",
        "上班族晚饭",
        "周末做饭",
        "美食灵感",
        "我的厨房日记",
        "简单快手菜",
        "请客菜",
        "宴客菜",
    ),
    "wechat": (
        "家常菜",
        "家常菜谱",
        "今日菜谱",
        "今日晚餐",
        "晚餐灵感",
        "下饭菜",
        "厨房技巧",
        "做菜教程",
        "美食做法",
        "详细做法",
        "新手做菜",
        "零失败菜谱",
        "一周菜单",
        "营养搭配",
        "家庭餐桌",
        "妈妈味道",
        "快手菜",
        "懒人做饭",
        "家宴菜",
        "请客菜",
        "节日餐桌",
        "聚餐菜单",
        "厨房干货",
        "食材搭配",
        "烹饪小技巧",
        "香煎做法",
        "鲜味料理",
        "豆腐做法",
        "海鲜做法",
        "日常做饭",
        "原创菜谱",
        "美食分享",
        "家庭料理",
        "三餐四季",
        "饭桌烟火气",
        "餐桌日常",
        "每周吃什么",
        "今晚吃什么",
        "孩子爱吃",
        "上班族做饭",
    ),
    "kuaishou": (
        "快手美食",
        "快手家常菜",
        "家常菜",
        "下饭菜",
        "快手菜谱",
        "厨房实拍",
        "今日晚饭",
        "美食做法",
        "简单好吃",
        "跟着做不翻车",
    ),
}
GUIDE_LINE_STALE_KEYWORDS = (
    "周末请客",
    "家宴",
    "请客就做",
    "今天就做这盘",
    "米饭党就做这盘",
    "就馋这口热乎",
    "就馋这口焦香",
    "超有面子",
    "有面子",
)
AUTO_DISH_BANNED_NAME_TOKENS: tuple[str, ...] = ()
AUTO_DISH_NAME_MIN_CHARS = 3
AUTO_DISH_NAME_MAX_CHARS = 8
# 用户要求：不使用这批本地限制词表去约束豆包发挥。
AUTO_DISH_NAME_ACTION_TOKENS: tuple[str, ...] = ()
AUTO_DISH_STRUCTURE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = ()
AUTO_DISH_MAIN_INGREDIENT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = ()
AUTO_DISH_FLAVOR_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = ()
AUTO_DISH_PRIMARY_METHOD_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = ()
AUTO_DISH_REGION_PROFILES: dict[str, dict[str, Any]] = {
    "0": {
        "label": "全球随机",
        "sections": ("中餐", "国外"),
        "keywords": (),
        "aliases": (),
    },
    "1": {
        "label": "中华料理",
        "sections": ("中餐",),
        "keywords": (),
        "aliases": (),
    },
    "2": {
        "label": "新马泰与东南亚料理",
        "sections": ("国外",),
        "keywords": ("泰", "新加坡", "马来", "娘惹", "印尼", "菲律宾", "越南"),
        "aliases": (
            "冬阴功汤",
            "肉骨茶",
            "海南鸡饭",
            "咖喱叻沙",
            "亚参叻沙",
            "槟城虾面",
            "福建虾面",
            "炒粿条",
            "泰式船面",
            "泰式金边粉",
            "越南河粉",
            "越南顺化牛肉粉",
            "越南烤肉米线",
            "沙嗲鸡肉串",
            "沙嗲牛肉串",
            "泰式椰浆鸡",
            "泰式绿咖喱鸡",
            "泰式红咖喱鸡",
            "马沙文咖喱牛肉",
            "马来椰浆饭",
            "摩摩喳喳",
            "娘惹九层糕",
        ),
    },
    "3": {
        "label": "日韩料理",
        "sections": ("国外",),
        "keywords": ("韩式", "日式", "日本", "韩国"),
        "aliases": (
            "寿喜烧",
            "日式涮涮锅",
            "相扑火锅",
            "牛丼",
            "亲子丼",
            "鳗鱼饭",
            "天丼",
            "味噌汤",
            "豚汁",
            "日式拉面",
            "豚骨拉面",
            "味噌拉面",
            "乌冬面",
            "荞麦面",
            "大阪烧",
            "广岛烧",
            "天妇罗",
            "唐扬炸鸡",
            "可乐饼",
            "日式煎饺",
            "日式茶碗蒸",
            "韩式蒸蛋",
            "韩式拌饭",
            "石锅拌饭",
            "韩式烤牛肉",
            "韩式烤五花肉",
            "韩式炸酱面",
            "韩式大酱汤",
            "韩式参鸡汤",
            "韩式泡菜汤",
            "韩式泡菜",
            "韩式腌萝卜",
            "韩式杂菜",
            "韩式辣炒年糕",
            "韩式炖排骨",
            "鲷鱼烧",
            "日式大福",
            "日式羊羹",
            "日式铜锣烧",
        ),
    },
    "4": {
        "label": "西餐与欧洲经典料理",
        "sections": ("国外",),
        "keywords": ("法式", "意大利", "意式", "西班牙", "英国", "德国", "爱尔兰", "瑞士", "希腊", "葡式"),
        "aliases": (
            "凯撒沙拉",
            "尼斯沙拉",
            "卡普雷塞沙拉",
            "惠灵顿牛排",
            "玛格丽特披萨",
            "那不勒斯披萨",
            "法式洋葱汤",
            "马赛鱼汤",
            "意大利蔬菜汤",
            "西班牙冷汤",
            "德国土豆汤",
            "新英格兰蛤蜊浓汤",
            "奶油蘑菇汤",
            "维也纳炸牛排",
            "西西里炸饭团",
            "炸鱼薯条",
            "西班牙海鲜饭",
            "意大利烩饭",
            "希腊焗通心粉",
            "德式奶酪面疙瘩",
            "法式可丽饼",
            "意大利佛卡夏",
            "法式马卡龙",
            "法式可丽露",
            "法式舒芙蕾",
            "提拉米苏",
            "意式奶冻",
            "葡式蛋挞",
            "意式冰淇淋",
        ),
    },
    "5": {
        "label": "中东北非料理",
        "sections": ("国外",),
        "keywords": ("土耳其", "黎巴嫩", "伊朗", "以色列", "埃及", "摩洛哥", "突尼斯", "阿尔及利亚", "中东"),
        "aliases": (
            "塔布勒沙拉",
            "鹰嘴豆泥",
            "巴巴加努什",
            "穆哈马拉",
            "法图什沙拉",
            "拉布内",
            "酸奶黄瓜酱",
            "恰齐克",
            "雪克舒卡",
            "胡恩卡尔贝延迪",
            "法拉费",
            "基贝",
            "布里克",
            "中东科夫塔烤肉串",
            "摩洛哥塔吉锅",
            "哈里拉汤",
            "科沙里",
            "曼萨夫",
            "巴克拉瓦",
            "库纳法",
            "玛阿穆尔",
            "巴斯布萨",
            "乌姆阿里",
            "土耳其软糖",
            "土耳其米布丁",
        ),
    },
    "6": {
        "label": "东欧料理",
        "sections": ("国外",),
        "keywords": ("俄罗斯", "乌克兰", "波兰", "匈牙利", "捷克", "罗马尼亚", "格鲁吉亚", "亚美尼亚", "阿塞拜疆"),
        "aliases": (
            "奥利维耶沙拉",
            "俄式甜菜沙拉",
            "皮毛大衣沙拉",
            "罗宋汤",
            "匈牙利牛肉汤",
            "俄式土豆饼",
            "波兰土豆饼",
            "捷克炸奶酪",
            "俄罗斯鱼汤",
            "波兰酸黑麦汤",
            "波兰酸菜汤",
            "捷克蒜汤",
            "罗马尼亚牛肚汤",
            "罗马尼亚肉丸酸汤",
            "格鲁吉亚哈乔汤",
            "亚美尼亚酸奶大麦汤",
            "俄罗斯荞麦粥",
            "乌克兰荞麦粥",
            "波兰蘑菇荞麦饭",
            "罗马尼亚抓饭",
            "俄罗斯饺子",
            "波兰饺子",
            "格鲁吉亚饺子",
            "乌克兰樱桃饺",
            "波兰奶酪饺",
            "俄罗斯布林饼",
            "波兰薄饼",
            "匈牙利兰戈斯",
            "罗马尼亚奶酪馅饼",
            "俄罗斯蜂蜜蛋糕",
            "捷克烟囱卷",
            "罗马尼亚奶酪甜甜圈",
            "俄罗斯拿破仑蛋糕",
            "格鲁吉亚果仁糖串",
            "亚美尼亚加塔饼",
        ),
    },
    "7": {
        "label": "拉美料理",
        "sections": ("国外",),
        "keywords": ("墨西哥", "秘鲁", "巴西", "阿根廷", "智利", "古巴", "哥伦比亚", "委内瑞拉", "厄瓜多尔", "玻利维亚", "波多黎各", "巴拉圭", "萨尔瓦多"),
        "aliases": (
            "酸橘汁腌鱼",
            "墨西哥法士达",
            "墨西哥牧场蛋",
            "古巴碎牛肉",
            "巴西法罗法",
            "墨西哥鼹酱鸡",
            "墨西哥辣椒炖牛肉",
            "秘鲁黄椒鸡",
            "巴西黑豆炖肉",
            "巴西海鲜炖锅",
            "阿根廷玉米炖肉",
            "智利玉米派",
            "古巴旧衣服牛肉",
            "厄瓜多尔椰奶炖鱼",
            "墨西哥玉米粽",
            "秘鲁塔马尔",
            "委内瑞拉哈亚卡",
            "哥伦比亚塔马尔",
            "智利玉米蒸包",
            "墨西哥炸玉米卷",
            "阿根廷米兰萨炸牛排",
            "古巴炸青蕉",
            "波多黎各莫丰戈",
            "墨西哥烤玉米",
            "秘鲁炭烤鸡",
            "阿根廷青酱牛排",
            "古巴烤乳猪",
            "巴西烤香肠",
            "哥伦比亚烤牛肉串",
            "墨西哥玉米饼汤",
            "墨西哥波索莱汤",
            "秘鲁鸡肉香菜汤",
            "哥伦比亚鸡肉土豆汤",
            "巴西黑豆汤",
            "玻利维亚花生汤",
            "智利海鲜汤",
            "古巴黑豆汤",
            "委内瑞拉牛肚汤",
            "古巴莫罗斯饭",
            "秘鲁香菜鸡饭",
            "哥伦比亚椰子饭",
            "委内瑞拉鸡肉饭",
            "秘鲁青酱面",
            "秘鲁塔亚林萨尔塔多",
            "墨西哥干面",
            "萨尔瓦多普普萨",
            "委内瑞拉阿雷帕",
            "哥伦比亚阿雷帕",
            "阿根廷恩潘纳达",
            "智利恩潘纳达",
            "墨西哥克萨迪亚",
            "巴拉圭玉米面包",
            "墨西哥吉拿棒",
            "墨西哥三奶蛋糕",
            "墨西哥焦糖布丁",
            "巴西布里加德罗",
            "阿根廷焦糖牛奶夹心饼",
            "秘鲁紫玉米布丁",
        ),
    },
}

_RUNTIME_CONFIG_LOADED = False


def build_recipe_system_prompt(ad_copy: str, fixed_dish_name: str) -> str:
    return f"""
你是阿叶造新菜账号的原创融合新菜研发编辑，负责把用户给出的菜名想法或口味灵感，扩写成一张竖版一页菜谱海报所需的完整中文文案。

你的目标不是复述常见菜谱，而是做出“原创融合新菜研发”风格的新菜：
1. 成品必须像市面上少见但逻辑成立、家庭和小店都能复刻的原创菜。
2. 如果用户给的是常见菜名，也要基于做法、口感、卖点、结构、器皿或场景做明显优化，但最终菜名必须保持用户输入原样。
3. 要优先吸收用户的补充说明，例如摆盘、器皿、核心调味、关键操作和口感方向。
4. 全部配方与步骤统一使用 g、ml、L、分钟 这类物理量单位，不要使用“适量、少许、1勺”这类模糊表达。
5. 默认按 2 人份来写，步骤控制在 3 到 5 步之间，按这道菜本身判断最刚好的步数，越少越好，但不能省掉关键操作，最多只能 5 步。
6. 成败关键固定输出 5 条短句，每条都不要使用逗号、句号、顿号等标点。
7. 底部关注文案必须固定为：{ad_copy}
8. 底部收藏文案必须固定为：{DEFAULT_COLLECTION_COPY}
9. 首图上半部不要再额外设置收藏提示细条，避免和底部收藏关注横条重复。
10. 最终菜名必须严格等于：{fixed_dish_name}
11. 主画面必须指定一种最合适的真实餐具或上桌工具，并写出“有人手持餐具与主菜互动的瞬间”；不要给后续文生图阶段列举多个餐具备选，也不要写“自行安排”“例如”这类模板话。

输出必须严格遵循下面这份纯文本结构，不要添加解释，不要使用 Markdown 代码块：

【基础定位】
创意来源：...
最终菜名：{fixed_dish_name}
引导句：...
副标题：...
账号定位：原创融合新菜研发

【主画面说明】
器皿与摆盘：...
桌面与环境：...
背景陪衬：...
主画面食材：...
汤汁或酱体：...
质感重点：...
动态动作：...
色彩点缀：...

【2人份食材】
主料
- 食材名 数量
- 食材名 数量

配菜
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

【3步出锅】或【4步出锅】或【5步出锅】
上面这个区块标题只能三选一，数字必须和实际步骤条数完全一致。
1. 标题：...
内容：...
2. 标题：...
内容：...
3. 标题：...
内容：...
如果你判断这道菜需要第 4 步或第 5 步，再继续往下写；如果 3 步已经刚好，就不要硬凑更多步骤。

【底部文案】
收藏文案：{DEFAULT_COLLECTION_COPY}
关注文案：{ad_copy}

额外要求：
1. 标题要有明显带动性句式，但主标题菜名必须直接使用用户输入的固定菜名，不能改字，不能另起新名。
2. 引导句必须写成高变化的抖音美食短钩子，允许自由发挥成场景钩子、情绪钩子、结果钩子、反差钩子或口感钩子，语感要像平台上容易被点开和收藏的活文案，而不是模板句。
3. 引导句短 6 到 10 个汉字，最多不超过 12 个汉字，不要标点，不要长句，不要解释型语气，不要把菜名再重复一遍；默认不要总写“周末请客”“家宴”“请客就做”“今天就做”“有面子”这类过度常见的老套词，除非用户补充说明里明确要求这种场景。
4. 副标题必须像成熟黄条卖点写法，压缩成 2 到 3 个短卖点，总长度控制在 24 个汉字以内，优先用空格隔开，不要写成长句。
5. 引导句和副标题整体都要更像抖音美食平台会用的轻口语短句，可以更鲜活、更俏皮、更有反应感，但不要油腻，不要为了押韵硬凑词。
6. 引导句和副标题不能复用同一组词，不能只是上下换行、加减空格或多减一个字。引导句负责提供钩子点，副标题负责提炼 2 到 3 个卖点词，两者语义必须明显分工。
7. 主画面说明要足够具体，让后续文生图阶段知道器皿、桌面、背景陪衬、主体食材、汤汁或酱体状态、画面颜色重点，还要明确唯一最合适的具体餐具、具体动作瞬间和是否少量手部入镜，不要再默认所有菜都是筷子夹起镜头。
8. 动态动作必须直接写成唯一方案，并明确是“有人手持餐具与主菜互动的瞬间”；不能写成“筷子或勺子都可以”“按菜品自行安排”这种把选择权继续丢给后续模型的句子。
9. 如果主菜属于酿、夹心、包裹、卷入、夹层、内馅这类里面还包着其它食材的结构，主画面必须明确写出有一块主菜已被人真实咬开或掰开，能清楚看见里面食材和汁水，不要只写完整外表。
10. 器皿与摆盘不仅要写器皿类型，还要写真实出菜状态；要按菜品具体写出类似“人为不完全整齐地码放”“自然错位堆叠”“疏密不一地铺开”“汤面配料自然浮沉”这类人手装盘痕迹，不能把所有菜都写成整齐复制模板。
11. 器皿与摆盘、桌面与环境、背景陪衬必须跟这道菜本身的烹饪方式、上桌逻辑、菜系气质和主料结构相匹配，不要默认每道菜都写成暖色陶盘、木托、木桌和几只小碗的同一套模板。
12. 如果这道菜更适合砂锅、深口汤碗、长鱼盘、铸铁盘、搪瓷盘、石面台、水磨石台面、亚麻餐垫或简洁厨房台面，就直接写清楚，不要偷懒回到同一种木桌陶盘。
13. 不同出餐结构的菜必须主动拉开场景，不要只是把同一套浅灰石面、水磨石、小圆碟、玻璃油壶模板换成黑盘或白盘后重复使用。煎鱼挂汁类、炸卷类、锅物类、冷盘类、铁板类的桌面材质、器皿逻辑和后景陪衬都应该明显不同。
14. 食材分组要清楚，数量要合理，不能互相打架；如果存在明显配菜，单独放进“配菜”分组，不要混在主料里。
15. 3 到 5 步必须前后顺序清晰，适合普通人照做；不要为了凑数把一个动作拆成两步，也不要为了省步数漏掉决定成败的关键环节。
16. 文字整体要像成熟抖音爆款图文海报，而不是教程论文或餐厅菜单。
17. 主画面食材要显式体现家庭厨房切配随机性：同一种食材允许有大有小、有长有短、厚薄不一的自然差异，不要做成工厂化毫米级统一切割。
""".strip()


def build_recipe_user_prompt(dish_idea: str, notes: str) -> str:
    note_text = notes or "无补充说明，请按原创融合新菜方向自行补齐。"
    return f"""
用户输入的菜名或创意：{dish_idea}
用户补充说明：{note_text}

请你基于这两部分信息，写出一整张一页菜谱海报所需的完整文案。
如果输入看起来像现有家常菜，也要把卖点、做法重点和画面表现提炼得更有记忆点，但最终菜名必须保持“{dish_idea}”完全不变。
顶部引导句请用抖音美食平台常见的高点击短钩子语感自由发挥，不要总落回“周末请客”“家宴”这类固定老词。
引导句和副标题不能复用同一组词，不能只是把同一串卖点放到菜名上方再重复一遍；引导句要像钩子，副标题要像拆开的卖点词。
主画面的器皿、桌面和背景陪衬必须跟菜本身匹配，不要默认写成木桌、暖色陶盘、木托和几只失焦小碗。
不同结构的菜请主动拉开场景，不要只是把同一套浅灰石面、水磨石和小圆碟背景换个盘色再复用。
主画面的动态动作必须直接指定唯一一种最合适的餐具或上桌工具，并写成“有人手持餐具与主菜互动的瞬间”；不要把筷子、勺子、刀叉、叉子、手抓写成多个备选方向。
如果主菜属于酿、夹心、包裹、卷入、夹层、内馅这类里面还有其它食材的结构，主画面必须明确写出有一块主菜被人真实咬开或掰开，能看见里面食材和汁水。
器皿与摆盘要写出真实的人手出菜状态，例如轻微错位、高低差、疏密变化、自然铺开或少量盘边汁痕，不要写成整齐复制模板。
""".strip()


def build_auto_dish_generation_system_prompt(region_label: str) -> str:
    return ""


def build_auto_dish_generation_user_prompt(
    region_label: str,
    reference_dish: str,
    region_samples: list[str],
    used_reference_dishes: list[str],
    used_generated_dishes: list[str],
    recent_history_restrictions: dict[str, tuple[str, ...]],
    retry_feedback: str = "",
) -> str:
    used_generated_text = join_auto_dish_history_items(recent_history_restrictions.get("recent_generated_dishes", ()))
    banned_ingredient_text = join_auto_dish_history_items(recent_history_restrictions.get("banned_ingredients", ()))
    banned_flavor_text = join_auto_dish_history_items(recent_history_restrictions.get("banned_flavors", ()))
    banned_method_text = join_auto_dish_history_items(recent_history_restrictions.get("banned_methods", ()))

    return f"""
参考传统菜“{reference_dish}（这里从@chuantongcaipu.txt 里提取）”，生成全新的市场上没有的新菜名并用简短的语言描述这道新菜的做法（食材，配菜，酱汁，烹饪流程和出菜摆盘），满足近10条的禁用规则（近10条历史新菜名：{used_generated_text}；近10条历史禁用主食材：{banned_ingredient_text}；近10条历史禁用口味：{banned_flavor_text}；近10条历史禁用主烹饪方式：{banned_method_text}），你创造的菜名必须搜索确保市面上是没有的，你可以更换食材，口味和烹饪流程来创造全新菜，要求跟关联的文件里的菜品没有重复，生成的菜名控制在3至8个汉子以内，食材要用大众有认知度的，不能太离谱和偏离大众的认知，同时杜绝象征式的比喻式的隐喻式的菜名。
输出格式：第一行为菜名。第二行为描述（不要空行也不要空段）。
""".strip()


def strip_inline_env_comment(raw_value: str) -> str:
    result_chars: list[str] = []
    quote_char = ""

    for char in raw_value:
        if quote_char:
            if char == quote_char:
                quote_char = ""
            result_chars.append(char)
            continue

        if char in {'"', "'"}:
            quote_char = char
            result_chars.append(char)
            continue

        if char == "#":
            break

        result_chars.append(char)

    return "".join(result_chars).strip()


def parse_env_file(env_file: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not env_file.exists():
        return parsed

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_inline_env_comment(value).strip().strip('"').strip("'")
        if key:
            parsed[key] = value

    return parsed


def load_env_file(env_file: Path, overwrite: bool = False) -> None:
    for key, value in parse_env_file(env_file).items():
        if overwrite or key not in os.environ:
            os.environ[key] = value


def ensure_runtime_config_loaded() -> None:
    global _RUNTIME_CONFIG_LOADED
    if _RUNTIME_CONFIG_LOADED:
        return

    existing_keys = set(os.environ.keys())
    merged_values: dict[str, str] = {}
    merged_values.update(parse_env_file(DEFAULT_CONFIG_FILE))
    merged_values.update(parse_env_file(ROOT_DIR / ".env"))

    for key, value in merged_values.items():
        if key not in existing_keys:
            os.environ[key] = value

    _RUNTIME_CONFIG_LOADED = True


def parse_bool_env_value(env_name: str, raw_value: str | None, default: bool) -> bool:
    normalized = (raw_value or ("1" if default else "0")).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{env_name} 只支持 1/0/true/false/on/off。")


def parse_dual_switch_env_value(
    env_name: str,
    raw_value: str | None,
    enabled_value: str,
    disabled_value: str,
) -> bool:
    normalized = (raw_value or enabled_value).strip()
    if normalized == enabled_value:
        return True
    if normalized == disabled_value:
        return False
    raise RuntimeError(f"{env_name} 只支持 {enabled_value} 或 {disabled_value}。")


def is_auto_dish_generation_enabled() -> bool:
    ensure_runtime_config_loaded()
    return parse_bool_env_value(
        env_name="AUTO_GENERATE_DISH_IDEA",
        raw_value=os.getenv("AUTO_GENERATE_DISH_IDEA"),
        default=DEFAULT_AUTO_DISH_GENERATION_ENABLED,
    )


def is_photoshop_auto_composite_enabled() -> bool:
    ensure_runtime_config_loaded()
    return parse_dual_switch_env_value(
        env_name="PHOTOSHOP_AUTO_COMPOSITE",
        raw_value=os.getenv("PHOTOSHOP_AUTO_COMPOSITE"),
        enabled_value="1",
        disabled_value="2",
    )


def is_publish_auto_select_enabled() -> bool:
    ensure_runtime_config_loaded()
    return parse_dual_switch_env_value(
        env_name="PUBLISH_AUTO_SELECT",
        raw_value=os.getenv("PUBLISH_AUTO_SELECT"),
        enabled_value="1",
        disabled_value="2",
    )


def get_auto_dish_region_code() -> str:
    ensure_runtime_config_loaded()
    region_code = os.getenv("AUTO_DISH_CUISINE_MODE", DEFAULT_AUTO_DISH_REGION_CODE).strip() or DEFAULT_AUTO_DISH_REGION_CODE
    if region_code not in AUTO_DISH_REGION_PROFILES:
        supported = ", ".join(sorted(AUTO_DISH_REGION_PROFILES.keys()))
        raise RuntimeError(f"AUTO_DISH_CUISINE_MODE 只支持这些数字：{supported}。")
    return region_code


def resolve_runtime_path(env_name: str, default_path: Path) -> Path:
    ensure_runtime_config_loaded()
    raw_value = os.getenv(env_name, default_path.name).strip() or default_path.name
    path = Path(raw_value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def get_auto_dish_library_file() -> Path:
    return resolve_runtime_path("AUTO_DISH_LIBRARY_FILE", DEFAULT_TRADITIONAL_DISH_FILE)


def get_auto_dish_memory_file() -> Path:
    return resolve_runtime_path("AUTO_DISH_MEMORY_FILE", DEFAULT_AUTO_DISH_MEMORY_FILE)


def build_image_client() -> OpenAI:
    ensure_runtime_config_loaded()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请先在 .env 文件中配置。")

    request_timeout = get_request_timeout_seconds()

    return OpenAI(api_key=api_key, timeout=request_timeout)


def build_text_http_timeout(read_timeout: float) -> httpx.Timeout:
    phase_timeout = min(read_timeout, DEFAULT_TEXT_CONNECT_TIMEOUT_SECONDS)
    return httpx.Timeout(connect=phase_timeout, read=read_timeout, write=phase_timeout, pool=phase_timeout)


def get_text_provider() -> str:
    ensure_runtime_config_loaded()
    provider = os.getenv("TEXT_API_PROVIDER", DEFAULT_TEXT_PROVIDER).strip().lower()
    if provider not in {"doubao", "openai"}:
        raise RuntimeError("TEXT_API_PROVIDER 只支持 doubao 或 openai。")
    return provider


def build_text_client() -> OpenAI:
    ensure_runtime_config_loaded()
    request_timeout = get_text_request_timeout_seconds()
    http_timeout = build_text_http_timeout(request_timeout)
    provider = get_text_provider()

    if provider == "doubao":
        doubao_api_key = os.getenv("DOUBAO_API_KEY", "").strip()
        if not doubao_api_key:
            raise RuntimeError("未找到 DOUBAO_API_KEY，请先在 .env 文件中配置。")
        base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
        return OpenAI(api_key=doubao_api_key, base_url=base_url, timeout=http_timeout)

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请先在 .env 文件中配置。")
    return OpenAI(api_key=openai_api_key, timeout=http_timeout)


def get_request_timeout_seconds() -> float:
    ensure_runtime_config_loaded()
    timeout_text = os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "900").strip() or "900"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def is_timeout_error(exc: Exception) -> bool:
    for error in iter_exception_chain(exc):
        if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
            return True

        message = str(error).lower()
        if "timed out" in message or "timeout" in message:
            return True

    return False


def iter_exception_chain(exc: BaseException) -> list[BaseException]:
    pending: list[BaseException] = [exc]
    chain: list[BaseException] = []
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue

        seen.add(current_id)
        chain.append(current)

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None and context is not cause:
            pending.append(context)

    return chain


def close_openai_client(client: OpenAI | None) -> None:
    if client is None:
        return

    close_method = getattr(client, "close", None)
    if callable(close_method):
        try:
            close_method()
        except Exception:
            pass


def get_multimodal_review_model() -> str:
    ensure_runtime_config_loaded()
    return os.getenv("DOUBAO_REVIEW_MODEL", "").strip() or get_text_model()


def build_multimodal_review_client() -> OpenAI:
    ensure_runtime_config_loaded()
    doubao_api_key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not doubao_api_key:
        return build_text_client()

    request_timeout = get_text_request_timeout_seconds()
    http_timeout = build_text_http_timeout(request_timeout)
    base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
    return OpenAI(api_key=doubao_api_key, base_url=base_url, timeout=http_timeout)


def is_retriable_text_request_error(exc: Exception) -> bool:
    if is_timeout_error(exc):
        return True

    for error in iter_exception_chain(exc):
        if isinstance(
            error,
            (
                httpx.TransportError,
                httpcore.NetworkError,
                httpcore.ProtocolError,
                APIConnectionError,
                InternalServerError,
                ssl.SSLError,
                ConnectionError,
                BrokenPipeError,
                EOFError,
            ),
        ):
            return True

        message = str(error).lower()
        if any(
            token in message
            for token in (
                "connection reset",
                "connection aborted",
                "connection refused",
                "server disconnected",
                "remote protocol error",
                "unexpected eof",
                "temporarily unavailable",
                "eof occurred in violation of protocol",
                "connection terminated unexpectedly",
                "connection closed",
                "tlsv1 alert",
            )
        ):
            return True

    return False


def get_text_request_error_label(exc: Exception) -> str:
    if is_timeout_error(exc):
        return "超时"
    return "连接异常"


def contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword in text for keyword in keywords)


SCENE_HINT_KEYWORDS: tuple[str, ...] = (
    "摆盘",
    "装盘",
    "码放",
    "平码",
    "摆入",
    "盛入",
    "盛在",
    "上桌",
    "平盘",
    "圆盘",
    "长盘",
    "白瓷盘",
    "瓷盘",
    "陶盘",
    "鱼盘",
    "深盘",
    "浅盘",
    "汤碗",
    "面碗",
    "饭盘",
    "饭碗",
    "砂锅",
    "陶煲",
    "锅仔",
    "炖盅",
    "铸铁盘",
    "木筛盘",
    "托盘",
    "餐垫",
    "桌面",
    "台面",
    "木桌",
    "石面",
    "水磨石",
    "亚麻",
    "背景",
    "后景",
    "陪衬",
    "小碗",
    "小碟",
    "锅盖",
)


def build_scene_inference_text(dish_name: str, notes: str) -> str:
    relevant_clauses: list[str] = []
    for clause in re.split(r"[，。；;、\n]+", notes):
        normalized_clause = " ".join(clause.split()).strip()
        if not normalized_clause:
            continue
        if contains_any(normalized_clause, SCENE_HINT_KEYWORDS) and normalized_clause not in relevant_clauses:
            relevant_clauses.append(normalized_clause)
    scene_notes = " ".join(relevant_clauses)
    return f"{dish_name} {scene_notes}".strip()


def should_infer_scene_field(field_value: str) -> bool:
    normalized_value = " ".join(field_value.split())
    if not normalized_value:
        return True
    if re.fullmatch(r"[.。…·、/／_\-\s]+", normalized_value):
        return True
    if normalized_value in {"待补", "待定", "同上", "略"}:
        return True
    return looks_like_placeholder_output(normalized_value)


def looks_like_template_scene_field(field_name: str, field_value: str) -> bool:
    normalized_value = " ".join(field_value.split())
    if not normalized_value:
        return False

    if field_name == "桌面与环境":
        if re.search(r"(浅灰|灰白|米白|暖白).{0,8}(水磨石|石面|石台面)", normalized_value) and re.search(
            r"(简洁|干净|克制|无杂物|不抢主菜)", normalized_value
        ):
            return True
        if normalized_value in {
            "浅灰色水磨石台面，简洁干净",
            "浅灰色水磨石台面，干净无杂物",
            "浅灰石面台面，干净克制，不抢主菜",
            "米白水磨石台面，保留生活化纹理",
        }:
            return True

    if field_name == "背景陪衬":
        if contains_any(normalized_value, ["无多余杂物", "干净无杂物", "简洁无杂物"]):
            return True
        if re.search(r"(一小碟|小味碟|半瓶).*(旁置|盘后|后景|虚化|失焦)", normalized_value):
            return True

    return False


FILLED_DISH_STRONG_KEYWORDS: tuple[str, ...] = (
    "酿",
    "夹心",
    "包心",
    "内馅",
    "馅料",
    "夹层",
    "包裹",
    "裹着",
    "裹入",
    "塞入",
    "填入",
    "填馅",
    "露馅",
    "露芯",
)
FILLED_DISH_STRUCTURE_KEYWORDS: tuple[str, ...] = (
    "卷",
    "盒",
    "饼",
    "豆腐",
    "腐皮",
    "春卷",
    "丸",
)
FILLED_DISH_CONTENT_KEYWORDS: tuple[str, ...] = (
    "肉",
    "鱼",
    "虾",
    "豆腐",
    "马蹄",
    "笋",
    "菌",
    "馅",
)
GENERIC_DYNAMIC_ACTION_PATTERNS: tuple[str, ...] = (
    r"按菜品结构自行安排",
    r"最合适的餐具和互动动作",
    r"餐具数量不限",
    r"可一只手或多只手",
    r"例如",
    r"不固定成",
    r"一只筷",
    r"另一只筷",
)


def is_filled_or_wrapped_dish(*text_parts: str) -> bool:
    combined_text = " ".join(part for part in text_parts if part).strip()
    if not combined_text:
        return False

    if contains_any(combined_text, list(FILLED_DISH_STRONG_KEYWORDS)):
        return True

    return contains_any(combined_text, list(FILLED_DISH_STRUCTURE_KEYWORDS)) and contains_any(
        combined_text,
        list(FILLED_DISH_CONTENT_KEYWORDS),
    )


def infer_plating_naturalness_phrase(
    dish_name: str,
    notes: str,
    plate_description: str = "",
    main_food_description: str = "",
) -> str:
    combined_text = " ".join(
        part for part in (dish_name, notes, plate_description, main_food_description) if part
    ).strip()

    if contains_any(combined_text, ["面条", "炒面", "拌面", "焖面", "汤面", "凉面", "意面", "拉面", "乌冬", "米线", "河粉", "米粉", "炒粉", "拌粉", "汤粉", "粉丝"]):
        return "面条与配料要像人手刚拌好或刚夹起前的自然堆叠状态，松紧不一，不要机械盘绕"
    if contains_any(combined_text, ["汤", "羹", "煲", "锅", "砂锅", "锅仔", "炖"]):
        return "主料和配料在汤汁里的分布要自然浮沉，疏密不匀，边缘保留少量真实汁痕"
    if is_filled_or_wrapped_dish(combined_text) or contains_any(combined_text, ["块", "卷", "丸", "豆腐"]):
        return "主菜以人手自然码放或堆叠，允许轻微错位、高低差、疏密变化和少量盘边汁痕，不要机械等距排列"
    return "摆盘要保留人手整理后的轻微不齐、前后错位和少量汁痕，不要整齐复制"


def normalize_plate_description(
    plate_description: str,
    dish_name: str,
    notes: str,
    main_food_description: str = "",
) -> str:
    normalized_description = " ".join(plate_description.split()).strip()
    if not normalized_description:
        normalized_description = infer_plate_description(dish_name, notes)

    if re.search(r"(人为|人手|不完全整齐|轻微错位|高低差|疏密|自然随机)", normalized_description):
        return normalized_description

    naturalness_phrase = infer_plating_naturalness_phrase(
        dish_name,
        notes,
        normalized_description,
        main_food_description,
    )
    connector = "，" if normalized_description and not normalized_description.endswith(("，", "。")) else ""
    return f"{normalized_description.rstrip('。')}{connector}{naturalness_phrase}".strip()


def looks_like_generic_dynamic_action(action_text: str) -> bool:
    normalized_action = " ".join(action_text.split()).strip()
    if not normalized_action:
        return True
    if looks_like_placeholder_output(normalized_action):
        return True
    if any(re.search(pattern, normalized_action) for pattern in GENERIC_DYNAMIC_ACTION_PATTERNS):
        return True
    return bool(
        re.search(r"(筷子|勺子|汤匙|大汤勺|刀叉|叉子|手抓|夹子).*(或|和|等|例如).*(筷子|勺子|汤匙|大汤勺|刀叉|叉子|手抓|夹子)", normalized_action)
    )


def infer_dynamic_action_description(
    dish_name: str,
    notes: str,
    plate_description: str = "",
    main_food_description: str = "",
) -> str:
    combined_text = " ".join(
        part for part in (dish_name, notes, plate_description, main_food_description) if part
    ).strip()
    filled_dish = is_filled_or_wrapped_dish(combined_text)

    if contains_any(combined_text, ["汉堡", "三明治", "夹饼", "夹馍", "卷饼", "塔可", "手抓饼"]):
        action_text = "有人手持主菜自然上手取食的瞬间，边缘保留真实按压痕迹"
    elif contains_any(combined_text, ["汤", "羹", "煲", "砂锅", "锅仔", "炖", "火锅"]):
        action_text = "有人手持汤勺舀起主菜与汤汁的瞬间，少量自然手部入镜"
    elif contains_any(combined_text, ["牛排", "菲力", "羊排", "猪排", "肋排", "排类"]):
        action_text = "有人手持刀叉切开并托起主菜切面的瞬间，少量自然手部入镜"
    elif contains_any(combined_text, ["意面", "通心粉", "螺旋面"]):
        action_text = "有人手持叉子卷起一口主菜与酱汁的瞬间，少量自然手部入镜"
    elif contains_any(combined_text, ["面条", "炒面", "拌面", "焖面", "汤面", "凉面", "拉面", "乌冬", "米线", "河粉", "米粉", "炒粉", "拌粉", "汤粉", "粉丝"]) and not contains_any(combined_text, ["意面"]):
        action_text = "有人手持餐具挑起一口主菜主体的瞬间，带出自然垂坠感，少量自然手部入镜"
    elif contains_any(combined_text, ["豆腐", "酿", "卷", "丸", "块", "鱼肉", "鱼片", "虾球"]):
        action_text = "有人手持餐具托起一块主菜主体的瞬间，少量自然手部入镜"
    else:
        action_text = "有人手持餐具与主菜互动的瞬间，突出最有食欲的一口，少量自然手部入镜"

    if filled_dish and not re.search(r"(咬|露出|露芯|露馅|剖开|掰开|切开)", action_text):
        action_text = f"{action_text.rstrip('。；，,')}，这块主菜已经被人真实咬开一口，能清楚看见里面包裹的食材层次和汁水"

    return action_text


def normalize_dynamic_action_description(
    action_text: str,
    dish_name: str,
    notes: str,
    plate_description: str = "",
    main_food_description: str = "",
) -> str:
    if looks_like_generic_dynamic_action(action_text):
        return infer_dynamic_action_description(
            dish_name,
            notes,
            plate_description,
            main_food_description,
        )

    normalized_action = " ".join(action_text.split()).strip()
    normalized_action = re.sub(r"一只\s*筷子?", "一双筷子", normalized_action)
    normalized_action = re.sub(r"另一只\s*筷子?", "另一双筷子", normalized_action)
    normalized_action = re.sub(r"一把\s*筷子", "一双筷子", normalized_action)
    if is_filled_or_wrapped_dish(dish_name, notes, plate_description, main_food_description) and not re.search(
        r"(咬|露出|露芯|露馅|剖开|掰开|切开)",
        normalized_action,
    ):
        normalized_action = f"{normalized_action.rstrip('。；，,')}，这块主菜已经被人真实咬开一口，能清楚看见里面包裹的食材层次和汁水"

    return normalized_action


def dedupe_items(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for name, amount in items:
        if name in seen:
            continue
        seen.add(name)
        deduped.append((name, amount))
    return deduped


def join_selling_points(points: list[str], max_chars: int = 24) -> str:
    filtered = [point.strip() for point in points if point.strip()]
    if not filtered:
        return "层次清楚 一盘就想配饭"

    chosen: list[str] = []
    for point in filtered:
        candidate = " ".join(chosen + [point]).strip()
        candidate_length = len(candidate.replace(" ", ""))
        if candidate_length <= max_chars or not chosen:
            chosen.append(point)

    return " ".join(chosen).strip()


TOFU_FAMILY_KEYWORDS: tuple[str, ...] = (
    "豆腐",
    "豆干",
    "豆皮",
    "腐竹",
    "千页豆腐",
    "豆泡",
    "豆腐泡",
    "冻豆腐",
)

PEA_FAMILY_KEYWORDS: tuple[str, ...] = (
    "黄豌豆",
    "耙豌豆",
)

SEARED_KEYWORDS: tuple[str, ...] = (
    "煎",
    "煎香",
    "焦香",
    "焦色",
    "焦边",
    "金黄",
    "金棕",
)

AROMA_PROFILE_KEYWORDS: tuple[str, ...] = (
    "黄油",
    "焦葱",
    "葱香",
    "蒜香",
    "椒麻",
    "青花椒",
    "花椒香",
    "孜然",
    "酱香",
    "豉香",
    "鲜香",
    "奶香",
)

SAUCE_PROFILE_KEYWORDS: tuple[str, ...] = (
    "酱",
    "汁",
    "挂汁",
    "裹汁",
    "收汁",
    "浓汁",
)

SEAFOOD_PROFILE_KEYWORDS: tuple[str, ...] = (
    "帆立贝",
    "带子",
    "扇贝",
    "贝",
    "虾",
    "鱿鱼",
    "蛤",
    "海鲜",
)


def pick_stable_variant(options: list[str], seed_text: str) -> str:
    if not options:
        return "这口鲜香太顶了"
    seed_value = sum((index + 1) * ord(char) for index, char in enumerate(seed_text))
    return options[seed_value % len(options)]


def sanitize_guide_line(text: str) -> str:
    cleaned = re.sub(r"[，。！？、；：,.!?:\s]+", "", text).strip()
    return cleaned[:12]


def normalize_marketing_text_for_comparison(text: str) -> str:
    return re.sub(r"[，。！？、；：,.!?:\s]+", "", text).strip()


def marketing_lines_too_similar(first_text: str, second_text: str) -> bool:
    normalized_first = normalize_marketing_text_for_comparison(first_text)
    normalized_second = normalize_marketing_text_for_comparison(second_text)

    if not normalized_first or not normalized_second:
        return False
    if normalized_first == normalized_second:
        return True

    shorter_text, longer_text = sorted((normalized_first, normalized_second), key=len)
    if len(shorter_text) >= 4 and shorter_text in longer_text and len(longer_text) - len(shorter_text) <= 2:
        return True

    return False


def looks_like_stale_guide_line(text: str) -> bool:
    normalized = sanitize_guide_line(text)
    if not normalized:
        return True
    return any(keyword in normalized for keyword in GUIDE_LINE_STALE_KEYWORDS)


def infer_guide_line(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    candidate_lines: list[str] = []

    if contains_any(combined_text, ["下饭", "拌饭", "配饭", "酱", "汁"]):
        candidate_lines.extend([
            "这盘太费米饭了",
            "米饭要先多煮点",
            "这一口太下饭了",
            "不配饭真的亏了",
        ])

    if contains_any(combined_text, ["汤", "锅", "煲", "炖"]):
        candidate_lines.extend([
            "这一锅热乎上头",
            "热气一冒就饿了",
            "天冷就馋这锅",
            "这一口暖到胃里",
        ])

    if contains_any(combined_text, ["脆", "焦", "煎", "炸"]):
        candidate_lines.extend([
            "这口脆香太勾人",
            "焦香一上来就馋",
            "这层焦香太会了",
            "一咬就知道会香",
        ])

    if contains_any(combined_text, ["麻", "辣", "椒", "鲜香", "香辣"]):
        candidate_lines.extend([
            "这口鲜麻太会了",
            "麻香一上来就馋",
            "这口香辣太上头",
            "闻着就想先夹块",
        ])

    if contains_any(combined_text, ["酸", "青柠", "梅子", "番茄", "清爽"]):
        candidate_lines.extend([
            "酸香一冒就饿了",
            "这口清爽太会了",
            "一入口就醒胃了",
            "越吃越想来一口",
        ])

    if contains_any(combined_text, ["牛", "羊", "鸡", "鸭", "虾", "海鲜", "排骨", "牛舌", "肘"]):
        candidate_lines.extend([
            "这盘硬菜太会了",
            "一上桌就先被夸",
            "这盘端上真镇场",
            "客人先问这啥菜",
        ])

    if not candidate_lines:
        candidate_lines.extend([
            "这口鲜香太顶了",
            "刚上桌就先空盘",
            "闻着就想先夹块",
            "这盘真的太会了",
            "一上桌就被夸了",
            "这盘端上就抢光",
        ])

    unique_candidates = list(dict.fromkeys(sanitize_guide_line(item) for item in candidate_lines if item.strip()))
    return pick_stable_variant(unique_candidates, seed_text=combined_text)


def extract_recipe_field_value(recipe_text: str, field_name: str) -> str:
    match = re.search(rf"^{re.escape(field_name)}：(.+)$", recipe_text, flags=re.M)
    if not match:
        return ""
    return match.group(1).strip()


def replace_recipe_field_value(recipe_text: str, field_name: str, new_value: str) -> str:
    pattern = rf"^{re.escape(field_name)}：.*$"
    return re.sub(pattern, f"{field_name}：{new_value}", recipe_text, count=1, flags=re.M)


def extract_main_ingredient_evidence(recipe_text: str) -> str:
    match = re.search(r"【2人份食材】\s*主料\s*(.*?)(?:\n\s*配菜|\n\s*香料)", recipe_text, flags=re.S)
    if not match:
        return ""

    lines: list[str] = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        lines.append(line.lstrip("-").strip())
    return " ".join(lines)


def extract_recipe_named_block(recipe_text: str, block_name: str) -> str:
    pattern = rf"【{re.escape(block_name)}】\s*(.*?)(?=\n【|\Z)"
    match = re.search(pattern, recipe_text, flags=re.S)
    return match.group(1).strip() if match else ""


def parse_recipe_item_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip().lstrip("-").strip()
    if not line:
        return None

    parts = line.rsplit(None, 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return line, ""


def extract_recipe_ingredient_groups(recipe_text: str) -> dict[str, list[tuple[str, str]]]:
    ingredients_block = extract_recipe_named_block(recipe_text, "2人份食材")
    grouped_lines: dict[str, list[str]] = {
        "主料": [],
        "配菜": [],
        "香料": [],
        "调味料": [],
    }

    current_group = ""
    for raw_line in ingredients_block.splitlines():
        line = raw_line.strip()
        if line in grouped_lines:
            current_group = line
            continue
        if not current_group or not line:
            continue
        grouped_lines[current_group].append(line)

    parsed_groups: dict[str, list[tuple[str, str]]] = {}
    for group_name, lines in grouped_lines.items():
        items: list[tuple[str, str]] = []
        for line in lines:
            parsed_item = parse_recipe_item_line(line)
            if not parsed_item:
                continue
            items.append(parsed_item)
        parsed_groups[group_name] = dedupe_items(items)

    return parsed_groups


def extract_recipe_numbered_items(block_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in block_text.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\d+\.\s*(.+)$", line)
        if match:
            items.append(match.group(1).strip())
    return items


def build_recipe_step_section_title(step_count: int) -> str:
    normalized_count = max(3, min(step_count, 5))
    return f"{normalized_count}步出锅"


def extract_recipe_step_section_title(recipe_text: str) -> str:
    match = re.search(r"【([3-5])步出锅】", recipe_text)
    if not match:
        return ""
    return build_recipe_step_section_title(int(match.group(1)))


def extract_recipe_steps(recipe_text: str) -> list[dict[str, str]]:
    step_block_title = extract_recipe_step_section_title(recipe_text) or build_recipe_step_section_title(5)
    step_block = extract_recipe_named_block(recipe_text, step_block_title)
    steps: list[dict[str, str]] = []
    current_step: dict[str, str] | None = None

    for raw_line in step_block.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        title_match = re.match(r"^\d+\.\s*标题：(.+)$", line)
        if title_match:
            if current_step:
                steps.append(current_step)
            normalized_title = title_match.group(1).strip().rstrip("：:;；，,.。")
            current_step = {
                "title": normalized_title,
                "content": "",
            }
            continue

        if current_step is None:
            continue

        if line.startswith("内容："):
            current_step["content"] = line.split("：", 1)[1].strip()
        elif current_step["content"]:
            current_step["content"] = f"{current_step['content']} {line}".strip()

    if current_step:
        steps.append(current_step)

    return steps


def build_recipe_bundle_from_recipe_text(recipe_text: str, fixed_dish_name: str, ad_copy: str) -> dict[str, Any]:
    normalized_recipe_text = recipe_text.strip()
    base_bundle = build_local_recipe_bundle(
        dish_name=fixed_dish_name,
        notes=normalized_recipe_text,
        ad_copy=ad_copy,
    )
    ingredient_groups = extract_recipe_ingredient_groups(normalized_recipe_text)
    parsed_tips = extract_recipe_numbered_items(extract_recipe_named_block(normalized_recipe_text, "成败关键"))
    parsed_steps = extract_recipe_steps(normalized_recipe_text)

    return {
        **base_bundle,
        "dish_name": extract_recipe_field_value(normalized_recipe_text, "最终菜名") or fixed_dish_name,
        "guide_line": extract_recipe_field_value(normalized_recipe_text, "引导句") or base_bundle["guide_line"],
        "subtitle": extract_recipe_field_value(normalized_recipe_text, "副标题") or base_bundle["subtitle"],
        "collection_hint": extract_recipe_field_value(normalized_recipe_text, "收藏提示") or base_bundle["collection_hint"],
        "collection_copy": extract_recipe_field_value(normalized_recipe_text, "收藏文案") or base_bundle["collection_copy"],
        "ad_copy": extract_recipe_field_value(normalized_recipe_text, "关注文案") or ad_copy,
        "plate": extract_recipe_field_value(normalized_recipe_text, "器皿与摆盘") or base_bundle["plate"],
        "table_setting": extract_recipe_field_value(normalized_recipe_text, "桌面与环境") or base_bundle["table_setting"],
        "background_props": extract_recipe_field_value(normalized_recipe_text, "背景陪衬") or base_bundle["background_props"],
        "main_food": extract_recipe_field_value(normalized_recipe_text, "主画面食材") or base_bundle["main_food"],
        "sauce": extract_recipe_field_value(normalized_recipe_text, "汤汁或酱体") or base_bundle["sauce"],
        "texture": extract_recipe_field_value(normalized_recipe_text, "质感重点") or base_bundle["texture"],
        "dynamic_action": extract_recipe_field_value(normalized_recipe_text, "动态动作") or base_bundle["dynamic_action"],
        "colors": extract_recipe_field_value(normalized_recipe_text, "色彩点缀") or base_bundle["colors"],
        "main_ingredients": ingredient_groups.get("主料") or base_bundle["main_ingredients"],
        "side_ingredients": ingredient_groups.get("配菜") or base_bundle.get("side_ingredients", []),
        "spices": ingredient_groups.get("香料") or base_bundle["spices"],
        "seasonings": ingredient_groups.get("调味料") or base_bundle["seasonings"],
        "tips": parsed_tips or base_bundle["tips"],
        "steps": parsed_steps or base_bundle["steps"],
        "notes": " ".join(normalized_recipe_text.split()),
    }


def build_subtitle_inference_text(recipe_text: str, fixed_dish_name: str) -> str:
    evidence_parts = [fixed_dish_name]
    for field_name in ("主画面食材", "汤汁或酱体", "质感重点"):
        field_value = extract_recipe_field_value(recipe_text, field_name)
        if field_value:
            evidence_parts.append(field_value)

    main_ingredient_text = extract_main_ingredient_evidence(recipe_text)
    if main_ingredient_text:
        evidence_parts.append(main_ingredient_text)

    return " ".join(part for part in evidence_parts if part).strip()


def normalize_generated_guide_line(recipe_text: str, fixed_dish_name: str) -> str:
    current_guide_line = extract_recipe_field_value(recipe_text, "引导句")
    if not current_guide_line:
        return recipe_text

    current_subtitle = extract_recipe_field_value(recipe_text, "副标题")
    cleaned_guide_line = sanitize_guide_line(current_guide_line)
    should_replace = (
        len(cleaned_guide_line) < 6
        or len(cleaned_guide_line) > 12
        or fixed_dish_name in cleaned_guide_line
        or looks_like_stale_guide_line(cleaned_guide_line)
        or marketing_lines_too_similar(cleaned_guide_line, current_subtitle)
    )

    replacement_line = infer_guide_line(fixed_dish_name, recipe_text) if should_replace else cleaned_guide_line
    return replace_recipe_field_value(recipe_text, "引导句", replacement_line)


def normalize_generated_subtitle(recipe_text: str, fixed_dish_name: str) -> str:
    current_subtitle = extract_recipe_field_value(recipe_text, "副标题")
    if not current_subtitle:
        return recipe_text

    combined_text = build_subtitle_inference_text(recipe_text, fixed_dish_name)
    has_wrong_tofu_point = (
        "豆腐两面煎焦香" in current_subtitle
        and not contains_any(combined_text, TOFU_FAMILY_KEYWORDS)
    )
    has_wrong_pea_point = (
        "黄豌豆压到软糯" in current_subtitle
        and not contains_any(combined_text, PEA_FAMILY_KEYWORDS)
    )
    if not has_wrong_tofu_point and not has_wrong_pea_point:
        return recipe_text

    replacement_subtitle = infer_subtitle(fixed_dish_name, combined_text)
    return replace_recipe_field_value(recipe_text, "副标题", replacement_subtitle)


def infer_subtitle(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    selling_points: list[str] = []

    has_pea_family = contains_any(combined_text, PEA_FAMILY_KEYWORDS)
    has_tofu_family = contains_any(combined_text, TOFU_FAMILY_KEYWORDS)
    has_seared_texture = contains_any(combined_text, SEARED_KEYWORDS)
    has_rich_aroma = contains_any(combined_text, AROMA_PROFILE_KEYWORDS)
    has_sauce_profile = contains_any(combined_text, SAUCE_PROFILE_KEYWORDS)

    if has_pea_family:
        selling_points.append("黄豌豆压到软糯")
    if has_tofu_family and has_seared_texture:
        selling_points.append("豆腐两面煎焦香")
    elif has_seared_texture:
        selling_points.append(
            pick_stable_variant(
                ["表面煎到焦香", "边缘煎到金黄", "煎香一口就记住"],
                combined_text,
            )
        )
    if contains_any(combined_text, ["肉沫", "猪肉"]):
        selling_points.append("肉香裹汁更有层次")
    if contains_any(combined_text, ["下饭", "拌饭", "配饭"]):
        selling_points.append("一盘超下饭")
    if contains_any(combined_text, ["酸", "梅子", "青柠", "柠檬", "柚香"]):
        selling_points.append("酸香提味更开胃")
    if has_rich_aroma:
        selling_points.append("咸香浓郁有记忆点")
    elif has_sauce_profile:
        selling_points.append("挂汁到位更入味")
    elif contains_any(combined_text, SEAFOOD_PROFILE_KEYWORDS):
        selling_points.append("鲜味越嚼越上头")

    return join_selling_points(selling_points)


def infer_plate_description(dish_name: str, notes: str) -> str:
    scene_text = build_scene_inference_text(dish_name, notes)
    combined_text = scene_text or dish_name

    if contains_any(scene_text, ["木筛盘", "食物纸"]):
        return "木筛盘加吸油食物纸，适合盛住焦香或干爽主料"
    if contains_any(scene_text, ["砂锅", "陶煲", "煲仔", "锅仔", "黑陶锅", "炖盅"]):
        return pick_stable_variant(
            [
                "厚壁砂锅或陶煲直接上桌，保留锅沿与热气",
                "深口陶煲直接出餐，锅边允许有轻微汁痕",
                "粗陶锅仔直接摆上桌面，器皿本身带真实使用感",
            ],
            combined_text,
        )
    if contains_any(scene_text, ["铁板", "铸铁", "煎盘"]):
        return pick_stable_variant(
            [
                "黑色铸铁盘直接上桌，边缘带一点真实煎痕",
                "厚重铁板或铸铁煎盘出餐，保留热盘质感",
                "深色煎盘承托主菜，边缘允许有少量酱汁痕迹",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["鲈鱼", "鱼片"]) and contains_any(combined_text, ["酸", "酸汤", "木姜子", "娃娃菜"]):
        return pick_stable_variant(
            [
                "浅口长形白瓷鱼盘，盘底先垫蔬菜丝再平码鱼片",
                "奶白窄边长盘承托鱼片和垫菜，酸酱沿盘底自然铺开",
                "暖白椭圆长盘盛放煎鱼片，盘中留出明显酱汁流线",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["里脊", "春卷皮"]) and contains_any(combined_text, ["卷"]) and contains_any(combined_text, ["炸", "煎", "黑椒", "脆"]):
        return pick_stable_variant(
            [
                "黑色长方煎盘或铁板，卷身平行摆放，盘底留少量黑椒酱",
                "深炭灰窄边长盘承托脆卷，卷与卷之间留出酥脆呼吸感",
                "磨砂黑釉长方盘盛放里脊卷，盘底只留薄薄一层椒香酱汁",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["拉面", "乌冬", "米线", "河粉", "意面", "炒面", "拌面", "面条", "汤面", "焖面", "凉面", "面食"]):
        if contains_any(combined_text, ["汤面", "拉面", "汤粉", "羹", "汤米线", "汤河粉"]):
            return pick_stable_variant(
                [
                    "深口汤碗或拉面碗，碗沿留足汤面空间",
                    "厚边深碗承托汤面和配料层次，碗口不要太小",
                    "暖白深口大碗，让汤头、面条和浇头同时清楚可见",
                ],
                combined_text,
            )
        return pick_stable_variant(
            [
                "宽口浅碗或低矮大盘，让面条自然卷起堆高",
                "大号浅盘承托拌面主体，方便把浇头铺开",
                "宽口石瓷盘装面，主料和酱汁能完整展开",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["饭", "烩饭", "炒饭", "丼", "抓饭", "科沙里", "曼萨夫"]):
        return pick_stable_variant(
            [
                "宽口浅碗或石瓷饭盘，让饭粒和主料层次清楚",
                "低矮饭碗配宽边盘，主料盖在米饭上方更集中",
                "深口饭盘承托米饭和主菜，边缘留有自然空区",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["鱼", "鮰鱼", "鲈鱼", "鳕鱼", "石斑", "鲳", "鱼头", "加吉鱼"]) and not contains_any(combined_text, ["鱼丸", "鱼面", "鱼香"]):
        if contains_any(combined_text, ["整条", "清蒸", "蒸鱼", "武昌鱼", "鲳鱼", "石斑"]):
            return pick_stable_variant(
                [
                    "长椭圆鱼盘，鱼身顺着盘势自然舒展开",
                    "白瓷长鱼盘承托整鱼，主鱼完整占满盘面主体",
                    "浅口椭圆鱼盘，鱼身和配料沿长边铺开",
                ],
                combined_text,
            )
        return pick_stable_variant(
            [
                "深口宽边陶盘，让鱼块和配料堆出明显层次",
                "浅口汤盘承住酱汁、鱼块和辅料，不要平铺过散",
                "厚边石瓷深盘盛放块状鱼肉，边缘保留少量汁痕",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["沙拉", "凉拌", "冷盘", "腌鱼"]):
        return pick_stable_variant(
            [
                "浅口白瓷盘，留出清爽边距，结构利落",
                "磨砂玻璃盘或冷白瓷盘，冷菜铺展但不散乱",
                "低矮浅盘承托冷菜主体，边缘干净简洁",
            ],
            combined_text,
        )
    if (
        contains_any(combined_text, ["腐皮", "豆皮", "酿"])
        or (contains_any(combined_text, ["卷"]) and contains_any(combined_text, ["蒸", "平盘", "白瓷盘", "上桌"]))
    ) and not contains_any(combined_text, ["春卷", "炸卷", "蛋卷", "炸", "煎", "脆", "黑椒", "里脊"]):
        return pick_stable_variant(
            [
                "白瓷平盘或浅口长盘盛放蒸卷，卷身平码但不呆板",
                "暖白浅盘承托腐皮卷，盘心保留少量蒸汁光泽",
                "简洁浅口圆盘承托酿卷主菜，边缘留出真实盘边空间",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["炸", "酥炸", "脆皮", "春卷", "天妇罗", "炸鸡", "锅包肉", "虾球", "丸子"]):
        return pick_stable_variant(
            [
                "浅色搪瓷盘或金属托盘，底下垫吸油纸",
                "宽口圆盘盛放炸物，主菜堆高但边缘留白",
                "金属网托配浅盘，突出刚出锅的脆感和轻油感",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["烤", "串", "牛排", "羊排", "猪排", "菲力", "披萨"]):
        return pick_stable_variant(
            [
                "厚边石瓷餐盘，保留烤后焦边和堆叠高度",
                "黑色铸铁盘或深色烤盘直接上桌，突出热感",
                "大号圆盘让主菜居中堆叠，边缘保留自然空区",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["汤", "羹", "煲", "炖", "锅"]):
        return pick_stable_variant(
            [
                "深口陶碗或锅仔直接上桌，汤面有明显热气",
                "厚壁汤碗稳住汤汁和配料层次，碗沿不过厚",
                "锅仔或深口炖盅直接出餐，保留沸感与汤色层次",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["丁", "块", "虾球", "丸", "豆腐", "菌菇"]):
        return pick_stable_variant(
            [
                "暖白深盘承托块状主料和酱汁，中心略微堆高",
                "宽口石瓷盘让块面主料铺开但不扁塌",
                "低矮深盘盛放主料，边缘允许保留少量汁痕",
            ],
            combined_text,
        )
    return pick_stable_variant(
        [
            "暖白厚边深盘，主菜集中在盘心区域",
            "灰釉石瓷浅盘，主料和酱汁自然铺开",
            "低矮圆盘承托主菜主体，留出真实盘边空间",
        ],
        combined_text or dish_name,
    )


def infer_table_description(dish_name: str, notes: str, plate_description: str) -> str:
    scene_text = build_scene_inference_text(dish_name, notes)
    combined_text = f"{scene_text} {plate_description}".strip()

    if contains_any(scene_text, ["大理石", "石面", "石桌"]):
        return "浅灰石面台面，表面干净但保留真实细纹"
    if contains_any(scene_text, ["不锈钢", "金属台"]):
        return "拉丝不锈钢备餐台面，反光克制且真实"
    if contains_any(scene_text, ["亚麻", "餐垫"]):
        return "素色亚麻餐垫铺在克制的台面上，不做花哨装饰"
    if contains_any(scene_text, ["木桌", "木纹", "木托"]):
        return "深胡桃木桌面，桌面保留轻微使用痕迹，不要过度布景"

    if contains_any(combined_text, ["鲈鱼", "鱼片"]) and contains_any(combined_text, ["酸", "酸汤", "木姜子", "娃娃菜"]):
        return pick_stable_variant(
            [
                "奶油白石英台面或浅米色微纹餐台，衬出酸酱与鱼片亮泽",
                "温润浅米石面台面，留出清爽餐桌气息，不落回灰色石面模板",
                "淡奶油色复合台面，画面更亮，突出白瓷长盘和红亮酸酱",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["里脊", "春卷皮"]) and contains_any(combined_text, ["卷"]) and contains_any(combined_text, ["炸", "煎", "黑椒", "脆"]):
        return pick_stable_variant(
            [
                "深炭灰石板台面或磨砂黑餐台，突出脆卷金黄和黑椒酱光泽",
                "胡桃木餐桌压一块深色耐热垫，让黑盘和酥卷更有层次",
                "偏深色的餐厅出菜台面，保留暖光与少量油润反射，不再落回浅灰水磨石",
            ],
            combined_text,
        )

    if contains_any(combined_text, ["意面", "牛排", "羊排", "猪排", "菲力", "烩饭", "披萨", "焗"]):
        return pick_stable_variant(
            [
                "浅灰石面或米白水磨石台面，配一块素色亚麻餐垫",
                "哑光浅石面台面，留出简洁餐桌氛围",
                "米白水磨石台面配窄边亚麻垫，不做多余装饰",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["拉面", "乌冬", "米线", "河粉", "意面", "炒面", "拌面", "面条", "汤面", "焖面", "凉面", "米粉", "炒粉", "拌粉", "汤粉"]):
        return pick_stable_variant(
            [
                "暖灰石面台面，干净克制，突出碗面主体",
                "浅色复古瓷砖台面，生活化但不花哨",
                "米白水磨石台面，让面碗和热气更突出",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["煲", "锅仔", "陶煲", "炖", "汤", "砂锅", "火锅"]):
        return pick_stable_variant(
            [
                "深色耐热餐台或胡桃木桌面，稳住锅感和热气",
                "哑光深灰石面，衬托锅边热气和汤汁反光",
                "复古深色餐台，表面留有真实光影和热菜氛围",
            ],
            combined_text,
        )
    if (
        contains_any(combined_text, ["腐皮", "豆皮", "酿"])
        or (contains_any(combined_text, ["卷"]) and contains_any(combined_text, ["蒸", "平盘", "白瓷盘", "上桌"]))
    ) and not contains_any(combined_text, ["春卷", "炸卷", "蛋卷"]):
        return pick_stable_variant(
            [
                "温润浅木餐桌或浅胡桃木台面，配素色餐垫，像热菜刚端上桌",
                "暖米色亚麻餐垫铺在哑光餐台上，清爽但有家常热气",
                "克制的浅木桌面，保留少量生活化光影，不做固定大理石模板",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["炸", "酥炸", "脆皮"]):
        return pick_stable_variant(
            [
                "暖白水磨石台面，突出炸物干爽脆感",
                "浅灰石面台面，反差克制，不抢炸物主体",
                "简洁厨房台面，生活化但不凌乱",
            ],
            combined_text,
        )
    if contains_any(combined_text, ["凉拌", "沙拉", "冷盘"]):
        return pick_stable_variant(
            [
                "浅米色石面台面，干净清爽，冷菜更显利落",
                "浅灰水磨石台面，克制地托住冷盘轮廓",
                "暖白复合台面，画面轻快但不空",
            ],
            combined_text,
        )
    return pick_stable_variant(
        [
            "浅灰石面台面，干净克制，不抢主菜",
            "温润浅木餐桌，保留真实生活化纹理",
            "素色亚麻餐垫铺在哑光台面上，氛围克制但不空",
        ],
        combined_text or dish_name,
    )


def infer_background_prop_description(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}".strip()
    props: list[str] = []

    def add_prop(prop: str) -> None:
        if prop not in props:
            props.append(prop)

    if contains_any(combined_text, ["鲈鱼", "鱼片"]) and contains_any(combined_text, ["酸", "酸汤", "木姜子", "娃娃菜"]):
        add_prop("一只装青蒜碎的浅白小碟轻微失焦")
        add_prop("半瓶无字木姜子油或细口量油玻璃瓶放在后侧")
    if contains_any(combined_text, ["里脊", "春卷皮"]) and contains_any(combined_text, ["卷"]) and contains_any(combined_text, ["炸", "煎", "黑椒", "脆"]):
        add_prop("一只小浅碟装黑胡椒碎或黑椒酱轻微失焦")
        add_prop("少量鲜笋丝或控油网架边缘退到后景")

    if contains_any(combined_text, ["葱", "葱花", "小葱", "葱白", "葱绿"]):
        add_prop("一小碟切段小葱或葱花失焦放在后景")
    if contains_any(combined_text, ["仔姜", "生姜", "姜丝", "姜片"]):
        add_prop("少量姜丝或姜片放在小味碟里做后景")
    if contains_any(combined_text, ["蒜", "蒜片", "蒜末"]):
        add_prop("一只小碟蒜片或蒜末轻微失焦")
    if contains_any(combined_text, ["辣", "小米辣", "辣椒", "干辣椒"]):
        add_prop("少量切圈红椒或辣椒段自然散在后景")
    if contains_any(combined_text, ["花椒", "青花椒", "藤椒"]):
        add_prop("一只浅味碟里的花椒粒轻微失焦")
    if contains_any(combined_text, ["菌菇", "香菇", "杏鲍菇", "蘑菇"]):
        add_prop("一只装着切片菌菇的小浅碗虚化放在后侧")
    if contains_any(combined_text, ["青柠", "柠檬", "酸橙"]):
        add_prop("半个切开的青柠或柠檬作为少量酸香陪衬")
    if contains_any(combined_text, ["鱼", "虾", "海鲜", "蛤蜊"]):
        add_prop("锅盖、小酱碟或少量海鲜辅料虚化放在远处")
    if contains_any(combined_text, ["拉面", "乌冬", "米线", "河粉", "意面", "炒面", "拌面", "面条", "汤面", "焖面", "凉面", "粉"]):
        add_prop("一只面碗边、筷架或少量原料小碗虚化出现在后景")
    if contains_any(combined_text, ["汤", "煲", "锅仔", "陶煲", "炖", "砂锅", "火锅"]):
        add_prop("锅盖、汤勺或小汤碗只保留一两件失焦存在")
    if contains_any(combined_text, ["炸", "酥", "脆"]):
        add_prop("一小碟蘸料或半片柠檬做轻量陪衬")
    if contains_any(combined_text, ["牛排", "意面", "烩饭", "披萨"]):
        add_prop("素色餐巾折角或烤盘边缘轻微出现在后景")

    if not props:
        if contains_any(combined_text, ["汤", "煲", "锅仔", "陶煲", "砂锅", "火锅"]):
            props.append("后景只留一只锅盖或小汤勺失焦点缀")
        elif contains_any(combined_text, ["拉面", "乌冬", "米线", "河粉", "意面", "炒面", "拌面", "面条", "汤面", "焖面", "凉面", "粉"]):
            props.append("后景只留一只原料小碗或筷架轻微失焦")
        else:
            props.append("后景只留与本菜直接相关的一两样原料小碗轻微失焦")

    selected_props = props[:3]
    return (
        f"后景只保留少量与本菜直接相关的失焦陪衬，例如{'、'.join(selected_props)}，"
        "不要默认摆整排干辣椒、整头蒜、木勺、香料碗和无关摆件"
    )


def infer_main_food_description(dish_name: str, notes: str) -> str:
    combined_text = f"{dish_name} {notes}"
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]) and contains_any(combined_text, ["豆腐", "肉沫"]):
        return "金黄焦边豆腐块裹着软糯耙豌豆和油润肉沫，盘中有明显颗粒层次和浓稠挂汁"
    if contains_any(combined_text, ["鸡肉", "鸡腿肉", "年糕"]):
        return "主菜堆叠有层次，鸡肉表面油亮，年糕边缘微焦，酱汁包裹均匀"
    return "主菜主体清楚，块面饱满，表面有自然挂汁和热气"


def infer_sauce_description(notes: str) -> str:
    if contains_any(notes, ["勾芡", "浓稠", "挂汁"]):
        return "酱汁浓稠，能明显挂在主菜表面，不是稀汤感"
    if contains_any(notes, ["汤", "清水", "煲"]):
        return "汤汁饱满但不浑浊，有热气和油亮反光"
    return "酱汁均匀包裹主菜，画面有热气和油亮感"


def infer_texture_description(notes: str) -> str:
    if contains_any(notes, ["软糯", "粘性强"]):
        return "主菜同时呈现软糯、焦香、挂汁三种口感层次"
    if contains_any(notes, ["脆", "焦"]):
        return "表面微焦微脆，内部保留湿润和饱满质感"
    return "质感真实自然，不能像塑料模型"


def infer_color_description(notes: str) -> str:
    if contains_any(notes, ["老抽", "黄豆酱"]):
        return "主色为焦糖棕、暖金黄和葱花鲜绿"
    if contains_any(notes, ["青柠", "梅子"]):
        return "主色为暖金黄、青绿色和少量酸香色点缀"
    return "主色为暖奶白、橙金和焦糖棕"


def infer_main_ingredients(dish_name: str, notes: str) -> list[tuple[str, str]]:
    combined_text = f"{dish_name} {notes}"
    ingredients: list[tuple[str, str]] = []
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]):
        ingredients.append(("黄豌豆", "250g"))
    if "豆腐" in combined_text:
        ingredients.append(("老豆腐", "400g"))
    if contains_any(combined_text, ["肉沫", "猪肉"]):
        ingredients.append(("猪肉沫", "150g"))
    if contains_any(combined_text, ["鸡腿肉", "鸡肉"]):
        ingredients.append(("鸡腿肉", "300g"))
    if "年糕" in combined_text:
        ingredients.append(("年糕片", "220g"))
    if "虾" in combined_text:
        ingredients.append(("鲜虾", "250g"))
    if not ingredients:
        ingredients.append((dish_name, "300g"))
    return dedupe_items(ingredients)


def infer_side_ingredients(notes: str) -> list[tuple[str, str]]:
    side_candidates: list[tuple[tuple[str, ...], tuple[str, str]]] = [
        (("土豆",), ("土豆", "200g")),
        (("青椒", "大青椒"), ("大青椒", "120g")),
        (("洋葱",), ("洋葱", "120g")),
        (("芹菜",), ("芹菜", "100g")),
        (("胡萝卜",), ("胡萝卜", "100g")),
        (("杏鲍菇",), ("杏鲍菇", "120g")),
        (("香菇",), ("香菇", "100g")),
    ]
    sides: list[tuple[str, str]] = []
    for keywords, item in side_candidates:
        if any(keyword in notes for keyword in keywords):
            sides.append(item)
    return dedupe_items(sides)


def infer_spice_ingredients(notes: str) -> list[tuple[str, str]]:
    spices: list[tuple[str, str]] = []
    if "葱花" in notes:
        spices.append(("葱花", "10g"))
    if contains_any(notes, ["青花椒", "花椒"]):
        spices.append(("青花椒", "4g"))
    if "藤椒" in notes:
        spices.append(("藤椒", "4g"))
    if "薄荷" in notes:
        spices.append(("薄荷碎", "3g"))
    if not spices:
        spices.append(("葱花", "10g"))
    return dedupe_items(spices)


def infer_seasoning_ingredients(notes: str) -> list[tuple[str, str]]:
    seasonings: list[tuple[str, str]] = []
    if "黄豆酱" in notes:
        seasonings.append(("黄豆酱", "25g"))
    if "老抽" in notes:
        seasonings.append(("老抽", "10ml"))
    if "盐" in notes:
        seasonings.append(("盐", "4g"))
    if "味精" in notes:
        seasonings.append(("味精", "2g"))
    if "鸡精" in notes:
        seasonings.append(("鸡精", "2g"))
    if contains_any(notes, ["水", "清水"]):
        seasonings.append(("清水", "300ml"))
    if contains_any(notes, ["勾芡", "淀粉"]):
        seasonings.append(("水淀粉", "20ml"))
    if not seasonings:
        seasonings.extend([
            ("盐", "4g"),
            ("生抽", "15ml"),
            ("清水", "250ml"),
        ])
    return dedupe_items(seasonings)


def infer_key_tips(dish_name: str, notes: str) -> list[str]:
    combined_text = f"{dish_name} {notes}"
    tips: list[str] = []
    has_pea_family = contains_any(combined_text, PEA_FAMILY_KEYWORDS)
    has_tofu_family = contains_any(combined_text, TOFU_FAMILY_KEYWORDS)
    has_seared_texture = contains_any(combined_text, SEARED_KEYWORDS)

    if has_pea_family:
        tips.append("黄豌豆一定先压到软糯")
    if has_tofu_family and has_seared_texture:
        tips.append("豆腐先煎出焦边再合炒")
    elif has_seared_texture:
        tips.append("先把表面煎出焦香再合味")
    if contains_any(notes, ["肉沫", "猪肉"]):
        tips.append("肉沫先炒香再并入主菜")
    if has_tofu_family and contains_any(notes, ["水到豆腐的一半", "加水到豆腐的一半"]):
        tips.append("加水别没过豆腐")
    if contains_any(notes, ["勾芡", "浓稠"]):
        tips.append("最后勾芡把汁收到位")
    if not tips:
        tips.extend([
            "主料火候要先做透",
            "酱汁要能挂住主料",
            "出锅前再补最终香气",
        ])
    return tips[:5]


def infer_steps(dish_name: str, notes: str) -> list[dict[str, str]]:
    combined_text = f"{dish_name} {notes}"
    if contains_any(combined_text, ["耙豌豆", "黄豌豆"]) and contains_any(combined_text, ["豆腐", "肉沫"]):
        return [
            {
                "title": "压豌豆",
                "content": "黄豌豆加清水没过，电高压锅压30分钟，压到颗粒松软带明显糯感",
            },
            {
                "title": "炒肉沫",
                "content": "锅中下猪肉沫150g炒散，加入黄豆酱25g、老抽10ml炒到肉香和酱香完全融合",
            },
            {
                "title": "煎豆腐",
                "content": "老豆腐切厚块，下锅煎到两面微金黄，边缘有轻微焦香感再盛出",
            },
            {
                "title": "合炒烧味",
                "content": "把耙豌豆、肉沫和豆腐回锅同炒，加入盐4g、味精2g、鸡精2g和清水300ml，大火烧5分钟",
            },
            {
                "title": "勾芡出锅",
                "content": "最后淋入水淀粉20ml把汁收浓，撒葱花10g，装入木筛盘食物纸上桌",
            },
        ]

    if contains_any(combined_text, ["凉拌", "冷拌", "沙拉", "白灼", "快手", "蘸水", "凉菜"]):
        return [
            {"title": "备主料", "content": "先把主料和关键配料处理干净，切到最适合入口和拌匀的状态"},
            {"title": "调主味", "content": "把决定风味走向的底味先调好，再让主料均匀裹上或吸住味道"},
            {"title": "收口上桌", "content": "最后补上决定香气和口感的点睛料，拌匀或装盘后立刻上桌"},
        ]

    if contains_any(combined_text, ["煲", "锅", "焖", "炖", "卤", "焗", "炸", "卷", "酿", "塔", "夹饼", "慢煮", "烧", "烤"]):
        return [
            {"title": "备主料", "content": "先把主料和核心配料按出菜顺序处理干净备用"},
            {"title": "做底味", "content": "先把香味和底味炒出来，让主菜后续更容易挂味"},
            {"title": "处理主菜", "content": "把主菜做到七八成熟，保留最关键的口感层次"},
            {"title": "合味收汁", "content": "把主料和调味重新合在一起，让汤汁或酱汁均匀包裹主菜"},
            {"title": "出锅装盘", "content": "最后补香并装盘，保留热气、亮泽和最强食欲感"},
        ]

    return [
        {"title": "备主料", "content": "先把主料和核心配料按出菜顺序处理干净备用"},
        {"title": "做底味", "content": "先把香味和底味炒出来，让主菜后续更容易挂味"},
        {"title": "处理主菜", "content": "把主菜做到七八成熟，保留最关键的口感层次"},
        {"title": "合味出锅", "content": "把主料和调味重新合在一起，收住味道和状态后立刻装盘上桌"},
    ]

def build_local_recipe_bundle(dish_name: str, notes: str, ad_copy: str) -> dict[str, Any]:
    normalized_notes = " ".join(notes.split())
    main_food_description = infer_main_food_description(dish_name, normalized_notes)
    plate_description = normalize_plate_description(
        infer_plate_description(dish_name, normalized_notes),
        dish_name,
        normalized_notes,
        main_food_description,
    )
    table_description = infer_table_description(dish_name, normalized_notes, plate_description)
    background_prop_description = infer_background_prop_description(dish_name, normalized_notes)
    dynamic_action_description = infer_dynamic_action_description(
        dish_name,
        normalized_notes,
        plate_description,
        main_food_description,
    )
    return {
        "dish_name": dish_name,
        "guide_line": infer_guide_line(dish_name, normalized_notes),
        "subtitle": infer_subtitle(dish_name, normalized_notes),
        "collection_hint": DEFAULT_COLLECTION_HINT,
        "collection_copy": DEFAULT_COLLECTION_COPY,
        "ad_copy": ad_copy,
        "plate": plate_description,
        "table_setting": table_description,
        "background_props": background_prop_description,
        "main_food": main_food_description,
        "sauce": infer_sauce_description(normalized_notes),
        "texture": infer_texture_description(normalized_notes),
        "dynamic_action": dynamic_action_description,
        "colors": infer_color_description(normalized_notes),
        "main_ingredients": infer_main_ingredients(dish_name, normalized_notes),
        "side_ingredients": infer_side_ingredients(normalized_notes),
        "spices": infer_spice_ingredients(normalized_notes),
        "seasonings": infer_seasoning_ingredients(normalized_notes),
        "tips": infer_key_tips(dish_name, normalized_notes),
        "steps": infer_steps(dish_name, normalized_notes),
        "notes": normalized_notes,
    }


def render_recipe_bundle_text(bundle: dict[str, Any]) -> str:
    main_ingredients = "\n".join(f"- {name} {amount}" for name, amount in bundle["main_ingredients"])
    side_ingredients = "\n".join(f"- {name} {amount}" for name, amount in bundle.get("side_ingredients", []))
    spices = "\n".join(f"- {name} {amount}" for name, amount in bundle["spices"])
    seasonings = "\n".join(f"- {name} {amount}" for name, amount in bundle["seasonings"])
    tips = "\n".join(f"{index}. {tip}" for index, tip in enumerate(bundle["tips"], start=1))
    steps = []
    for index, step in enumerate(bundle["steps"], start=1):
        steps.append(f"{index}. 标题：{step['title']}\n内容：{step['content']}")
    step_section_title = build_recipe_step_section_title(len(bundle["steps"]))

    return f"""
【基础定位】
创意来源：根据用户输入菜名和补充说明整理为可直接出图的一页菜谱
最终菜名：{bundle['dish_name']}
引导句：{bundle['guide_line']}
副标题：{bundle['subtitle']}
账号定位：原创融合新菜研发

【主画面说明】
器皿与摆盘：{bundle['plate']}
桌面与环境：{bundle['table_setting']}
背景陪衬：{bundle['background_props']}
主画面食材：{bundle['main_food']}
汤汁或酱体：{bundle['sauce']}
质感重点：{bundle['texture']}
动态动作：{bundle['dynamic_action']}
色彩点缀：{bundle['colors']}

【2人份食材】
主料
{main_ingredients}

{"配菜\n" + side_ingredients + "\n" if side_ingredients else ""}

香料
{spices}

调味料
{seasonings}

【成败关键】
{tips}

【{step_section_title}】
{'\n'.join(steps)}

【底部文案】
收藏文案：{bundle['collection_copy']}
关注文案：{bundle['ad_copy']}
""".strip()


def build_local_image_prompt(bundle: dict[str, Any]) -> str:
    return page01_recipe.build_local_page01_prompt(bundle)


def build_local_cover_prompt(bundle: dict[str, Any]) -> str:
    return cover_page.build_local_cover_prompt(bundle)

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


def save_dish_idea(idea_file: Path, dish_name: str, notes: str) -> None:
    idea_file.write_text(f"{dish_name.strip()}\n{notes.strip()}\n", encoding="utf-8")


def is_top_level_section_line(line: str) -> bool:
    return bool(re.fullmatch(r"\[[^\[\]]+\]", line))


def is_second_level_section_line(line: str) -> bool:
    return bool(re.fullmatch(r"\[\[[^\]]+\]\]", line))


def load_traditional_dish_library(library_file: Path) -> dict[str, dict[str, list[str]]]:
    if not library_file.exists():
        raise FileNotFoundError(f"未找到传统菜库文件：{library_file}")

    library: dict[str, dict[str, list[str]]] = {}
    current_top_level = ""
    current_second_level = ""

    for raw_line in library_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if is_top_level_section_line(line):
            current_top_level = line[1:-1].strip()
            current_second_level = ""
            library.setdefault(current_top_level, {})
            continue

        if is_second_level_section_line(line):
            if not current_top_level:
                raise ValueError(f"传统菜库格式异常，二级分类缺少顶层分类：{line}")
            current_second_level = line[2:-2].strip()
            library[current_top_level].setdefault(current_second_level, [])
            continue

        if not current_top_level or not current_second_level:
            raise ValueError(f"传统菜库格式异常，菜名未落在有效分类下：{line}")

        library[current_top_level][current_second_level].append(line)

    if not library:
        raise ValueError(f"传统菜库为空：{library_file}")

    return library


def flatten_library_dishes(
    library: dict[str, dict[str, list[str]]],
    top_sections: tuple[str, ...] | None = None,
) -> list[str]:
    dishes: list[str] = []
    for top_level, category_map in library.items():
        if top_sections and top_level not in top_sections:
            continue
        for items in category_map.values():
            dishes.extend(items)
    return dishes


def build_region_candidate_dishes(
    library: dict[str, dict[str, list[str]]],
    region_code: str,
) -> list[str]:
    profile = AUTO_DISH_REGION_PROFILES[region_code]
    candidate_dishes = flatten_library_dishes(library, top_sections=profile["sections"])
    keywords = tuple(profile["keywords"])
    aliases = set(profile["aliases"])

    if not keywords and not aliases:
        return candidate_dishes

    filtered: list[str] = []
    for dish_name in candidate_dishes:
        if any(keyword in dish_name for keyword in keywords) or dish_name in aliases:
            filtered.append(dish_name)

    if filtered:
        return filtered

    raise RuntimeError(f"当前 AUTO_DISH_CUISINE_MODE={region_code} 没有匹配到可用参考菜。")


def normalize_dish_name_key(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", name).strip().lower()


def find_conflicting_dish_name(candidate_name: str, existing_names: list[str]) -> str:
    candidate_key = normalize_dish_name_key(candidate_name)
    if not candidate_key:
        return ""

    for existing_name in existing_names:
        existing_key = normalize_dish_name_key(existing_name)
        if not existing_key:
            continue
        if candidate_key == existing_key:
            return existing_name

        shorter, longer = (candidate_key, existing_key)
        if len(shorter) > len(longer):
            shorter, longer = longer, shorter
        if len(shorter) >= 3 and shorter in longer and len(longer) - len(shorter) <= 2:
            return existing_name

    return ""


def extract_non_overlapping_auto_dish_name_tokens(text: str, tokens: tuple[str, ...]) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    occupied = [False] * len(text)

    for token in sorted(set(tokens), key=len, reverse=True):
        search_start = 0
        while True:
            match_index = text.find(token, search_start)
            if match_index < 0:
                break

            match_end = match_index + len(token)
            if not any(occupied[match_index:match_end]):
                matches.append((match_index, token))
                for index in range(match_index, match_end):
                    occupied[index] = True

            search_start = match_index + 1

    return sorted(matches, key=lambda item: item[0])


def find_mechanical_auto_dish_name_reason(dish_name: str) -> str:
    if len(dish_name) > AUTO_DISH_NAME_MAX_CHARS:
        return (
            "自动生成的新菜名太长，像把主料、辅料、做法和载体一起塞进了标题。"
            f"请压缩到 {AUTO_DISH_NAME_MIN_CHARS} 到 {AUTO_DISH_NAME_MAX_CHARS} 个字，只保留最有记忆点的 2 到 3 段信息。"
        )

    action_matches = extract_non_overlapping_auto_dish_name_tokens(dish_name, AUTO_DISH_NAME_ACTION_TOKENS)
    if len(action_matches) >= 3:
        return f"自动生成的新菜名工艺和结构词堆得太多，读起来不顺口：{dish_name}。请压缩成更像人会说出口的短名字。"

    if len(dish_name) >= AUTO_DISH_NAME_MAX_CHARS and len(action_matches) >= 2 and action_matches[0][0] >= 4:
        return (
            f"自动生成的新菜名信息塞得过满，读起来像配方摘要，不像人会点单的菜名：{dish_name}。"
            "请按‘风味/状态 + 主料 + 做法或载体’重写，辅料和细节全部放到第二行。"
        )

    return ""


def extract_auto_dish_structure_families(text: str) -> set[str]:
    normalized = normalize_dish_name_key(text)
    matches: set[str] = set()

    for family_name, keywords in AUTO_DISH_STRUCTURE_FAMILIES:
        if any(normalize_dish_name_key(keyword) in normalized for keyword in keywords):
            matches.add(family_name)

    if re.search(r"夹.{0,3}(饼|馍|烧饼|火烧)", text):
        matches.add("夹饼夹馍类")
    if re.search(r"(盖|拌|炒|烩).{0,2}饭", text):
        matches.add("盖饭拌饭类")
    if re.search(r"(汤|拌|炒|焖).{0,2}(面|粉)", text):
        matches.add("面粉主食类")

    return matches


def extract_auto_dish_main_ingredient_families(text: str) -> set[str]:
    return set(extract_auto_dish_family_matches(text, AUTO_DISH_MAIN_INGREDIENT_FAMILIES))


def extract_auto_dish_family_matches(
    text: str,
    family_definitions: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[str]:
    normalized = normalize_dish_name_key(text)
    matches: list[str] = []

    for family_name, keywords in family_definitions:
        if any(normalize_dish_name_key(keyword) in normalized for keyword in keywords):
            matches.append(family_name)

    return matches


def extract_auto_dish_flavor_families(text: str) -> set[str]:
    return set(extract_auto_dish_family_matches(text, AUTO_DISH_FLAVOR_FAMILIES))


def extract_auto_dish_primary_method_families(text: str) -> set[str]:
    return set(extract_auto_dish_family_matches(text, AUTO_DISH_PRIMARY_METHOD_FAMILIES))


def join_auto_dish_history_items(items: Sequence[str]) -> str:
    filtered_items = [str(item).strip() for item in items if str(item).strip()]
    return "、".join(filtered_items) if filtered_items else "无"


def build_recent_auto_dish_restriction_profile(
    historical_generated_dish_names: Sequence[str],
    limit: int = AUTO_DISH_RECENT_HISTORY_LIMIT,
) -> dict[str, tuple[str, ...]]:
    recent_generated_dishes = [str(name).strip() for name in historical_generated_dish_names if str(name).strip()][-limit:]

    def collect_unique_matches(family_definitions: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, ...]:
        collected: list[str] = []
        seen: set[str] = set()
        for dish_name in reversed(recent_generated_dishes):
            for family_name in extract_auto_dish_family_matches(dish_name, family_definitions):
                if family_name in seen:
                    continue
                seen.add(family_name)
                collected.append(family_name)
        return tuple(collected)

    return {
        "recent_generated_dishes": tuple(recent_generated_dishes),
        "banned_ingredients": collect_unique_matches(AUTO_DISH_MAIN_INGREDIENT_FAMILIES),
        "banned_flavors": collect_unique_matches(AUTO_DISH_FLAVOR_FAMILIES),
        "banned_methods": collect_unique_matches(AUTO_DISH_PRIMARY_METHOD_FAMILIES),
    }


def find_recent_auto_dish_history_conflicts(
    candidate_text: str,
    recent_history_restrictions: dict[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    banned_ingredients = set(recent_history_restrictions.get("banned_ingredients", ()))
    banned_flavors = set(recent_history_restrictions.get("banned_flavors", ()))
    banned_methods = set(recent_history_restrictions.get("banned_methods", ()))

    ingredient_conflicts = tuple(
        family_name
        for family_name in extract_auto_dish_family_matches(candidate_text, AUTO_DISH_MAIN_INGREDIENT_FAMILIES)
        if family_name in banned_ingredients
    )
    flavor_conflicts = tuple(
        family_name
        for family_name in extract_auto_dish_family_matches(candidate_text, AUTO_DISH_FLAVOR_FAMILIES)
        if family_name in banned_flavors
    )
    method_conflicts = tuple(
        family_name
        for family_name in extract_auto_dish_family_matches(candidate_text, AUTO_DISH_PRIMARY_METHOD_FAMILIES)
        if family_name in banned_methods
    )

    return {
        "ingredients": ingredient_conflicts,
        "flavors": flavor_conflicts,
        "methods": method_conflicts,
    }


def find_structural_dish_conflict(
    candidate_name: str,
    candidate_text: str,
    existing_names: list[str],
) -> str:
    candidate_structures = extract_auto_dish_structure_families(candidate_text)
    candidate_ingredients = extract_auto_dish_main_ingredient_families(candidate_text)
    if not candidate_structures or not candidate_ingredients:
        return ""

    for existing_name in existing_names:
        existing_structures = extract_auto_dish_structure_families(existing_name)
        if not candidate_structures.intersection(existing_structures):
            continue

        existing_ingredients = extract_auto_dish_main_ingredient_families(existing_name)
        if candidate_ingredients.intersection(existing_ingredients):
            return existing_name

    return ""


def load_auto_dish_memory(memory_file: Path) -> list[dict[str, Any]]:
    if not memory_file.exists():
        return []

    entries: list[dict[str, Any]] = []
    for raw_line in memory_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def append_auto_dish_memory(memory_file: Path, entry: dict[str, Any]) -> None:
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    with memory_file.open("a", encoding="utf-8") as memory_handle:
        memory_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def pick_unused_reference_dish(candidate_dishes: list[str], used_reference_dishes: set[str]) -> str:
    available_dishes = [dish_name for dish_name in candidate_dishes if dish_name not in used_reference_dishes]
    if not available_dishes:
        raise RuntimeError("当前区域内可用的传统参考菜已经全部使用过，请清空记忆文件或切换 AUTO_DISH_CUISINE_MODE。")
    return random.choice(available_dishes)


def parse_auto_generated_dish_idea(raw_text: str) -> dict[str, str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("自动造菜输出行数不足，必须至少有两行。")

    dish_name = re.sub(r"^(?:第一行|菜名|新菜名|原创菜名)[:：]\s*", "", lines[0]).strip()
    description = "".join(lines[1:]).strip()
    description = re.sub(r"^(?:第二行|菜品描述|描述|说明)[:：]\s*", "", description).strip()

    return {
        "dish_idea": dish_name,
        "notes": description,
    }


def validate_auto_generated_dish_idea(
    idea_payload: dict[str, str],
    reference_dish: str,
    traditional_dish_names: list[str],
    historical_generated_dish_names: list[str],
    recent_history_restrictions: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, str]:
    dish_name = idea_payload["dish_idea"].strip()
    notes = re.sub(r"\s+", "", idea_payload["notes"]).strip()

    if not re.fullmatch(rf"[\u4e00-\u9fff]{{{AUTO_DISH_NAME_MIN_CHARS},{AUTO_DISH_NAME_MAX_CHARS}}}", dish_name):
        raise ValueError(
            f"自动生成的新菜名必须是 {AUTO_DISH_NAME_MIN_CHARS} 到 {AUTO_DISH_NAME_MAX_CHARS} 个纯中文汉字。"
        )
    if not notes:
        raise ValueError("自动生成的第二行描述不能为空。")

    return {
        "dish_idea": dish_name,
        "notes": notes,
    }


def generate_auto_dish_idea(
    idea_file: Path,
    client: OpenAI,
) -> dict[str, str]:
    library_file = get_auto_dish_library_file()
    memory_file = get_auto_dish_memory_file()
    library = load_traditional_dish_library(library_file)
    memory_entries = load_auto_dish_memory(memory_file)
    region_code = get_auto_dish_region_code()
    region_profile = AUTO_DISH_REGION_PROFILES[region_code]

    region_candidate_dishes = build_region_candidate_dishes(library, region_code)
    used_reference_dishes = [str(entry.get("reference_dish", "")).strip() for entry in memory_entries if str(entry.get("reference_dish", "")).strip()]
    used_generated_dishes = [str(entry.get("generated_dish_name", "")).strip() for entry in memory_entries if str(entry.get("generated_dish_name", "")).strip()]
    recent_history_restrictions = build_recent_auto_dish_restriction_profile(used_generated_dishes)
    reference_dish = pick_unused_reference_dish(region_candidate_dishes, set(used_reference_dishes))

    sample_pool = [dish_name for dish_name in region_candidate_dishes if dish_name != reference_dish]
    sample_size = min(18, len(sample_pool))
    region_samples = random.sample(sample_pool, sample_size) if sample_size else []
    traditional_dish_names = flatten_library_dishes(library)

    last_error = ""
    last_model = ""
    for _ in range(AUTO_DISH_GENERATION_RETRY_COUNT):
        generation_result = request_text_generation(
            client=client,
            system_prompt=build_auto_dish_generation_system_prompt(region_label=region_profile["label"]),
            user_prompt=build_auto_dish_generation_user_prompt(
                region_label=region_profile["label"],
                reference_dish=reference_dish,
                region_samples=region_samples,
                used_reference_dishes=used_reference_dishes,
                used_generated_dishes=used_generated_dishes,
                recent_history_restrictions=recent_history_restrictions,
                retry_feedback=last_error,
            ),
            stage_name="自动造菜",
        )
        last_model = generation_result["model"]
        parsed_payload = parse_auto_generated_dish_idea(generation_result["content"])
        try:
            validated_payload = validate_auto_generated_dish_idea(
                idea_payload=parsed_payload,
                reference_dish=reference_dish,
                traditional_dish_names=traditional_dish_names,
                historical_generated_dish_names=used_generated_dishes,
                recent_history_restrictions=recent_history_restrictions,
            )
        except ValueError as exc:
            last_error = str(exc)
            continue

        save_dish_idea(idea_file, validated_payload["dish_idea"], validated_payload["notes"])
        append_auto_dish_memory(
            memory_file,
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "region_code": region_code,
                "region_label": region_profile["label"],
                "reference_dish": reference_dish,
                "generated_dish_name": validated_payload["dish_idea"],
                "model": last_model,
            },
        )

        return {
            **validated_payload,
            "auto_generated": "1",
            "region_code": region_code,
            "region_label": region_profile["label"],
            "reference_dish": reference_dish,
            "memory_file": str(memory_file),
            "library_file": str(library_file),
            "generation_model": last_model,
        }

    raise RuntimeError(f"自动造菜连续失败：{last_error or '模型多次输出不合规。'}")


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


def build_run_output_dir(timestamp: str, dish_name: str) -> Path:
    return OUTPUT_ROOT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}"


def backup_dish_idea_file(source_file: Path, output_dir: Path, timestamp: str, dish_name: str) -> Path | None:
    """
    备份菜谱创意灵感文件到输出目录。
    
    Args:
        source_file: 源菜谱灵感文件路径
        output_dir: 输出目录路径
        timestamp: 时间戳
        dish_name: 菜品名称
    
    Returns:
        备份文件路径，如果备份失败则返回 None
    """
    if not source_file.exists():
        return None
    
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_file_name = f"{timestamp}_{sanitize_file_name(dish_name)}_菜谱创意灵感.txt"
    backup_file_path = output_dir / backup_file_name
    
    try:
        shutil.copy2(source_file, backup_file_path)
        return backup_file_path
    except Exception as exc:
        print(f"[警告] 菜谱灵感备份失败：{exc}")
        return None


def get_text_model() -> str:
    ensure_runtime_config_loaded()
    provider = get_text_provider()
    if provider == "doubao":
        return os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    return os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


def get_text_fallback_model() -> str:
    ensure_runtime_config_loaded()
    provider = get_text_provider()
    if provider == "doubao":
        default_model = get_text_model()
        return os.getenv("DOUBAO_TEXT_FALLBACK_MODEL", default_model).strip() or default_model
    return os.getenv("OPENAI_TEXT_FALLBACK_MODEL", "gpt-4.1-nano").strip() or "gpt-4.1-nano"


def get_text_temperature() -> float:
    ensure_runtime_config_loaded()
    provider = get_text_provider()
    if provider == "doubao":
        text = os.getenv("DOUBAO_TEXT_TEMPERATURE", "0.2").strip() or "0.2"
    else:
        text = os.getenv("OPENAI_TEXT_TEMPERATURE", "0.2").strip() or "0.2"

    try:
        return float(text)
    except ValueError as exc:
        raise RuntimeError("文本温度参数必须是数字。") from exc


def get_text_max_output_tokens(stage_name: str) -> int:
    if stage_name == "自动造菜":
        return 1200
    if stage_name == "创意菜谱":
        return 1400
    if stage_name == "封面prompt":
        return 1600
    if stage_name == "抖音发布文案":
        return 1200
    return 2600


def get_text_request_timeout_seconds() -> float:
    ensure_runtime_config_loaded()
    timeout_text = os.getenv("OPENAI_TEXT_REQUEST_TIMEOUT_SECONDS", "120").strip() or "120"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_TEXT_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def get_text_request_retry_count() -> int:
    ensure_runtime_config_loaded()
    retry_text = os.getenv("TEXT_REQUEST_RETRY_COUNT", str(DEFAULT_TEXT_REQUEST_RETRY_COUNT)).strip() or str(DEFAULT_TEXT_REQUEST_RETRY_COUNT)
    try:
        retry_count = int(retry_text)
    except ValueError as exc:
        raise RuntimeError("TEXT_REQUEST_RETRY_COUNT 必须是整数。") from exc

    if retry_count < 1:
        raise RuntimeError("TEXT_REQUEST_RETRY_COUNT 必须大于等于 1。")
    return retry_count


def get_image_request_timeout_seconds() -> float:
    ensure_runtime_config_loaded()
    timeout_text = os.getenv("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", "900").strip() or "900"
    try:
        return float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS 必须是数字。") from exc


def validate_gpt_image_size(size_text: str, env_name: str) -> str:
    normalized = size_text.strip().lower()
    match = re.fullmatch(r"(\d+)x(\d+)", normalized)
    if match is None:
        raise RuntimeError(f"{env_name} 必须是 宽x高 格式，例如 1024x1536。")

    width = int(match.group(1))
    height = int(match.group(2))
    long_edge = max(width, height)
    short_edge = min(width, height)
    total_pixels = width * height

    if width % 16 != 0 or height % 16 != 0:
        raise RuntimeError(f"{env_name}={normalized} 非法：宽和高都必须是 16 的倍数。")
    if long_edge > 3840:
        raise RuntimeError(f"{env_name}={normalized} 非法：最长边不能超过 3840 像素。")
    if short_edge == 0 or long_edge / short_edge > 3:
        raise RuntimeError(f"{env_name}={normalized} 非法：长边与短边比例不能超过 3:1。")
    if total_pixels < 655360 or total_pixels > 8294400:
        raise RuntimeError(f"{env_name}={normalized} 非法：总像素必须介于 655360 和 8294400 之间。")

    return f"{width}x{height}"


def get_image_settings() -> dict[str, Any]:
    ensure_runtime_config_loaded()
    return {
        "model": os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_OPENAI_IMAGE_MODEL).strip() or DEFAULT_OPENAI_IMAGE_MODEL,
        "size": validate_gpt_image_size(
            os.getenv("OPENAI_IMAGE_SIZE", "1024x1536").strip() or "1024x1536",
            env_name="OPENAI_IMAGE_SIZE",
        ),
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high",
        "image_count": int(os.getenv("OPENAI_IMAGE_COUNT", "2").strip() or "2"),
    }


def get_tujie_image_settings() -> dict[str, Any]:
    ensure_runtime_config_loaded()
    default_model = os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_OPENAI_IMAGE_MODEL).strip() or DEFAULT_OPENAI_IMAGE_MODEL
    default_size = validate_gpt_image_size(
        os.getenv("OPENAI_IMAGE_SIZE", "1024x1536").strip() or "1024x1536",
        env_name="OPENAI_IMAGE_SIZE",
    )
    default_quality = os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high"
    return {
        "model": os.getenv("OPENAI_TUJIE_IMAGE_MODEL", default_model).strip() or default_model,
        "size": validate_gpt_image_size(
            os.getenv("OPENAI_TUJIE_IMAGE_SIZE", default_size).strip() or default_size,
            env_name="OPENAI_TUJIE_IMAGE_SIZE",
        ),
        "quality": os.getenv("OPENAI_TUJIE_IMAGE_QUALITY", default_quality).strip() or default_quality,
        "image_count": int(os.getenv("OPENAI_TUJIE_IMAGE_COUNT", "1").strip() or "1"),
    }


def get_cover_image_settings() -> dict[str, Any]:
    ensure_runtime_config_loaded()
    default_model = os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_OPENAI_IMAGE_MODEL).strip() or DEFAULT_OPENAI_IMAGE_MODEL
    default_quality = os.getenv("OPENAI_IMAGE_QUALITY", "high").strip() or "high"
    return {
        "model": os.getenv("OPENAI_COVER_IMAGE_MODEL", default_model).strip() or default_model,
        "size": validate_gpt_image_size(
            os.getenv("OPENAI_COVER_IMAGE_SIZE", "1024x1536").strip() or "1024x1536",
            env_name="OPENAI_COVER_IMAGE_SIZE",
        ),
        "quality": os.getenv("OPENAI_COVER_IMAGE_QUALITY", default_quality).strip() or default_quality,
        "image_count": int(os.getenv("OPENAI_COVER_IMAGE_COUNT", "1").strip() or "1"),
    }


def build_client() -> OpenAI:
    return build_image_client()


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


def extract_chat_text_output(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    text_parts: list[str] = []
    for item in content or []:
        text = getattr(item, "text", None) or item.get("text") or ""
        if text:
            text_parts.append(text)

    return "\n".join(text_parts).strip()


def encode_image_file_as_data_url(image_path: Path) -> str:
    mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{image_b64}"


def request_text_generation(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    stage_name: str,
) -> dict[str, str]:
    text_model = get_text_model()
    request_timeout = get_text_request_timeout_seconds()
    request_retry_count = get_text_request_retry_count()
    max_output_tokens = get_text_max_output_tokens(stage_name)
    temperature = get_text_temperature()

    response = None
    print(f"正在生成{stage_name}，调用文本模型：{text_model}")
    for attempt in range(1, request_retry_count + 1):
        request_client = client if attempt == 1 else build_text_client()
        try:
            response = request_client.chat.completions.create(
                model=text_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=request_timeout,
                max_tokens=max_output_tokens,
                temperature=temperature,
            )
            break
        except Exception as exc:
            if not is_retriable_text_request_error(exc):
                raise
            error_label = get_text_request_error_label(exc)
            if attempt >= request_retry_count:
                raise RuntimeError(f"{stage_name}文本接口连续 {request_retry_count} 次{error_label}，已终止本轮流程。") from exc
            print(f"{stage_name}文本接口{error_label}，正在重试第 {attempt + 1}/{request_retry_count} 次...")
        finally:
            if request_client is not client:
                close_openai_client(request_client)

    content = extract_chat_text_output(response)
    if not content:
        raise ValueError(f"{stage_name}阶段未获得有效文本输出。")

    return {
        "model": text_model,
        "content": content,
    }


def request_multimodal_text_generation(
    client: OpenAI,
    system_prompt: str,
    user_content: list[dict[str, Any]],
    stage_name: str,
    model: str | None = None,
) -> dict[str, str]:
    text_model = model or get_multimodal_review_model()
    request_timeout = get_text_request_timeout_seconds()
    request_retry_count = get_text_request_retry_count()
    max_output_tokens = get_text_max_output_tokens(stage_name)
    temperature = get_text_temperature()

    response = None
    print(f"正在生成{stage_name}，调用多模态模型：{text_model}")
    for attempt in range(1, request_retry_count + 1):
        request_client = client if attempt == 1 else build_multimodal_review_client()
        try:
            response = request_client.chat.completions.create(
                model=text_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                timeout=request_timeout,
                max_tokens=max_output_tokens,
                temperature=temperature,
            )
            break
        except Exception as exc:
            if not is_retriable_text_request_error(exc):
                raise
            error_label = get_text_request_error_label(exc)
            if attempt >= request_retry_count:
                raise RuntimeError(f"{stage_name}文本接口连续 {request_retry_count} 次{error_label}，已终止本轮流程。") from exc
            print(f"{stage_name}文本接口{error_label}，正在重试第 {attempt + 1}/{request_retry_count} 次...")
        finally:
            if request_client is not client:
                close_openai_client(request_client)

    content = extract_chat_text_output(response)
    if not content:
        raise ValueError(f"{stage_name}阶段未获得有效文本输出。")

    return {
        "model": text_model,
        "content": content,
    }


def run_text_stage_with_validation_retry(stage_name: str, operation: Callable[[], Any]) -> Any:
    request_retry_count = get_text_request_retry_count()
    last_error: ValueError | None = None

    for attempt in range(1, request_retry_count + 1):
        try:
            return operation()
        except ValueError as exc:
            last_error = exc
            if attempt >= request_retry_count:
                break
            print(f"{stage_name}内容异常（{exc}），正在重新调用第 {attempt + 1}/{request_retry_count} 次...")

    detail = f"最后一次原因：{last_error}" if last_error else "未返回可定位原因"
    raise RuntimeError(f"{stage_name}连续 {request_retry_count} 次内容异常，已终止本轮流程。{detail}") from last_error


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
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError(f"待保存文本为空：{suffix}")

    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{timestamp}_{sanitize_file_name(base_name)}{suffix}.txt"
    file_path.write_text(normalized_content + "\n", encoding="utf-8")
    return str(file_path)


def replace_or_insert_prefixed_line(
    text: str,
    prefix: str,
    replacement_line: str,
    insert_after_prefix: str | None = None,
) -> str:
    lines = text.splitlines()

    for index, raw_line in enumerate(lines):
        if raw_line.strip().startswith(prefix):
            lines[index] = replacement_line
            return "\n".join(lines)

    if insert_after_prefix:
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(insert_after_prefix):
                lines.insert(index + 1, replacement_line)
                return "\n".join(lines)

    lines.append(replacement_line)
    return "\n".join(lines)


def remove_prefixed_line(text: str, prefix: str) -> str:
    lines = [line for line in text.splitlines() if not line.strip().startswith(prefix)]
    return "\n".join(lines)


def normalize_recipe_text(recipe_text: str, fixed_dish_name: str, ad_copy: str, notes: str = "") -> str:
    normalized_text = recipe_text.strip()
    normalized_notes = " ".join(notes.split())
    main_food_description = extract_recipe_field_value(normalized_text, "主画面食材") or infer_main_food_description(
        fixed_dish_name,
        normalized_notes,
    )
    inferred_plate_description = infer_plate_description(fixed_dish_name, normalized_notes)
    existing_plate_description = extract_recipe_field_value(normalized_text, "器皿与摆盘")
    plate_description = existing_plate_description
    if should_infer_scene_field(plate_description):
        plate_description = inferred_plate_description
    plate_description = normalize_plate_description(
        plate_description,
        fixed_dish_name,
        normalized_notes,
        main_food_description,
    )

    inferred_table_description = infer_table_description(fixed_dish_name, normalized_notes, plate_description)
    existing_table_description = extract_recipe_field_value(normalized_text, "桌面与环境")
    table_description = existing_table_description
    if should_infer_scene_field(table_description) or looks_like_template_scene_field("桌面与环境", table_description):
        table_description = inferred_table_description

    inferred_background_prop_description = infer_background_prop_description(fixed_dish_name, normalized_notes)
    existing_background_prop_description = extract_recipe_field_value(normalized_text, "背景陪衬")
    background_prop_description = existing_background_prop_description
    if should_infer_scene_field(background_prop_description) or looks_like_template_scene_field("背景陪衬", background_prop_description):
        background_prop_description = inferred_background_prop_description

    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="最终菜名：",
        replacement_line=f"最终菜名：{fixed_dish_name}",
        insert_after_prefix="创意来源：",
    )
    normalized_text = remove_prefixed_line(normalized_text, prefix="收藏提示：")
    existing_dynamic_action = extract_recipe_field_value(normalized_text, "动态动作")
    dynamic_action_description = normalize_dynamic_action_description(
        existing_dynamic_action,
        fixed_dish_name,
        normalized_notes,
        plate_description,
        main_food_description,
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="动态动作：",
        replacement_line=f"动态动作：{dynamic_action_description}",
        insert_after_prefix="质感重点：",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="器皿与摆盘：",
        replacement_line=f"器皿与摆盘：{plate_description}",
        insert_after_prefix="【主画面说明】",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="桌面与环境：",
        replacement_line=f"桌面与环境：{table_description}",
        insert_after_prefix="器皿与摆盘：",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="背景陪衬：",
        replacement_line=f"背景陪衬：{background_prop_description}",
        insert_after_prefix="桌面与环境：",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="收藏文案：",
        replacement_line=f"收藏文案：{DEFAULT_COLLECTION_COPY}",
        insert_after_prefix="【底部文案】",
    )
    normalized_text = replace_or_insert_prefixed_line(
        text=normalized_text,
        prefix="关注文案：",
        replacement_line=f"关注文案：{ad_copy}",
        insert_after_prefix="收藏文案：",
    )
    return normalized_text.strip()


def looks_like_placeholder_output(text: str) -> bool:
    normalized = " ".join(text.split())
    if not normalized:
        return True
    if re.fullmatch(r"[?？_\-\s]+", normalized):
        return True
    if "??" in normalized or "？？" in normalized:
        return True
    if len(normalized) <= 40 and re.search(r"(创意菜谱|图解\d{2}(文案|prompt)|封面prompt|文生图prompt).*(输出|output)$", normalized, flags=re.I):
        return True
    return False


def ensure_non_placeholder_text(text: str, stage_name: str, min_length: int = 10) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError(f"{stage_name} 结果为空。")
    if len(normalized) < min_length:
        raise ValueError(f"{stage_name} 结果过短，疑似异常。")
    if looks_like_placeholder_output(normalized):
        raise ValueError(f"{stage_name} 返回了占位文本，疑似异常。")
    return normalized


def validate_recipe_text_content(recipe_text: str, fixed_dish_name: str) -> str:
    normalized = ensure_non_placeholder_text(recipe_text, stage_name="创意菜谱", min_length=80)
    normalized = normalize_generated_guide_line(normalized, fixed_dish_name=fixed_dish_name)
    normalized = normalize_generated_subtitle(normalized, fixed_dish_name=fixed_dish_name)
    step_section_title = extract_recipe_step_section_title(normalized)
    required_sections = [
        "【基础定位】",
        f"最终菜名：{fixed_dish_name}",
        "【主画面说明】",
        "器皿与摆盘：",
        "桌面与环境：",
        "背景陪衬：",
        "【2人份食材】",
        "【成败关键】",
        "【底部文案】",
    ]
    missing_sections = [section for section in required_sections if section not in normalized]
    if missing_sections:
        raise ValueError(f"创意菜谱缺少必要结构：{','.join(missing_sections)}")
    if not step_section_title:
        raise ValueError("创意菜谱缺少步骤区块，必须使用【3步出锅】、【4步出锅】或【5步出锅】其中之一。")

    steps = extract_recipe_steps(normalized)
    step_count = len(steps)
    expected_step_count = int(step_section_title[0])
    if step_count < 3 or step_count > 5:
        raise ValueError("创意菜谱步骤数必须在 3 到 5 步之间。")
    if step_count != expected_step_count:
        raise ValueError(f"创意菜谱步骤标题与实际步数不一致：标题写了 {expected_step_count} 步，实际解析到 {step_count} 步。")
    return normalized


def validate_image_prompt_content(prompt_text: str, fixed_dish_name: str, stage_name: str) -> str:
    normalized = ensure_non_placeholder_text(prompt_text, stage_name=stage_name, min_length=60)
    normalized_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", normalized)
    dish_name_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", fixed_dish_name)
    if dish_name_key not in normalized_key:
        raise ValueError(f"{stage_name} 缺少菜名，疑似异常。")
    return normalized


def build_page01_required_prompt_fragments(bundle: dict[str, Any]) -> list[str]:
    fragments: list[str] = [
        bundle.get("guide_line", ""),
        bundle.get("subtitle", ""),
        bundle.get("collection_copy", ""),
        bundle.get("ad_copy", ""),
        bundle.get("plate", ""),
        bundle.get("dynamic_action", ""),
    ]

    for group_name in ("main_ingredients", "side_ingredients", "spices", "seasonings"):
        for name, amount in bundle.get(group_name, []):
            fragments.append(f"{name} {amount}".strip())

    fragments.extend(bundle.get("tips", []))

    for step in bundle.get("steps", []):
        title = str(step.get("title", "")).strip()
        content = str(step.get("content", "")).strip()
        if title:
            fragments.append(title)
        if content:
            fragments.append(content)

    unique_fragments: list[str] = []
    seen_fragments: set[str] = set()
    for fragment in fragments:
        if not fragment or fragment in seen_fragments:
            continue
        seen_fragments.add(fragment)
        unique_fragments.append(fragment)

    return unique_fragments


def normalize_page01_prompt_phrasing(prompt_text: str) -> str:
    normalized = prompt_text.strip()
    normalized = re.sub(r"一只\s*竹?筷子?", "一双筷子", normalized)
    normalized = re.sub(r"另一只\s*竹?筷子?", "另一双筷子", normalized)
    normalized = re.sub(r"一把\s*竹?筷子", "一双筷子", normalized)
    normalized = re.sub(
        r"有人手持竹?筷[，,、]\s*一只([^，。；;]*?)[，,、]\s*另一只([^，。；;]*?)",
        r"有人手持餐具与主菜互动，\1，同时\2",
        normalized,
    )
    normalized = re.sub(
        r"一双竹?筷子?夹起([^，。；;]*?)[，,、]\s*另一只([^，。；;]*?)",
        r"有人手持餐具夹起\1，并\2",
        normalized,
    )
    normalized = re.sub(
        r"一双竹?筷子?([^，。；;]*?)[，,、]\s*另一双竹?筷子?([^，。；;]*?)",
        r"有人手持餐具与主菜互动，\1，并\2",
        normalized,
    )
    normalized = re.sub(r"(?<=[\u4e00-\u9fff”’」》）】])[:：]", " ", normalized)
    return normalized


def collect_page01_prompt_risks(prompt_text: str) -> list[str]:
    normalized = prompt_text.strip()
    risks: list[str] = []
    leakage_patterns = (
        r"硬性补充约束",
        r"文生图\s*prompt",
        r"程序化字段标签",
        r"页面名称",
        r"内容卡\d",
    )
    for pattern in leakage_patterns:
        if re.search(pattern, normalized, flags=re.I):
            risks.append("提示词泄露了流程术语或程序化字段。")
            break

    return risks


def validate_page01_prompt_content(prompt_text: str, fixed_dish_name: str, bundle: dict[str, Any]) -> str:
    normalized = validate_image_prompt_content(prompt_text, fixed_dish_name=fixed_dish_name, stage_name="文生图prompt")
    normalized = normalize_page01_prompt_phrasing(normalized)
    if not re.search(r"(中轴线|正中竖线|画面正中竖线|居中堆叠|居中排布)", normalized):
        raise ValueError("文生图prompt 缺少首图标题居中结构约束。")

    risks = collect_page01_prompt_risks(normalized)
    if risks:
        raise ValueError(f"文生图prompt 视觉审校未通过：{risks[0]}")

    required_groups: list[tuple[str, list[tuple[str, str]]]] = [
        ("主料", bundle.get("main_ingredients", [])),
        ("香料", bundle.get("spices", [])),
        ("调味料", bundle.get("seasonings", [])),
    ]
    side_ingredients = bundle.get("side_ingredients", [])
    if side_ingredients:
        required_groups.append(("配菜", side_ingredients))

    compact_normalized = re.sub(r"\s+", "", normalized)
    for group_name, items in required_groups:
        if not items:
            continue
        matched_count = 0
        for name, amount in items:
            fragment = re.sub(r"\s+", "", f"{name}{amount}")
            if fragment and fragment in compact_normalized:
                matched_count += 1
        min_required = max(1, min(2, len(items)))
        if matched_count < min_required:
            raise ValueError(f"文生图prompt 缺少{group_name}关键信息，请补充后重试。")

    return normalized


def validate_guide_page_prompt_content(prompt_text: str, fixed_dish_name: str, stage_name: str) -> str:
    normalized = ensure_non_placeholder_text(prompt_text, stage_name=stage_name, min_length=60)
    normalized_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", normalized)
    dish_name_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", fixed_dish_name)
    additions: list[str] = []

    if dish_name_key and dish_name_key not in normalized_key:
        additions.append(
            f"这张延续页围绕同一道菜“{fixed_dish_name}”展开；如果页面里需要出现菜名，只能直接写“{fixed_dish_name}”，不要改写成别名、简称或字段标签。"
        )

    if not re.search(r"(不要|禁止|绝对不要).*(当前菜名|当前页面|页面名称|页面标题|页面副标题|内容卡1|内容卡2|内容卡3|页尾提示)", normalized):
        additions.append(
            "画面里绝对不要出现“当前菜名”“当前页面”“页面名称”“页面标题”“页面副标题”“内容卡1”“内容卡2”“内容卡3”“页尾提示”这类程序化字段标签。"
        )

    if additions:
        normalized += "\n\n硬性补充约束：\n" + "\n".join(additions)

    return normalized


def append_cover_hard_requirements(prompt_text: str, fixed_dish_name: str, bundle: dict[str, Any]) -> str:
    normalized = prompt_text.strip()
    additions: list[str] = []
    vertical_dish_name = cover_page.format_vertical_dish_name(fixed_dish_name)

    if not re.search(r"竖版\s*2:3", normalized):
        additions.append("整张图必须是竖版2:3封面图。")
    if not re.search(r"(画布正中|中轴|正中竖轴|正中竖线).*(标题通道|单列竖排|竖向标题通道)", normalized):
        additions.append(cover_page.build_cover_centered_vertical_title_requirement(fixed_dish_name, vertical_dish_name))
    if not re.search(r"(背景主菜|餐盘|陪衬|背景).*(避开|退到).*(中轴|标题通道)", normalized):
        additions.append("背景主菜、餐盘和陪衬必须主动避开中轴标题通道，不能把竖排菜名挤到侧边。")
    if not re.search(r"(封面背景|背景).*(沿用|来自|参考).*(首图|参考图).*(同场景|同一道菜)", normalized):
        additions.append("封面背景沿用首图同一道菜的同场景，不要另起模板。")
    if not re.search(r"(除菜名外|其它区域\s*0\s*文字|0\s*文字)", normalized):
        additions.append("除菜名外画面其它区域 0 文字。")

    if not additions:
        return normalized

    return normalized + "\n\n硬性补充约束：\n" + "\n".join(additions)


def validate_cover_prompt_content(prompt_text: str, fixed_dish_name: str, bundle: dict[str, Any]) -> str:
    normalized = validate_image_prompt_content(prompt_text, fixed_dish_name=fixed_dish_name, stage_name="封面prompt")
    normalized = append_cover_hard_requirements(normalized, fixed_dish_name=fixed_dish_name, bundle=bundle)
    if not re.search(r"竖版\s*2:3", normalized):
        raise ValueError("封面prompt 缺少 2:3 画幅约束。")
    if not re.search(r"(画布正中|中轴|正中竖轴|正中竖线).*(标题通道|单列竖排|竖向标题通道)", normalized):
        raise ValueError("封面prompt 缺少菜名单列竖排中轴约束。")
    if not re.search(r"(背景主菜|餐盘|陪衬|背景).*(避开|退到).*(中轴|标题通道)", normalized):
        raise ValueError("封面prompt 缺少背景避让中轴约束。")
    if not re.search(r"(封面背景|背景).*(沿用|来自|参考).*(首图|参考图).*(同场景|同一道菜)", normalized):
        raise ValueError("封面prompt 缺少首图同场景约束。")
    if not re.search(r"(除菜名外|其它区域\s*0\s*文字|0\s*文字)", normalized):
        raise ValueError("封面prompt 缺少除菜名外 0 文字约束。")

    return normalized


def validate_guide_page_text_content(page_text: str, page_number: int, page_name: str) -> str:
    normalized = ensure_non_placeholder_text(page_text, stage_name=f"图解{page_number:02d}文案", min_length=40)
    required_sections = [
        "【页面信息】",
        f"页码：{page_number:02d}/06",
        f"页面名称：{page_name}",
        "页面标题：",
        "页面副标题：",
        "阅读收益：",
        "【页尾提示】",
    ]
    missing_sections = [section for section in required_sections if section not in normalized]
    if missing_sections:
        raise ValueError(f"图解{page_number:02d}文案缺少必要结构：{','.join(missing_sections)}")
    return normalized


def normalize_topic_text(topic: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", topic).strip()
    return cleaned[:18]


def format_topic_tag(topic: str) -> str:
    normalized = normalize_topic_text(topic).lstrip("#")
    if not normalized:
        return ""
    return f"#{normalized}"


def parse_publish_topic_candidates(raw_topics: str) -> list[str]:
    if not raw_topics.strip():
        return []

    hashtag_tokens = re.findall(r"#?[0-9A-Za-z\u4e00-\u9fff]+", raw_topics)
    raw_tokens = hashtag_tokens or re.split(r"[\s,，;；|]+", raw_topics)

    topics: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        tag = format_topic_tag(token)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        topics.append(tag)
    return topics


def get_required_publish_topics() -> list[str]:
    return get_required_publish_topics_for_platform("douyin")


def get_required_publish_topics_for_platform(platform_key: str) -> list[str]:
    ensure_runtime_config_loaded()
    env_name = PUBLISH_PLATFORM_REQUIRED_TOPIC_ENV.get(platform_key, "PUBLISH_REQUIRED_TOPICS")
    raw_topics = os.getenv(env_name, "").strip()
    return parse_publish_topic_candidates(raw_topics)


def is_disallowed_publish_topic(topic: str, dish_name: str) -> bool:
    normalized_topic = normalize_topic_text(topic)
    normalized_dish_name = normalize_topic_text(dish_name)
    if not normalized_topic:
        return True
    if any(token in normalized_topic for token in PUBLISH_BANNED_TOPIC_TOKENS):
        return True
    if normalized_dish_name and normalized_dish_name in normalized_topic:
        return True
    return False


def sanitize_publish_title_highlight(highlight: str) -> str:
    cleaned = highlight.replace("阿叶造新菜", " ")
    cleaned = re.sub(r"[#【】\[\]（）()《》<>]", " ", cleaned)
    cleaned = cleaned.replace("，", " ").replace(",", " ")
    cleaned = cleaned.replace("！", " ").replace("!", " ")
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\s]+", " ", cleaned)
    cleaned = " ".join(cleaned.split()).replace(" ", "")
    return cleaned[:12].strip()


def infer_publish_title_highlight(dish_name: str, source_text: str, notes: str = "") -> str:
    combined_text = f"{dish_name} {notes} {source_text}"

    if contains_any(combined_text, ["脆", "酥", "焦", "煎"]):
        return "外脆里嫩超香"
    if contains_any(combined_text, ["鱼", "虾", "贝", "帆立", "扇贝", "鲜", "嫩", "滑"]):
        return "鲜嫩到一口上头"
    if contains_any(combined_text, ["酸", "柚", "青柠", "梅子"]):
        return "酸香开胃真上头"
    if contains_any(combined_text, ["煲", "锅", "汤"]):
        return "热乎鲜香太顶了"
    if contains_any(combined_text, ["豆", "扁豆", "豆腐", "素"]):
        return "外脆内软超香"
    return "香到想立刻开饭"


def normalize_publish_title(
    dish_name: str,
    source_text: str,
    notes: str,
    title: str,
) -> str:
    raw_title = " ".join(title.splitlines()).strip()
    if not raw_title:
        raise ValueError("图文标题为空。")

    raw_title = raw_title.replace("!", "！").replace(",", "，")
    raw_title = raw_title.replace("阿叶造新菜", " ")

    highlight_source = raw_title
    if dish_name in highlight_source:
        highlight_source = highlight_source.split(dish_name, 1)[1]
    if "，" in highlight_source:
        highlight_source = highlight_source.split("，", 1)[-1]
    highlight_source = highlight_source.strip(" ，。！？!?.、：:；; ")

    highlight = sanitize_publish_title_highlight(highlight_source)
    if not highlight:
        raise ValueError("图文标题卖点为空或无效。")

    return f"{dish_name}，{highlight}！"


def build_publish_activity_topics(source_text: str) -> list[str]:
    topics: list[str] = []
    if contains_any(source_text, ["原创", "创意", "新菜", "灵感"]):
        topics.append("抖音美食推荐官")
    else:
        topics.append("跟着抖音学做菜")
    topics.append("我的厨房日记")
    return topics


def infer_publish_topic_tags(dish_name: str, source_text: str, notes: str = "") -> list[str]:
    combined_text = f"{dish_name} {notes} {source_text}"
    required_topics = get_required_publish_topics()
    required_topic_set = set(required_topics)
    candidate_topics: list[str] = [*required_topics, *build_publish_activity_topics(combined_text)]

    if contains_any(combined_text, ["煲", "锅", "汤"]):
        candidate_topics.extend(["家常煲菜", "一锅出晚饭"])
    if contains_any(combined_text, ["煎", "焦", "铁板"]):
        candidate_topics.extend(["平底锅菜谱", "香煎做法"])
    if contains_any(combined_text, ["炸", "脆", "酥"]):
        candidate_topics.extend(["外脆里嫩", "香酥做法"])
    if contains_any(combined_text, ["鱼", "虾", "贝", "海鲜", "鱿", "蛤"]):
        candidate_topics.extend(["海鲜做法", "鲜味料理"])
    if contains_any(combined_text, ["豆", "扁豆", "豆腐", "素"]):
        candidate_topics.extend(["素食菜谱", "豆类料理"])
    if contains_any(combined_text, ["孜然", "中东", "北非", "摩洛哥", "黎巴嫩", "异国"]):
        candidate_topics.extend(["异国风味菜", "中东风味料理"])
    if contains_any(combined_text, ["请客", "宴客", "待客", "聚餐"]):
        candidate_topics.extend(["宴客菜", "聚餐菜"])
    if contains_any(combined_text, ["下饭", "拌饭", "配饭"]):
        candidate_topics.extend(["下饭菜", "晚饭吃什么"])
    if contains_any(combined_text, ["懒人", "简单", "快手", "省时"]):
        candidate_topics.extend(["快手家常菜", "懒人菜谱"])

    candidate_topics.extend(PUBLISH_GENERAL_TOPICS)

    tags: list[str] = []
    seen: set[str] = set()
    for topic in candidate_topics:
        tag = format_topic_tag(topic)
        if not tag:
            continue
        if tag not in required_topic_set and is_disallowed_publish_topic(topic, dish_name=dish_name):
            continue
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= 5:
            break

    return tags


def build_platform_topic_prompt_system() -> str:
    return """
你是多平台美食内容运营助手。你要基于给定菜谱内容，为不同平台输出话题标签。

硬要求：
1. 必须先按“平台最新热门活动/热门话题”方向思考，再补“潜力增长话题”。
2. 话题标签必须以 # 开头。
3. 不能输出 #阿叶造新菜，不能输出菜品本身名称相关话题。
4. 输出必须严格使用下面四个分区，不要加解释：

【抖音】
...

【小红书】
...

【微信视频号和公众号】
...

【快手】
...

5. 各分区只输出空格分隔的话题标签，不要编号，不要句子。
""".strip()


def build_platform_topic_prompt_user(
    dish_name: str,
    topic_reference_text: str,
) -> str:
    required_topic_lines: list[str] = []
    for platform_key, platform_label, _count in PUBLISH_PLATFORM_TOPIC_SPECS:
        required_topics = get_required_publish_topics_for_platform(platform_key)
        required_text = " ".join(required_topics) if required_topics else "无"
        required_topic_lines.append(f"- {platform_label}：{required_text}")

    required_topic_block = "\n".join(required_topic_lines)

    return f"""
当前菜名：{dish_name}

关联新菜品文档（只允许使用这份文档的信息生成话题，不要引用其它来源文档）：
{topic_reference_text}

请按下面数量输出四个平台的话题（热门优先，再补潜力）：
1. 抖音：5 个
2. 小红书：10 个
3. 微信视频号和公众号：30 个
4. 快手：4 个

各平台指定必带话题（如为“无”则不强制）：
{required_topic_block}
""".strip()


def extract_platform_topic_section(text: str, section_name: str) -> list[str]:
    section_pattern = rf"【{re.escape(section_name)}】\s*(.*?)(?=\n【|\Z)"
    match = re.search(section_pattern, text, flags=re.S)
    if not match:
        return []

    raw_section = match.group(1).strip()
    if not raw_section:
        return []

    tokens = re.findall(r"#?[^\s#]+", raw_section)
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        tag = format_topic_tag(token)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def build_platform_topic_fallback_candidates(
    *,
    platform_key: str,
    dish_name: str,
    source_text: str,
    notes: str,
    description_body: str,
) -> list[str]:
    merged_text = f"{dish_name} {notes} {source_text} {description_body}"
    candidates: list[str] = [*get_required_publish_topics_for_platform(platform_key)]

    if platform_key == "douyin":
        candidates.extend(build_publish_activity_topics(merged_text))
        candidates.extend(infer_publish_topic_tags(dish_name=dish_name, source_text=source_text, notes=notes))

    if contains_any(merged_text, ["煲", "锅", "汤"]):
        candidates.extend(["家常煲菜", "一锅出晚饭", "热乎暖胃"])
    if contains_any(merged_text, ["煎", "焦", "铁板"]):
        candidates.extend(["香煎做法", "平底锅菜谱", "外脆里嫩"])
    if contains_any(merged_text, ["鱼", "虾", "贝", "海鲜", "鱿", "蛤"]):
        candidates.extend(["海鲜做法", "鲜味料理", "鲜香下饭"])
    if contains_any(merged_text, ["豆", "豆腐", "素"]):
        candidates.extend(["豆类料理", "豆腐做法", "素食家常菜"])

    candidates.extend(PUBLISH_GENERAL_TOPICS)
    candidates.extend(PUBLISH_PLATFORM_TOPIC_FALLBACKS.get(platform_key, ()))
    return candidates


def normalize_platform_topics(
    *,
    platform_key: str,
    count: int,
    dish_name: str,
    model_topics: Sequence[str],
    source_text: str,
    notes: str,
    description_body: str,
) -> list[str]:
    required_topics = get_required_publish_topics_for_platform(platform_key)
    required_tags = [format_topic_tag(item) for item in required_topics if format_topic_tag(item)]
    required_set = set(required_tags)

    normalized: list[str] = []
    seen: set[str] = set()

    for tag in required_tags:
        if tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
        if len(normalized) >= count:
            return normalized[:count]

    ordered_topics = [*model_topics, *build_platform_topic_fallback_candidates(
        platform_key=platform_key,
        dish_name=dish_name,
        source_text=source_text,
        notes=notes,
        description_body=description_body,
    )]

    for topic in ordered_topics:
        tag = format_topic_tag(topic)
        if not tag:
            continue
        if tag not in required_set and is_disallowed_publish_topic(topic, dish_name=dish_name):
            continue
        if tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
        if len(normalized) >= count:
            return normalized[:count]

    raise ValueError(f"{platform_key} 平台话题不足，无法凑够 {count} 个。")


def generate_platform_topic_assets(
    *,
    client: OpenAI,
    dish_name: str,
    topic_reference_text: str,
    source_text: str,
    notes: str,
    description_body: str,
) -> dict[str, list[str]]:
    platform_result = request_text_generation(
        client=client,
        system_prompt=build_platform_topic_prompt_system(),
        user_prompt=build_platform_topic_prompt_user(
            dish_name=dish_name,
            topic_reference_text=topic_reference_text,
        ),
        stage_name="多平台话题",
    )

    content = platform_result["content"]
    normalized_platform_topics: dict[str, list[str]] = {}
    for platform_key, platform_label, count in PUBLISH_PLATFORM_TOPIC_SPECS:
        model_topics = extract_platform_topic_section(content, platform_label)
        normalized_platform_topics[platform_key] = normalize_platform_topics(
            platform_key=platform_key,
            count=count,
            dish_name=dish_name,
            model_topics=model_topics,
            source_text=source_text,
            notes=notes,
            description_body=description_body,
        )

    return normalized_platform_topics


def build_publish_copy_system_prompt(fixed_dish_name: str) -> str:
    return f"""
你是阿叶造新菜账号的抖音图文运营编辑，负责把菜谱内容整理成更适合抖音发布的图文标题和图文描述。

你的目标：
1. 标题要像近期平台里更容易让人点开的图文标题，语气新鲜、亲切、有用，可以轻微玩梗，但不能油腻、不能空喊。
2. 描述要像抖音菜谱博主本人在发图文时的口吻，带一点俏皮、口语和网络感，但不要用力过猛，既有食欲也有实用信息。
3. 标题和描述都要尽量贴近中文互联网和抖音里的流行表达，但不能过度夸张，不要低质鸡汤，不要硬凑热搜。
4. 标题必须严格写成“菜名，卖点！”；菜名后必须用中文逗号，最后必须用中文叹号，卖点单独放在菜名后面。
5. 描述最后必须单独放一行 5 个话题标签，每个标签都以 # 开头；自动补充的话题不要生成菜名本身的话题，也不要生成 #阿叶造新菜。
6. 如果我另外给了“指定必带话题”，那几个话题必须优先保留在最终 5 个标签里。
7. 话题优先按抖音常见美食搜索词和活动型话题的写法去想；如果你无法确认实时热榜，就选更稳的通用高频美食话题，不要编造榜单来源。
8. 标题里必须保留菜名“{fixed_dish_name}”或非常直接地指向这道菜，不能改成别的菜。

输出格式必须严格如下，不要加解释：

【图文标题】
...

【图文描述】
...

补充要求：
1. 标题控制在 22 个汉字以内，且必须严格使用“{fixed_dish_name}，卖点！”这个格式。
2. 描述正文控制在 2 到 4 句，优先写“为什么好吃、什么场景会想做、做时最该注意什么”。
3. 描述最后一行只能放 5 个话题标签，不要多，不要少，不要换成普通短语。
4. 不要输出 emoji，不要输出英文段落，不要输出 Markdown 代码块。
5. 不要把描述统一写成“这道原创融合的……”“这个原创菜……”“先收藏，想做时……”“想吃时照着步骤做就稳……”这类机械模板开头或收尾。
6. 正文允许更像真人说话，可以从口感、翻车点、适合谁吃、上桌气氛、做法反差里切入，但不要每条都像同一个模板改词。
""".strip()


def build_publish_copy_user_prompt(
    dish_name: str,
    source_text: str,
    notes: str = "",
    source_label: str = "菜谱文案",
) -> str:
    note_text = notes.strip() or "无额外补充说明"
    required_topics = get_required_publish_topics()
    required_topics_text = " ".join(required_topics) if required_topics else "无"
    return f"""
当前菜名：{dish_name}
补充说明：{note_text}
参考内容类型：{source_label}
指定必带话题：{required_topics_text}

请基于下面这份内容，生成最终抖音图文标题和图文描述：

{source_text}
""".strip()


def extract_named_text_section(text: str, section_name: str) -> str:
    pattern = rf"【{re.escape(section_name)}】\s*(.*?)(?=\n【|\Z)"
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def split_description_body_and_tags(description: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    if not lines:
        return "", []

    body_lines: list[str] = []
    tags: list[str] = []
    for line in lines:
        line_tags = re.findall(r"#[^\s#]+", line)
        compact_line = line.replace(" ", "")
        if line_tags and compact_line.startswith("#"):
            tags.extend(line_tags)
        else:
            body_lines.append(line)

    return "\n".join(body_lines).strip(), tags


def build_local_publish_copy(
    dish_name: str,
    source_text: str,
    notes: str = "",
) -> dict[str, str]:
    combined_text = f"{dish_name} {notes} {source_text}"
    title = f"{dish_name}，{infer_publish_title_highlight(dish_name=dish_name, source_text=source_text, notes=notes)}！"

    practical_tip = "先把最关键的主料状态做到位，再补最后那口香气，成菜会稳很多。"
    if contains_any(combined_text, ["豆腐", "煎"]):
        practical_tip = "豆腐先煎到边角有点焦香再合味，整锅会更香也更立体。"
    elif contains_any(combined_text, ["鱼滑", "虾滑"]):
        practical_tip = "鱼滑别久煮，状态定住就收汁，口感会更弹更嫩。"
    elif contains_any(combined_text, ["煲", "锅", "汤"]):
        practical_tip = "这类锅气菜更看重先后顺序，先把底味做香，再回锅合味更容易出层次。"

    opening = f"{dish_name}这口是真挺上头，端上桌很容易被连着夹。"
    if contains_any(combined_text, ["脆", "锅巴", "炸"]):
        opening = f"{dish_name}这种脆口挂汁的路子很讨喜，第一口就挺抓人。"
    elif contains_any(combined_text, ["酸", "柠", "番茄", "梅子"]):
        opening = f"{dish_name}这味型很会勾人，酸香一上来就特别开胃。"
    elif contains_any(combined_text, ["辣", "椒", "麻"]):
        opening = f"{dish_name}这口辣麻香来得很直接，越吃越想配饭。"

    closing = "步骤别贪快，把关键那一下做对，成品就会比想象里稳。"
    if contains_any(combined_text, ["请客", "宴客", "聚餐"]):
        closing = "这类菜上桌挺有气氛，家里做也不会显得单薄。"

    description_body = f"{opening} {practical_tip} {closing}"
    topic_line = " ".join(infer_publish_topic_tags(dish_name=dish_name, source_text=source_text, notes=notes))
    description = f"{description_body}\n{topic_line}".strip()
    return {
        "title": title,
        "description": description,
    }


def normalize_publish_copy(
    dish_name: str,
    source_text: str,
    notes: str,
    title: str,
    description: str,
) -> dict[str, str]:
    normalized_title = normalize_publish_title(
        dish_name=dish_name,
        source_text=source_text,
        notes=notes,
        title=title,
    )

    description_body, parsed_tags = split_description_body_and_tags(description)
    normalized_body = description_body.strip()
    if not normalized_body:
        raise ValueError("图文描述正文为空。")

    required_topics = get_required_publish_topics()
    normalized_tags: list[str] = []
    seen: set[str] = set()
    for topic in required_topics:
        tag = format_topic_tag(topic)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized_tags.append(tag)
        if len(normalized_tags) >= 5:
            break

    inferred_topics = infer_publish_topic_tags(dish_name=dish_name, source_text=source_text, notes=notes)
    for topic in parsed_tags + inferred_topics:
        tag = format_topic_tag(topic)
        if not tag:
            continue
        if tag not in set(required_topics) and is_disallowed_publish_topic(topic, dish_name=dish_name):
            continue
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized_tags.append(tag)
        if len(normalized_tags) >= 5:
            break

    if len(normalized_tags) < 5:
        raise ValueError("图文描述缺少足够的话题标签。")

    normalized_description = f"{normalized_body}\n{' '.join(normalized_tags[:5])}".strip()
    return {
        "title": normalized_title,
        "description": normalized_description,
    }


def generate_publish_copy_assets(
    client: OpenAI,
    dish_name: str,
    source_text: str,
    timestamp: str,
    notes: str = "",
    topic_reference_text: str = "",
    output_name: str | None = None,
    source_label: str = "菜谱文案",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    output_name = output_name or dish_name
    output_dir = output_dir or build_run_output_dir(timestamp, dish_name)

    def build_publish_copy_result() -> tuple[dict[str, str], dict[str, str]]:
        publish_result = request_text_generation(
            client=client,
            system_prompt=build_publish_copy_system_prompt(fixed_dish_name=dish_name),
            user_prompt=build_publish_copy_user_prompt(
                dish_name=dish_name,
                source_text=source_text,
                notes=notes,
                source_label=source_label,
            ),
            stage_name="抖音发布文案",
        )
        title = extract_named_text_section(publish_result["content"], "图文标题")
        description = extract_named_text_section(publish_result["content"], "图文描述")
        normalized = normalize_publish_copy(
            dish_name=dish_name,
            source_text=source_text,
            notes=notes,
            title=title,
            description=description,
        )
        return publish_result, normalized

    publish_result, normalized = run_text_stage_with_validation_retry("抖音发布文案", build_publish_copy_result)

    description_body, _ = split_description_body_and_tags(normalized["description"])
    if not description_body:
        raise ValueError("图文描述正文为空，无法生成多平台话题版本。")

    topic_doc_text = topic_reference_text.strip()
    if not topic_doc_text:
        dish_name_file = ROOT_DIR / "dish_name.txt"
        if dish_name_file.exists() and dish_name_file.is_file():
            topic_doc_text = dish_name_file.read_text(encoding="utf-8").strip()
    if not topic_doc_text:
        topic_doc_text = dish_name

    platform_topics = generate_platform_topic_assets(
        client=client,
        dish_name=dish_name,
        topic_reference_text=topic_doc_text,
        source_text=source_text,
        notes=notes,
        description_body=description_body,
    )

    title_file = save_text_output(
        content=normalized["title"],
        output_dir=output_dir,
        timestamp=timestamp,
        base_name=output_name,
        suffix="_图文标题",
    )
    description_body_file = save_text_output(
        content=description_body,
        output_dir=output_dir,
        timestamp=timestamp,
        base_name=output_name,
        suffix="_图文描述正文",
    )

    platform_topic_files: dict[str, str] = {}
    platform_description_files: dict[str, str] = {}
    for platform_key, platform_label, _count in PUBLISH_PLATFORM_TOPIC_SPECS:
        topic_line = " ".join(platform_topics[platform_key]).strip()
        topic_file = save_text_output(
            content=topic_line,
            output_dir=output_dir,
            timestamp=timestamp,
            base_name=output_name,
            suffix=f"_{platform_label}话题",
        )
        description_file = save_text_output(
            content=f"{description_body}\n{topic_line}",
            output_dir=output_dir,
            timestamp=timestamp,
            base_name=output_name,
            suffix=f"_{platform_label}图文描述",
        )
        platform_topic_files[platform_key] = topic_file
        platform_description_files[platform_key] = description_file

    douyin_description_text = f"{description_body}\n{' '.join(platform_topics['douyin'])}".strip()

    print(f"图文标题已保存：{title_file}")
    print(f"通用图文描述正文已保存：{description_body_file}")
    for platform_key, platform_label, _count in PUBLISH_PLATFORM_TOPIC_SPECS:
        print(f"{platform_label}话题已保存：{platform_topic_files[platform_key]}")
        print(f"{platform_label}图文描述已保存：{platform_description_files[platform_key]}")

    return {
        "model": publish_result["model"],
        "title": normalized["title"],
        "description": douyin_description_text,
        "description_body": description_body,
        "platform_topics": platform_topics,
        "title_file": title_file,
        "description_file": platform_description_files["douyin"],
        "description_body_file": description_body_file,
        "platform_topic_files": platform_topic_files,
        "platform_description_files": platform_description_files,
    }


def validate_photoshop_auto_composite_setup() -> None:
    from tools.apply_photoshop_template_batch import validate_local_photoshop_setup

    validate_local_photoshop_setup()


def apply_photoshop_postprocess_to_output_dir(output_dir: Path) -> dict[str, str]:
    from tools.apply_photoshop_template_batch import apply_photoshop_template_batch_to_dir

    return apply_photoshop_template_batch_to_dir(input_dir=output_dir)


def auto_select_publish_images_for_output_dir(
    output_dir: Path,
    *,
    include_page_types: Sequence[str] | None = None,
) -> dict[str, Any]:
    from tools.select_publish_images import select_publish_images

    return select_publish_images(input_dir=output_dir, include_page_types=include_page_types)


def save_publish_selection_reports(publish_dir: Path, report_payload: dict[str, Any]) -> tuple[str, str]:
    from tools.select_publish_images import save_review_reports

    report_file, summary_file = save_review_reports(publish_dir, report_payload)
    return str(report_file), str(summary_file)


def merge_publish_selection_results(*selection_results: dict[str, Any]) -> dict[str, Any]:
    merged_results = [item for item in selection_results if item]
    if not merged_results:
        return {}

    combined_groups: list[dict[str, Any]] = []
    for selection_result in merged_results:
        combined_groups.extend(selection_result.get("groups", []))

    merged_payload = {
        "input_dir": merged_results[0].get("input_dir", ""),
        "publish_dir": merged_results[0].get("publish_dir", ""),
        "model": merged_results[0].get("model", ""),
        "dry_run": merged_results[0].get("dry_run", False),
        "copy_mode": merged_results[0].get("copy_mode", False),
        "title_retry_limit": merged_results[0].get("title_retry_limit", 0),
        "groups": combined_groups,
    }
    report_file, summary_file = save_publish_selection_reports(Path(merged_payload["publish_dir"]), merged_payload)
    merged_payload["report_file"] = report_file
    merged_payload["summary_file"] = summary_file
    return merged_payload


def find_selected_publish_image(selection_result: dict[str, Any], page_type: str) -> str:
    for group_result in selection_result.get("groups", []):
        if group_result.get("page_type") != page_type:
            continue
        selected_path = str(group_result.get("selected_output_path", "")).strip()
        if selected_path:
            return selected_path
    return ""


def remap_publish_selection_groups(selection_result: dict[str, Any], processed_file_map: dict[str, str]) -> None:
    for group_result in selection_result.get("groups", []):
        selected_output_path = str(group_result.get("selected_output_path", "")).strip()
        if not selected_output_path:
            continue
        resolved_path = str(Path(selected_output_path).resolve())
        group_result["selected_output_path"] = processed_file_map.get(resolved_path, selected_output_path)


def build_cover_reference_prompt_user_content(
    *,
    selected_page01_image_path: Path,
    draft_cover_prompt: str,
    dish_name: str,
) -> list[dict[str, Any]]:
    user_text = f"""
你现在会看到一张已经从图解01 里筛出来的首图海报，它就是这道菜当前最终选中的主菜视觉参考。

请忽略海报上的引导句、标题、黄条、食材卡、成败关键、步骤卡和底部文案，只观察并吸收这些真实视觉信息：
1. 主菜本身的形状、厚薄、大小差异、摆放关系和主体密度。
2. 餐盘或器皿的真实类型、颜色、边缘形状、占画面比例。
3. 桌面材质、色温、酱汁光泽、辅料点缀和景别关系。
4. 这张图里真正让人认出“就是这道菜”的关键外观特征。

你要做的是重写最终封面图 prompt，让封面背景里的主菜样式、器皿、桌面、酱汁和构图关系尽量贴近这张已筛中的首图，而不是只沿用抽象文字描述。

必须继续保留这些封面硬约束：
- 竖版 2:3。
- 菜名单列竖排压在中轴。
- 除菜名外画面其它区域 0 文字。
- 背景主菜和餐盘主动避开中轴标题通道。

最终 prompt 要继续保持简洁直接，不要自行增加复杂条件、长清单或重复限制。

下面是当前已有的封面 prompt 草稿，请在保留上述硬约束的前提下，按这张已筛中的首图把它改写得更贴近真实参考图：

{draft_cover_prompt}

最终只输出一条新的完整中文封面图 prompt，不要解释。菜名必须还是：{dish_name}
""".strip()

    return [
        {"type": "text", "text": user_text},
        {"type": "text", "text": f"参考首图文件名：{selected_page01_image_path.name}"},
        {"type": "image_url", "image_url": {"url": encode_image_file_as_data_url(selected_page01_image_path)}},
    ]


def refine_cover_prompt_with_selected_page01_reference(
    *,
    selected_page01_image_path: Path,
    style_reference: str,
    existing_cover_prompt: str,
    dish_name: str,
    bundle: dict[str, Any],
) -> dict[str, str]:
    review_client = build_multimodal_review_client()
    vertical_dish_name = cover_page.format_vertical_dish_name(dish_name)

    def build_cover_prompt_result() -> tuple[dict[str, str], str]:
        cover_prompt_result = request_multimodal_text_generation(
            client=review_client,
            system_prompt=cover_page.build_cover_prompt_system_prompt(
                style_reference=style_reference,
                fixed_dish_name=dish_name,
                vertical_dish_name=vertical_dish_name,
                bundle=bundle,
            ),
            user_content=build_cover_reference_prompt_user_content(
                selected_page01_image_path=selected_page01_image_path,
                draft_cover_prompt=existing_cover_prompt,
                dish_name=dish_name,
            ),
            stage_name="封面prompt",
            model=get_multimodal_review_model(),
        )
        cover_prompt = validate_cover_prompt_content(
            prompt_text=cover_prompt_result["content"],
            fixed_dish_name=dish_name,
            bundle=bundle,
        )
        return cover_prompt_result, cover_prompt

    try:
        cover_prompt_result, refined_cover_prompt = run_text_stage_with_validation_retry("封面prompt", build_cover_prompt_result)
    finally:
        close_openai_client(review_client)

    return {
        "model": cover_prompt_result["model"],
        "prompt": refined_cover_prompt,
    }


def remap_processed_image_paths(saved_files: Sequence[str], processed_file_map: dict[str, str]) -> list[str]:
    remapped_paths: list[str] = []
    for file_path in saved_files:
        resolved_path = str(Path(file_path).resolve())
        remapped_paths.append(processed_file_map.get(resolved_path, file_path))
    return remapped_paths


def generate_images_from_prompt_text(
    client: OpenAI,
    dish_name: str,
    prompt: str,
    timestamp: str,
    image_settings: dict[str, Any] | None = None,
    output_name: str | None = None,
    stage_name: str = "生成",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    image_settings = image_settings or get_image_settings()
    request_timeout = get_image_request_timeout_seconds()
    output_name = output_name or dish_name
    output_dir = output_dir or build_run_output_dir(timestamp, dish_name)

    print(f"正在调用模型：{image_settings['model']}")
    print(f"{stage_name}尺寸：{image_settings['size']}，质量：{image_settings['quality']}，数量：{image_settings['image_count']}")
    response = None
    for attempt in range(1, DEFAULT_REQUEST_RETRY_COUNT + 1):
        try:
            response = client.images.generate(
                model=image_settings["model"],
                prompt=prompt,
                n=image_settings["image_count"],
                size=image_settings["size"],
                quality=image_settings["quality"],
                timeout=request_timeout,
            )
            break
        except Exception as exc:
            if attempt >= DEFAULT_REQUEST_RETRY_COUNT or not is_timeout_error(exc):
                raise
            print(f"{stage_name}图片请求超时，正在重试第 {attempt + 1}/{DEFAULT_REQUEST_RETRY_COUNT} 次...")

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("接口已返回响应，但未发现可保存的图片数据。")

    print("图片接口已返回，正在保存文件...")
    prompt_stem = f"{timestamp}_{sanitize_file_name(output_name)}"
    saved_files = save_generated_images(
        image_items=image_items,
        dish_name=output_name,
        output_dir=output_dir,
        timestamp=timestamp,
        revised_prompt_output_dir=output_dir,
        revised_prompt_stem=prompt_stem,
    )

    return {
        "model": image_settings["model"],
        "size": image_settings["size"],
        "quality": image_settings["quality"],
        "image_count": image_settings["image_count"],
        "output_dir": str(output_dir),
        "saved_files": saved_files,
    }


def generate_recipe_text_assets_from_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, Any]:
    idea_file = ROOT_DIR / idea_file_name
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    style_reference = render_prompt_template(load_prompt(DEFAULT_PROMPT_FILE))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    text_client: OpenAI | None = None
    if is_auto_dish_generation_enabled():
        text_client = build_text_client()
        idea_payload = generate_auto_dish_idea(idea_file=idea_file, client=text_client)
        print(f"自动造菜已启用，本轮参考菜：{idea_payload['reference_dish']}")
        print(f"自动造菜菜系范围：{idea_payload['region_label']}")
        print(f"自动生成的新菜名已写回：{idea_file}")
    else:
        idea_payload = load_dish_idea(idea_file)

    print(f"正在读取创意文件：{idea_file}")
    print(f"本次菜品创意：{idea_payload['dish_idea']}")
    if idea_payload["notes"]:
        print(f"补充说明：{idea_payload['notes']}")

    if text_client is None:
        text_client = build_text_client()

    def build_recipe_text_result() -> tuple[dict[str, str], str]:
        recipe_result = request_text_generation(
            client=text_client,
            system_prompt=build_recipe_system_prompt(
                ad_copy=ad_copy,
                fixed_dish_name=idea_payload["dish_idea"],
            ),
            user_prompt=build_recipe_user_prompt(
                dish_idea=idea_payload["dish_idea"],
                notes=idea_payload["notes"],
            ),
            stage_name="创意菜谱",
        )
        recipe_text = normalize_recipe_text(
            recipe_text=recipe_result["content"],
            fixed_dish_name=idea_payload["dish_idea"],
            ad_copy=ad_copy,
            notes=idea_payload["notes"],
        )
        recipe_text = validate_recipe_text_content(
            recipe_text=recipe_text,
            fixed_dish_name=idea_payload["dish_idea"],
        )
        return recipe_result, recipe_text

    recipe_result, recipe_text = run_text_stage_with_validation_retry("创意菜谱", build_recipe_text_result)

    generated_dish_name = idea_payload["dish_idea"]
    run_output_dir = build_run_output_dir(timestamp, generated_dish_name)
    
    idea_backup_file = backup_dish_idea_file(idea_file, run_output_dir, timestamp, generated_dish_name)
    if idea_backup_file:
        print(f"菜谱创意灵感已备份：{idea_backup_file}")
    
    guide_bundle = build_recipe_bundle_from_recipe_text(
        recipe_text=recipe_text,
        fixed_dish_name=generated_dish_name,
        ad_copy=ad_copy,
    )
    creative_file = save_text_output(
        content=recipe_text,
        output_dir=run_output_dir,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix=f"_{page01_recipe.FILE_LABEL}",
    )
    print(f"创意菜谱已保存：{creative_file}")

    def build_page01_prompt_result() -> tuple[dict[str, str], str]:
        prompt_result = request_text_generation(
            client=text_client,
            system_prompt=page01_recipe.build_page01_prompt_system_prompt(
                style_reference=style_reference,
                fixed_dish_name=generated_dish_name,
            ),
            user_prompt=page01_recipe.build_page01_prompt_user_prompt(
                recipe_text=recipe_text,
                fixed_dish_name=generated_dish_name,
            ),
            stage_name="文生图prompt",
        )
        image_prompt = validate_page01_prompt_content(
            prompt_text=prompt_result["content"],
            fixed_dish_name=generated_dish_name,
            bundle=guide_bundle,
        )
        return prompt_result, image_prompt

    prompt_result, image_prompt = run_text_stage_with_validation_retry("文生图prompt", build_page01_prompt_result)

    prompt_file = save_text_output(
        content=image_prompt,
        output_dir=run_output_dir,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix=f"_{page01_recipe.FILE_LABEL}_文生图prompt",
    )
    print(f"文生图 prompt 已保存：{prompt_file}")

    guide_page_results = generate_guide_pages(
        text_client=text_client,
        dish_name=generated_dish_name,
        notes=idea_payload["notes"],
        recipe_text=recipe_text,
        style_reference=style_reference,
        timestamp=timestamp,
        output_text_dir=run_output_dir,
        output_prompt_dir=run_output_dir,
        bundle=guide_bundle,
        request_text_generation=request_text_generation,
        run_text_stage_with_validation_retry=run_text_stage_with_validation_retry,
        save_text_output=save_text_output,
        validate_page_text_content=validate_guide_page_text_content,
        validate_page_prompt_content=validate_guide_page_prompt_content,
    )

    def build_cover_prompt_result() -> tuple[dict[str, str], str]:
        vertical_dish_name = cover_page.format_vertical_dish_name(generated_dish_name)
        cover_prompt_result = request_text_generation(
            client=text_client,
            system_prompt=cover_page.build_cover_prompt_system_prompt(
                style_reference=style_reference,
                fixed_dish_name=generated_dish_name,
                vertical_dish_name=vertical_dish_name,
                bundle=guide_bundle,
            ),
            user_prompt=cover_page.build_cover_prompt_user_prompt(
                bundle=guide_bundle,
                fixed_dish_name=generated_dish_name,
                vertical_dish_name=vertical_dish_name,
            ),
            stage_name="封面prompt",
        )
        cover_prompt = validate_cover_prompt_content(
            prompt_text=cover_prompt_result["content"],
            fixed_dish_name=generated_dish_name,
            bundle=guide_bundle,
        )
        return cover_prompt_result, cover_prompt

    cover_prompt_result, cover_prompt = run_text_stage_with_validation_retry("封面prompt", build_cover_prompt_result)

    cover_name = cover_page.build_cover_output_name(generated_dish_name)
    cover_prompt_file = save_text_output(
        content=cover_prompt,
        output_dir=run_output_dir,
        timestamp=timestamp,
        base_name=cover_name,
        suffix="_文生图prompt",
    )
    print(f"封面 prompt 已保存：{cover_prompt_file}")

    publish_copy_result = generate_publish_copy_assets(
        client=text_client,
        dish_name=generated_dish_name,
        source_text=recipe_text,
        timestamp=timestamp,
        notes=idea_payload["notes"],
        topic_reference_text=idea_payload.get("dish_idea", ""),
        output_name=generated_dish_name,
        source_label="一页菜谱定稿文案",
        output_dir=run_output_dir,
    )

    page01_result = {
        "page_number": 1,
        "page_name": "一页菜谱",
        "file_label": page01_recipe.FILE_LABEL,
        "text_model": recipe_result["model"],
        "prompt_model": prompt_result["model"],
        "text_file": creative_file,
        "prompt_file": prompt_file,
        "prompt": image_prompt,
        "output_name": page01_recipe.build_page01_output_name(generated_dish_name),
    }

    return {
        "dish_idea": idea_payload["dish_idea"],
        "dish_name": generated_dish_name,
        "guide_bundle": guide_bundle,
        "style_reference": style_reference,
        "notes": idea_payload["notes"],
        "auto_generated": idea_payload.get("auto_generated", "0"),
        "reference_dish": idea_payload.get("reference_dish", ""),
        "region_code": idea_payload.get("region_code", ""),
        "region_label": idea_payload.get("region_label", ""),
        "dish_memory_file": idea_payload.get("memory_file", ""),
        "text_model": recipe_result["model"],
        "prompt_model": prompt_result["model"],
        "cover_prompt_model": cover_prompt_result["model"],
        "creative_file": creative_file,
        "prompt_file": prompt_file,
        "cover_prompt_file": cover_prompt_file,
        "output_root": str(run_output_dir),
        "creative_output_dir": str(run_output_dir),
        "prompt_output_dir": str(run_output_dir),
        "publish_output_dir": str(run_output_dir),
        "image_output_dir": str(run_output_dir),
        "publish_model": publish_copy_result["model"],
        "publish_title": publish_copy_result["title"],
        "publish_description": publish_copy_result["description"],
        "publish_title_file": publish_copy_result["title_file"],
        "publish_description_file": publish_copy_result["description_file"],
        "publish_description_body_file": publish_copy_result["description_body_file"],
        "publish_platform_topic_files": publish_copy_result["platform_topic_files"],
        "publish_platform_description_files": publish_copy_result["platform_description_files"],
        "guide_pages": [
            page01_result,
            *guide_page_results,
        ],
        "cover_prompt": cover_prompt,
        "cover_output_name": cover_name,
        "timestamp": timestamp,
    }


def generate_page01_prompt_only_from_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, Any]:
    idea_file = ROOT_DIR / idea_file_name
    ad_copy = load_text_variable(DEFAULT_AD_COPY_FILE, "guanggaoyu")
    style_reference = render_prompt_template(load_prompt(DEFAULT_PROMPT_FILE))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"正在读取创意文件：{idea_file}")
    idea_payload = load_dish_idea(idea_file)
    print(f"本次菜品创意：{idea_payload['dish_idea']}")
    if idea_payload["notes"]:
        print(f"补充说明：{idea_payload['notes']}")

    text_client = build_text_client()

    def build_recipe_text_result() -> tuple[dict[str, str], str]:
        recipe_result = request_text_generation(
            client=text_client,
            system_prompt=build_recipe_system_prompt(
                ad_copy=ad_copy,
                fixed_dish_name=idea_payload["dish_idea"],
            ),
            user_prompt=build_recipe_user_prompt(
                dish_idea=idea_payload["dish_idea"],
                notes=idea_payload["notes"],
            ),
            stage_name="创意菜谱",
        )
        recipe_text = normalize_recipe_text(
            recipe_text=recipe_result["content"],
            fixed_dish_name=idea_payload["dish_idea"],
            ad_copy=ad_copy,
            notes=idea_payload["notes"],
        )
        recipe_text = validate_recipe_text_content(
            recipe_text=recipe_text,
            fixed_dish_name=idea_payload["dish_idea"],
        )
        return recipe_result, recipe_text

    recipe_result, recipe_text = run_text_stage_with_validation_retry("创意菜谱", build_recipe_text_result)

    generated_dish_name = idea_payload["dish_idea"]
    run_output_dir = build_run_output_dir(timestamp, generated_dish_name)
    
    idea_backup_file = backup_dish_idea_file(idea_file, run_output_dir, timestamp, generated_dish_name)
    if idea_backup_file:
        print(f"菜谱创意灵感已备份：{idea_backup_file}")
    
    guide_bundle = build_recipe_bundle_from_recipe_text(
        recipe_text=recipe_text,
        fixed_dish_name=generated_dish_name,
        ad_copy=ad_copy,
    )
    creative_file = save_text_output(
        content=recipe_text,
        output_dir=run_output_dir,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix=f"_{page01_recipe.FILE_LABEL}",
    )
    print(f"创意菜谱已保存：{creative_file}")

    def build_page01_prompt_result() -> tuple[dict[str, str], str]:
        prompt_result = request_text_generation(
            client=text_client,
            system_prompt=page01_recipe.build_page01_prompt_system_prompt(
                style_reference=style_reference,
                fixed_dish_name=generated_dish_name,
            ),
            user_prompt=page01_recipe.build_page01_prompt_user_prompt(
                recipe_text=recipe_text,
                fixed_dish_name=generated_dish_name,
            ),
            stage_name="文生图prompt",
        )
        image_prompt = validate_page01_prompt_content(
            prompt_text=prompt_result["content"],
            fixed_dish_name=generated_dish_name,
            bundle=guide_bundle,
        )
        return prompt_result, image_prompt

    prompt_result, image_prompt = run_text_stage_with_validation_retry("文生图prompt", build_page01_prompt_result)

    prompt_file = save_text_output(
        content=image_prompt,
        output_dir=run_output_dir,
        timestamp=timestamp,
        base_name=generated_dish_name,
        suffix=f"_{page01_recipe.FILE_LABEL}_文生图prompt",
    )
    print(f"图解01 文生图 prompt 已保存：{prompt_file}")

    return {
        "dish_idea": idea_payload["dish_idea"],
        "dish_name": generated_dish_name,
        "notes": idea_payload["notes"],
        "text_model": recipe_result["model"],
        "prompt_model": prompt_result["model"],
        "creative_file": creative_file,
        "prompt_file": prompt_file,
        "prompt": image_prompt,
        "output_root": str(run_output_dir),
        "timestamp": timestamp,
    }


def generate_auto_dish_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, str]:
    idea_file = ROOT_DIR / idea_file_name
    text_client = build_text_client()
    return generate_auto_dish_idea(idea_file=idea_file, client=text_client)


def generate_recipe_assets_from_idea_file(
    idea_file_name: str = "dish_name.txt",
) -> dict[str, Any]:
    result = generate_recipe_text_assets_from_idea_file(idea_file_name=idea_file_name)
    photoshop_auto_composite_enabled = is_photoshop_auto_composite_enabled()
    publish_auto_select_enabled = is_publish_auto_select_enabled()

    if photoshop_auto_composite_enabled:
        print("主流程已开启 Photoshop 自动合成，先校验本地 Photoshop 和 PSD 模板...")
        validate_photoshop_auto_composite_setup()
        print("Photoshop 自动合成校验通过，继续生成图片。")

    image_client = build_image_client()
    page01_image_settings = get_image_settings()
    tujie_image_settings = get_tujie_image_settings()
    cover_image_settings = get_cover_image_settings()
    print(
        "所有创意和 prompt 已完成，开始先生成图解01到图解06..."
        f"首图模型：{page01_image_settings['model']}，图解模型：{tujie_image_settings['model']}，封面模型：{cover_image_settings['model']}"
    )

    page01_result = result["guide_pages"][0]
    page01_image_result = generate_images_from_prompt_text(
        client=image_client,
        dish_name=result["dish_name"],
        prompt=page01_result["prompt"],
        timestamp=result["timestamp"],
        image_settings=page01_image_settings,
        output_name=page01_result["output_name"],
        stage_name=f"图解01 {page01_recipe.PAGE_NAME}",
    )

    page01_result["image_model"] = page01_image_result["model"]
    page01_result["saved_files"] = page01_image_result["saved_files"]

    for page_result in result["guide_pages"][1:]:
        page_image_result = generate_images_from_prompt_text(
            client=image_client,
            dish_name=result["dish_name"],
            prompt=page_result["prompt"],
            timestamp=result["timestamp"],
            image_settings=tujie_image_settings,
            output_name=page_result["output_name"],
            stage_name=page_result["page_name"],
        )
        page_result["image_model"] = page_image_result["model"]
        page_result["saved_files"] = page_image_result["saved_files"]

    result["image_model"] = page01_image_result["model"]
    result["cover_image_model"] = ""
    result["saved_files"] = page01_image_result["saved_files"]
    result["cover_saved_files"] = []
    result["photoshop_auto_composite"] = "1" if photoshop_auto_composite_enabled else "2"
    result["photoshop_processed_files"] = []
    result["publish_auto_select"] = "1" if publish_auto_select_enabled else "2"
    result["publish_selection"] = {}
    result["publish_selection_report_file"] = ""
    result["publish_selection_summary_file"] = ""
    result["publish_selected_files"] = []

    if publish_auto_select_enabled:
        print("图解01到图解06 已生成完成，开始先筛选前 6 组 publish 图片...")
        initial_publish_selection_result = auto_select_publish_images_for_output_dir(
            Path(result["output_root"]),
            include_page_types=("page01", "guide_page"),
        )
        selected_page01_path_text = find_selected_publish_image(initial_publish_selection_result, "page01")
        if not selected_page01_path_text:
            raise RuntimeError("前 6 组 publish 评审完成，但没有找到已选中的图解01 首图，无法继续按选中首图生成封面。")

        selected_page01_path = Path(selected_page01_path_text)
        print(f"已找到筛中的首图参考：{selected_page01_path.name}，开始按这张首图重写封面 prompt...")
        refined_cover_prompt_result = refine_cover_prompt_with_selected_page01_reference(
            selected_page01_image_path=selected_page01_path,
            style_reference=result["style_reference"],
            existing_cover_prompt=result["cover_prompt"],
            dish_name=result["dish_name"],
            bundle=result["guide_bundle"],
        )
        result["cover_prompt_model"] = refined_cover_prompt_result["model"]
        result["cover_prompt"] = refined_cover_prompt_result["prompt"]
        result["cover_prompt_file"] = save_text_output(
            content=result["cover_prompt"],
            output_dir=Path(result["output_root"]),
            timestamp=result["timestamp"],
            base_name=result["cover_output_name"],
            suffix="_文生图prompt",
        )
        print(f"封面 prompt 已按筛中的首图重写：{result['cover_prompt_file']}")

        print("前 6 组 publish 图片已确定，开始生成封面...")
        cover_image_result = generate_images_from_prompt_text(
            client=image_client,
            dish_name=result["dish_name"],
            prompt=result["cover_prompt"],
            timestamp=result["timestamp"],
            image_settings=cover_image_settings,
            output_name=result["cover_output_name"],
            stage_name="封面",
        )
        result["cover_image_model"] = cover_image_result["model"]
        result["cover_saved_files"] = cover_image_result["saved_files"]

        print("封面已生成，开始单独筛选封面并合并 publish 报告...")
        cover_publish_selection_result = auto_select_publish_images_for_output_dir(
            Path(result["output_root"]),
            include_page_types=("cover",),
        )
        publish_selection_result = merge_publish_selection_results(
            initial_publish_selection_result,
            cover_publish_selection_result,
        )
        result["publish_selection"] = publish_selection_result
        result["publish_selection_report_file"] = str(publish_selection_result.get("report_file", ""))
        result["publish_selection_summary_file"] = str(publish_selection_result.get("summary_file", ""))
        result["publish_selected_files"] = [
            str(group_result.get("selected_output_path", ""))
            for group_result in publish_selection_result.get("groups", [])
            if str(group_result.get("selected_output_path", "")).strip()
        ]

        if photoshop_auto_composite_enabled and result["publish_selected_files"]:
            publish_dir = Path(str(publish_selection_result.get("publish_dir", "")))
            print("publish 图片已全部选定，开始只对 publish 文件夹执行 Photoshop 模板合成...")
            processed_file_map = apply_photoshop_postprocess_to_output_dir(publish_dir)
            result["photoshop_processed_files"] = list(processed_file_map.values())
            remap_publish_selection_groups(result["publish_selection"], processed_file_map)
            result["publish_selected_files"] = remap_processed_image_paths(result["publish_selected_files"], processed_file_map)
            report_file, summary_file = save_publish_selection_reports(publish_dir, result["publish_selection"])
            result["publish_selection_report_file"] = report_file
            result["publish_selection_summary_file"] = summary_file
    else:
        print("publish 自动筛选未开启，按原顺序继续生成封面...")
        cover_image_result = generate_images_from_prompt_text(
            client=image_client,
            dish_name=result["dish_name"],
            prompt=result["cover_prompt"],
            timestamp=result["timestamp"],
            image_settings=cover_image_settings,
            output_name=result["cover_output_name"],
            stage_name="封面",
        )
        result["cover_image_model"] = cover_image_result["model"]
        result["cover_saved_files"] = cover_image_result["saved_files"]

        if photoshop_auto_composite_enabled:
            print("所有图片已生成完成，开始执行 Photoshop 模板合成并覆盖导出 JPG...")
            processed_file_map = apply_photoshop_postprocess_to_output_dir(Path(result["output_root"]))
            page01_result["saved_files"] = remap_processed_image_paths(page01_result["saved_files"], processed_file_map)
            for page_result in result["guide_pages"][1:]:
                page_result["saved_files"] = remap_processed_image_paths(page_result["saved_files"], processed_file_map)
            result["saved_files"] = remap_processed_image_paths(result["saved_files"], processed_file_map)
            result["cover_saved_files"] = remap_processed_image_paths(result["cover_saved_files"], processed_file_map)
            result["photoshop_processed_files"] = list(processed_file_map.values())

    return result


def generate_images_from_prompt_file(
    dish_name: str,
    prompt_file_name: str = "临时调试prompt.txt",
) -> dict[str, Any]:
    prompt_file = ROOT_DIR / prompt_file_name
    prompt_template = load_prompt(prompt_file)
    prompt = render_prompt_template(prompt_template)

    client = build_image_client()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_result = generate_images_from_prompt_text(
        client=client,
        dish_name=dish_name,
        prompt=prompt,
        timestamp=timestamp,
    )

    output_dir = build_run_output_dir(timestamp, dish_name)
    rendered_prompt_file = output_dir / f"{timestamp}_{sanitize_file_name(dish_name)}_原始prompt.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_prompt_file.write_text(prompt, encoding="utf-8")

    return {
        "model": image_result["model"],
        "size": image_result["size"],
        "quality": image_result["quality"],
        "output_dir": str(output_dir),
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
