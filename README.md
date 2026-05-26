# 一页厨师图片生成器

这是第一阶段的完整可运行版本。程序会读取 dish_name.txt 中的菜名创意和补充说明，先生成图解01的一页菜谱主图，再按固定顺序继续生成图解02到图解06的多图解页面，最后额外输出一张竖版 9:16 的封面图，统一保存到 output 目录下。

## 当前版本

v0.60

## 使用方式

1. 在 .env 中配置 OPENAI_API_KEY 和 DOUBAO_API_KEY。
2. 默认情况下程序会先按 config.env 中的自动造菜配置，自动从 chuantongcaipu.txt 抽一条传统菜做参考，再自动改写 dish_name.txt；如果你想手动指定菜名，就把 AUTO_GENERATE_DISH_IDEA 改成 0，再手填 dish_name.txt。
3. 如需调整自动造菜开关、参考菜系范围、图片模型、质量、数量、尺寸、超时和文本模型等参数，修改 config.env。
4. 如需调整固定关注文案，修改 guanggaoyu.txt。
5. 如需调整固定 VI 风格参考，修改 临时调试prompt.txt。
6. 运行 main.py。
7. 如需单独只生图，把要使用的 prompt 文本放进 tools/prompt 目录，每个 txt 文件代表一次生图任务，然后运行 tools/generate_images_from_prompt_folder.py。
8. 如需只生成创意 txt 和文生图 txt，而不生成任何图片，直接运行 tools/generate_text_prompts_from_dish_name.py，它会读取 dish_name.txt 并把结果保存到 output 下对应的“日期时间戳_菜品名”目录。
9. 主流程和文本模式现在都会额外生成“抖音图文标题”和“抖音图文描述”，描述最后会自动带 5 个更贴近菜谱的潜力话题标签。
10. 如需单独只刷新 dish_name.txt 而不继续生成 prompt 或图片，直接运行 tools/generate_auto_dish_idea.py。
11. 如需从某个 output 子目录里的多版本图片中自动筛出更适合发布的版本，运行 tools/select_publish_images.py；它会按页面分组让豆包逐页打分，再把胜出图移入 publish 文件夹。
12. 默认情况下，主流程在整套图片和 Photoshop 合成都完成后，也会自动执行一次 publish 筛图并输出报告；若只想关掉这一步，把 config.env 里的 PUBLISH_AUTO_SELECT 改成 2。
13. 如需手动指定 1 个或多个必须保留在最终 5 个抖音话题里的标签，修改 config.env 里的 PUBLISH_REQUIRED_TOPICS；多个话题可用空格、逗号、分号或换行分隔，写不写 # 都可以。
14. 如需把 publish 目录里的图和对应标题/描述自动投喂到抖音创作者页，运行 tools/douyin_publish.py；若当前没有可接管的调试浏览器，脚本会自动拉起一个独立的 Chrome 自动化窗口。

## 当前状态

1. main.py 已切换为完整流程入口，可自动完成“图解01一页菜谱 -> 图解02到图解06多图解 -> 封面图”。
2. 本地 git 仓库已初始化并完成首个中文提交。
3. GitHub 远程仓库已创建并完成首次推送。
4. 关注文案改为从 guanggaoyu.txt 动态注入到 prompt。
5. 当前临时调试模板已固定为截图1风格的爆款海报版式，作为 VI 参考。
6. 当前广告语变量已更新为“关注@阿叶造新菜，家用开店都不赖！”。
7. 当前主标题强制使用 dish_name.txt 第一行原文，不再由模型改菜名。
8. 图解01 与后续多图解页现在会保留原有 VI 与英雄镜头结构，只把食材和局部照片层的质感往真实拍摄方向收紧。
9. 图解02 到图解06 会按固定阅读顺序生成：食材怎么挑 -> 关键细节拆解 -> 省时技巧与工具 -> 平替方案 -> 口味调整。
10. 图解01、图解02到图解06，以及封面图现在都各自拥有独立页面脚本，统一复用同一套 VI 和 prompt 结构。
11. 文本接口超时时，多图解页面也会自动切换本地模板兜底，不让整条流程中断。
12. 当前会额外生成 1 张竖版 9:16 的封面图，文件名会在菜名后追加“封面”。
13. 封面图会沿用一页菜谱 VI，但构图已切到更高更窄的 9:16 长竖版，上半段为竖排菜名留出更长通道，主菜更多压在中下段。
14. 主流程现在可在全部 gpt-image-2 图片落盘后，自动调用本地 Photoshop 套 PSD 模板，并把整轮输出目录里的成图统一覆盖导出为 JPG。
15. image_generator.py 现在只保留通用素材整理、接口调用和总流程编排，图解01与封面的页面 prompt 逻辑已移动到 guide_pages 目录。
16. 图解02到图解06的本地兜底文案已改为按 dish_name.txt 输入动态生成，不再写死豆腐肉沫类固定菜文案。
17. 文本模型已临时切换为豆包新版本（doubao-seed-2-0-pro-260215），生图模型继续使用 gpt-image-2，不受影响。
18. 运行流程已调整为先产出全部创意和 prompt，再统一调用 gpt-image-2 生图。
19. 主流程已再次修正为先完成全部文案和 prompt，再统一进入所有图片的生图阶段。
20. 图解01、图解02到图解06以及封面图里只要出现任何菜品、食材、半成品、局部状态或背景食物，都已统一强制为 iPhone 主摄 1x 默认相机直出照片感。
21. 图解01 首图模板已单独强化为“像先用 iPhone 主摄拍好真实晚饭照片再排进海报里”，并继续保留筷子夹起主菜悬空的动作感。
22. 图解01 首图最终 prompt 的末尾已固定追加“请优先保证主菜图片看起来像真实手机实拍，饱和度降低20%，再完成整张海报的文字和排版。”这句收尾指令。
23. 封面图模板和默认尺寸已改为竖版 9:16，第2张到第6张的图片 prompt 也会统一带上“关注@阿叶造新菜，家用开店都不赖！”的细长底部关注文案，不生成图标。
24. 当前新增 config.env 作为统一可调参数文件，支持每行尾部直接写中文注释；运行时按“外部环境变量 > .env > config.env”的优先级读取。
25. 当前默认图片模型已更新为官方 GPT Image 2 最新快照 gpt-image-2-2026-04-21。
26. 当前主图、图解页与封面图的默认图片质量都已调整为 medium，作为更均衡的性价比方案。
27. 新增 tools/generate_images_from_prompt_folder.py，可单独扫描 tools/prompt 目录中的 txt 文件并逐个生图；若目录里没有 txt 文件，脚本会直接结束，不会误生成。
28. 新增 tools/generate_text_prompts_from_dish_name.py，可单独读取 dish_name.txt，只生成创意 txt 和文生图 txt，不调用 gpt-image-2，也不生成图片。
29. 封面模板已收紧为只允许“菜品名 + 背景图片”两类内容；封面 prompt 不再注入整份菜谱文案，也不再允许顶部引导句、副标题、收藏条、关注条等任何额外文字。
30. 主流程在文本阶段结束后会自动新增抖音发布文案，包括图文标题和图文描述；描述最后会自动带 5 个贴合菜谱的潜力话题标签，并与本轮其它文件一起保存到 output 下对应的单次运行目录。
31. tools/generate_text_prompts_from_dish_name.py 现在也会同步产出抖音图文标题和图文描述；tools/generate_images_from_prompt_folder.py 新增 --text-only 模式，可只根据 tools/prompt 下的 txt 生成抖音发布文案而不生图。
32. 文本生成链路已新增结果结构校验：创意菜谱、多图解文案、各类文生图 prompt 若返回空文本、占位文本或缺少关键结构，会直接切到本地兜底，不再把“封面prompt 输出”这类垃圾内容写进 output。
33. 图解01 首图已补充标题严格水平居中约束，并明确主菜照片必须是一整张主体背景大图，不能再被生成成带边框的独立照片卡；图解02到图解06的标题和副标题也统一要求严格水平居中。
34. chuantongcaipu.txt 现已重排为“中餐 / 国外”双层分类结构；每个大区下继续按 15 个主烹饪方式分组，便于程序先按大区再按烹饪方式读取。
35. 当前菜名总表已扩充到 939 道，其中中餐 560 道、国外 379 道；本轮继续补入 196 道中东、北非、东欧、拉美相关传统菜，覆盖葡萄叶卷、雪克舒卡、哈里拉汤、曼萨夫、库纳法、波兰酸黑麦汤、阿根廷玉米炖肉、墨西哥鼹酱鸡等高频经典菜名。
36. 图解01 首图顶部引导句的生成逻辑已改为更高变化的抖音短钩子写法；程序会主动规避“周末请客”“家宴”“请客就做”“有面子”这类过度常见老词，并在需要时自动改写为更鲜活的动态文案。
37. 当前新增“自动造菜”前置环节：默认开启后，主流程会先从 chuantongcaipu.txt 按配置菜系随机挑选一条传统菜做参考，让豆包自动生成全新的菜名与长描述，并写回 dish_name.txt 后再继续整条文本和生图流程。
38. 自动造菜的菜系范围可直接用数字切换：0 全部随机，1 中餐，2 新马泰/东南亚，3 日韩，4 西餐/欧洲，5 中东北非，6 东欧，7 拉美。
39. 当前新增 dish_idea_memory.jsonl 记忆文件，会持续记录已经用过的传统参考菜和已经生成过的新菜名，程序会据此避开重复参考菜和重复新菜名；并新增 tools/generate_auto_dish_idea.py 作为单独刷新 dish_name.txt 的脚本。
40. 图解01 首图现在会额外拆分并约束“器皿与摆盘 / 桌面与环境 / 背景陪衬”三个主画面字段；首图 prompt 不再默认把所有菜都往木桌、陶盘、木托和小碗这套固定模板里压，而是会按菜本身的上桌逻辑去选择铸铁盘、长鱼盘、深碗、砂锅、石面、水磨石、亚麻餐垫等更匹配的场景。
41. 当前 VI 参考模板与首图 prompt 模板都已收紧：即便延续统一海报风格，也不能再把“背景可以有木桌和香料小碗”理解成所有菜都必须同一套桌面与摆盘；后景陪衬现在要求只保留与本菜直接相关的少量失焦元素。
42. 创意菜谱归一化阶段现在会保留豆包已经生成好的“器皿与摆盘”“桌面与环境”“背景陪衬”字段，不再无条件改写；只有字段缺失或明显是占位文本时，才会启用本地推断补齐。
43. 本地场景推断已改成优先读取摆盘、装盘、上桌、桌面、台面等场景语句，不再被“淀粉”“白胡椒粉”“蒸锅”“脆甜”这类非场景词误判；像鲜笋鸡丝蒸酿腐皮这类蒸卷热菜，当前会落到浅口圆盘加亚麻/木台热菜场景，而不是再次回到炸物盘和米白石面模板。
44. 创意菜谱阶段现在会额外校验“顶部引导句”和“黄条副标题”是否在复用同一组词；如果上下两行只是同词换行、只差空格，或只多减 1 到 2 个字，程序会自动把顶部改写回钩子句，避免再出现“菜名上面和下面卖点几乎一样”的情况。
45. 多图解图片 prompt 现在会主动清理“当前菜名”“当前页面”“页面标题”“页面副标题”“内容卡1-3”“页尾提示”等程序化字段标签；共享模板会先把图解文案改写成仅含实际展示文字值的摘要，再进入图片 prompt 阶段，且如果最终 prompt 仍混入这些标签，程序会直接拦截并切回本地干净兜底。
46. 封面 prompt 现在不再只写“暖色虚化菜品背景”这种泛化描述，而是会强制锁定首图同一道菜的器皿、桌面、陪衬、主菜主体和酱汁关系；如果文本模型没有把这些首图同场景字段写进封面 prompt，程序会直接拦截并切回本地封面模板。
47. 首图归一化阶段现在会主动识别并替换模板化场景字段；如果豆包把不同菜又写回浅灰石面、水磨石、简洁干净、小碟失焦这类常见环境，程序会自动切到本地更贴菜的场景推断，不再让煎鱼、炸卷、锅物等不同结构菜继续挤在同一种拍摄环境里。
48. 封面图的菜名竖排现在进一步收紧为“画布中轴单列竖排 + 中间整条标题通道保留”；背景主菜和餐盘必须主动避让中轴并退到下半部或左右下角，避免再出现菜名跑到左侧的封面。
49. 主流程与单独调试生图链路现在都改为单文件夹输出：程序会在 output 下按“日期时间戳_菜品名”创建目录，并把图解文案、图解 prompt、封面 prompt、图片、revised prompt、抖音图文标题与图文描述全部放进同一个目录，便于直接交付和打包。
50. 图解01 首图标题区现在进一步收紧为“沿同一条画面正中竖线向下堆叠的中心柱布局”；image_generator.py 已新增首图 prompt 专用中轴校验，若最终 prompt 没写出中轴、中心点、左右留白对称与禁止偏左这类硬约束，会直接拦截并切回本地首图模板。
51. 封面图默认尺寸现已收口为 864x1536。这一尺寸继续保持竖版 9:16，且满足 OpenAI 官方当前对 gpt-image-2 的尺寸约束：最长边不超过 3840、宽高都必须是 16 的倍数、总像素不超过 8294400。当前封面像素规格已调整到与其它图接近的中等尺寸，不再单独拉到满规格；程序仍会在本地先校验图片尺寸配置。
52. tools/apply_photoshop_template_batch.py 现已收口为本地 Photoshop 批处理工具；会把指定目录中的 .jpg/.jpeg/.png 逐张替换进 PSD 模板，再直接调用本机 Photoshop 导出高质量 JPG 覆盖原图。
53. 主流程默认会在全部 gpt-image-2 图片生成完后自动调用这套本地 Photoshop 链路；图层混合模式、文字层、纹理层、自然饱和度等视觉处理统一由 PSD 模板自身负责。
54. config.env 里的 Photoshop 配置已收口为 PHOTOSHOP_AUTO_COMPOSITE、PHOTOSHOP_LOCAL_EXE、PHOTOSHOP_TEMPLATE_FILE、PHOTOSHOP_TEMPLATE_SMART_OBJECT_LAYER、PHOTOSHOP_JPEG_QUALITY、PHOTOSHOP_JOB_TIMEOUT_SECONDS；不再保留 Adobe/S3 云配置。
55. 图解01 首图本地兜底里的副标题与成败关键已收紧食材判定逻辑；非豆腐菜不再因为“煎 / 焦香 / 软糯 / 香”这类泛词被误写成“豆腐两面煎焦香”或“豆腐先煎出焦边再合炒”，创意菜谱校验阶段也会在落盘前自动纠正这类错误副标题。
56. 抖音图文标题现在会强制规范化为“菜名，卖点！”；即使文本模型漏掉中文逗号或中文叹号，落盘前也会自动纠正到统一格式。
57. 抖音图文描述最后一行的话题现在会过滤掉菜名话题和 #阿叶造新菜，并优先回落到更稳的通用高频美食/活动型话题写法，避免无意义的窄词标签。
58. 新增 tools/select_publish_images.py，可把某个 output 子目录里的多版本图片按首图、图解页和封面分组后投喂给豆包做图片评审；每页会按对应维度打分并把最佳图移动或复制到 publish 文件夹，同时生成 JSON 和 TXT 评分报告。
59. 图片评审工具对“品牌污染”的判定已收紧为只检查实体物体上的品牌、logo、包装、瓶身、锅具印字和食材标签；页面自带的菜名、标题、页尾关注文案与账号名不会再被误判为硬伤。
60. 图解01 page01 prompt 的本地回退链路已改为优先从正式菜谱文本反解析字段，不再从粗略 notes 猜主料、香料、调味料、成败关键和步骤；因此即使文本阶段回退，也不会再把主料写成菜名或把步骤退成通用模板。
61. 当前规则已收紧为：只要发生代码、脚本、配置或文档改动，任务收尾前必须完成版本更新、git 提交和 GitHub 推送；若远端阻塞，需要明确记录原因，避免版本长期只停留在本地工作区。
62. 图解01 首图与图解02到图解06的标题居中现在是 publish 评审硬门槛。tools/select_publish_images.py 会同时把原图和带中心参考线的辅助图交给豆包，再叠加本地像素偏移估算决定是否通过；当前 page01 的容忍度更严。
63. 若某个标题页当前候选全部未通过居中硬门槛，正式模式会按该页已经落盘的文生图 prompt 单张补生新图，再把新图并回候选复评；dry-run 只会在报告里写出“需要补生”，不会真的调图片接口。
64. 若当前目录里的成图已经是 Photoshop 合成后的 JPG，标题补生出来的新图会自动只对这一张补跑一次 PSD 模板再参与复评；如果补生阶段被图片接口 403 阻塞，该页不会进入 publish，但整轮仍会继续处理其它页面，并把阻塞原因写进报告。
65. 主流程现在支持自动 publish 收尾：当 config.env 中 PUBLISH_AUTO_SELECT=1 时，main.py 会在所有图片与 Photoshop 完成后自动创建 publish，并打印 publish_selection_report.json 与 publish_selection_report.txt 路径。
66. 新增 tools/douyin_publish.py，可通过 Playwright 接管已开启远程调试的 Chrome 抖音创作者页，自动上传 publish 里的 01.jpg 到 06.jpg、填写抖音图文标题与描述、单独上传 cover.jpg，并完成“个人观点或臆测”自主声明选择。脚本默认指向 output\20260525_043309_葱香海参酿 这套测试素材，也支持传入别的 output 子目录。
67. config.env 新增 PUBLISH_REQUIRED_TOPICS；一旦配置，抖音图文描述最后 5 个话题里会优先保留这些手动话题，剩余名额再由程序自动补齐。自动补位的话题仍默认过滤菜名标签和 #阿叶造新菜。

## 仓库地址

1. https://github.com/tradingdog/yiyechushi

## 运行命令

```bash
python main.py
```

## 单独生图命令

```bash
python tools/generate_images_from_prompt_folder.py
```

## 单独自动造菜命令

```bash
python tools/generate_auto_dish_idea.py
```

说明：
1. 该脚本只读取 tools/prompt 目录下的 txt 文件。
2. 每个 txt 文件会被当成一个完整 prompt，按 config.env 中的 OPENAI_IMAGE_* 参数逐个生图。
3. 若 tools/prompt 目录里没有 txt 文件，脚本会直接退出，不生成任何图片。
4. 每个 prompt 文件的图片与 revised prompt 都会保存到 output 下对应的“日期时间戳_菜品名”目录。

## 单独只生成发布文案命令

```bash
python tools/generate_images_from_prompt_folder.py --text-only
```

说明：
1. 该模式同样读取 tools/prompt 目录下的 txt 文件，但不会调用图片模型。
2. 每个 txt 文件会生成一份抖音图文标题 txt 和一份抖音图文描述 txt。
3. 图文描述最后一行会自动带 5 个经过过滤的话题标签，不会再写入菜名标签或 #阿叶造新菜。
4. 输出目录为 output 下对应的“日期时间戳_菜品名”目录。
5. 若 config.env 中配置了 PUBLISH_REQUIRED_TOPICS，最终 5 个话题会优先保留这些手动话题，再补剩余标签。

## 单独只生成 txt 命令

```bash
python tools/generate_text_prompts_from_dish_name.py
```

说明：
1. 该脚本固定读取 dish_name.txt 中的菜名和补充说明。
2. 只生成创意 txt、图解01到图解06的文案 txt、图解01到图解06的文生图 txt，以及封面文生图 txt。
3. 不会调用图片模型，不会生成任何 png 图片。
4. 同时会额外生成抖音图文标题和图文描述，并与其它文本结果一起保存到 output 下对应的“日期时间戳_菜品名”目录。
5. 文本结果不会再拆到 output/chuangyi、output/prompt 和 output/publish 子目录。

## 单独套 PSD 模板批量覆盖命令

```bash
python tools/apply_photoshop_template_batch.py 你的图片目录 --dry-run
python tools/apply_photoshop_template_batch.py 你的图片目录
python tools/apply_photoshop_template_batch.py 你的图片目录 --dry-run --local-photoshop-exe "D:\Program Files\Photoshop\App\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
python tools/apply_photoshop_template_batch.py 你的图片目录 --local-photoshop-exe "D:\Program Files\Photoshop\App\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
```

说明：
1. 这是主流程和独立调试共用的本地 Photoshop 工具；当 config.env 中 PHOTOSHOP_AUTO_COMPOSITE=1 时，main.py 会在全部图片落盘后自动调用它。
2. 默认 PSD 模板路径为 tools/photoshop_template/template.psd。
3. 默认智能对象层名称为 input_image；脚本会把指定目录下的 .jpg、.jpeg、.png 逐张替换到这层里。
4. 默认会读取 config.env 里的 PHOTOSHOP_LOCAL_EXE；若你临时想覆盖，也可以继续在命令行传 --local-photoshop-exe。
5. 模板里的混合模式、文字层、纹理层、调整层都会保留；导出结果为平面 JPG，并覆盖回原目录原文件。
6. 若原文件是 png，覆盖后会转成同名 jpg，并删除原 png。
7. 运行前请先手动关闭 Photoshop，避免脚本误碰你当前打开的工作区；工具会先做模板校验，再逐张启动 Photoshop 执行 JSX，处理完成后自动退出。
8. 建议先跑一次 --dry-run；该模式只检查 Photoshop.exe、PSD 模板和智能对象层，不真正覆盖图片。
9. 自然饱和度降低等视觉调整建议直接做进 PSD 模板；工具只负责替换智能对象并导出 JPG。

## Windows 双击补跑 PSD 模板命令

```bash
run_photoshop_template_batch.cmd
run_photoshop_template_batch.cmd output\某个目录 --dry-run
run_photoshop_template_batch.cmd output\某个目录
```

说明：
1. 双击 run_photoshop_template_batch.cmd 后，会先列出 output 下的子目录，让你输入目录名；直接回车会默认处理最新目录。
2. 也可以直接在命令行把某个 output 子目录作为参数传入，不需要手动输入。
3. 底层仍然调用 tools/apply_photoshop_template_batch.py，并沿用 config.env 中的 Photoshop 配置。

## 单独用豆包筛选发布图命令

```bash
python tools/select_publish_images.py output\某个目录 --dry-run
python tools/select_publish_images.py output\某个目录
python tools/select_publish_images.py output\某个目录 --copy
python tools/select_publish_images.py output\某个目录 --title-retry-limit 5
```

说明：
1. 该工具会自动按页面分组某个 output 子目录中的图片：图解01 首图、图解02到图解06、多版本封面会分别比较；封面仍按普通多版本评审逻辑处理。
2. 图解01 首图与图解02到图解06的标题居中是硬门槛。工具会把原图和带中心参考线的辅助图一起交给豆包，再叠加本地像素偏移估算；若当前候选全部未通过，正式模式会按该页原 prompt 单张补生后复评，dry-run 只会在报告里写出“需要补生”。
3. 若目录里的图片已经是 Photoshop 合成后的 JPG，补生出来的新图会自动只对这一张补跑 PSD 模板，再参与复评，避免 JPG 和原始 PNG 混评。
4. 工具会把每页胜出图移动到 output/日期时间戳_菜品名/publish；若传 --copy，则保留原图并复制一份到 publish。
5. 工具会在 publish 目录下额外生成 publish_selection_report.json 和 publish_selection_report.txt 两份评分报告；若补生阶段被图片接口阻塞，对应页面会被标记为“未入 publish”，阻塞原因会直接写进报告。
6. 默认优先读取 DOUBAO_REVIEW_MODEL；若未配置，则回退到 DOUBAO_TEXT_MODEL。标题补生上限默认读取 PUBLISH_TITLE_CENTER_RETRY_LIMIT。

## 抖音图文自动发布命令

```bash
python tools/douyin_publish.py
python tools/douyin_publish.py --dry-run
python tools/douyin_publish.py output\某个目录
python tools/douyin_publish.py output\某个目录 --cdp-url http://127.0.0.1:9222
```

说明：
1. 该脚本会连接已经开启 Chrome 远程调试端口的浏览器，并在已打开标签页里锁定 URL 含 creator.douyin.com 的抖音创作者页面；运行前需确保页面已经登录。
2. 默认测试目录为 output\20260525_043309_葱香海参酿；脚本会自动读取该目录下的抖音图文标题 txt、抖音图文描述 txt，以及 publish 目录里的 01.jpg 到 06.jpg 和 cover.jpg。
3. 图文描述会按人类逐字输入；进入话题阶段后，脚本会按“# + 中文话题 + 空格确认 + 空格分隔”的顺序逐个录入 5 个标签。
4. --dry-run 只校验本地文件、参数和默认素材，不连接 Chrome，适合先做无副作用预检。
5. 若运行中途失败，脚本会尝试把当前页面截图保存为 tools/douyin_publish_last_error.png，便于回看网页实际状态。
6. 如果当前没有可接管的 Chrome 调试端口，脚本会自动拉起一个独立的自动化 Chrome，并使用 tools/chrome_automation_profile 作为独立资料目录，不会直接接管你当前已经打开的普通 Chrome 窗口。
7. 这个独立自动化 Chrome 第一次使用时需要你手动登录一次抖音创作者中心；后续脚本会复用该资料目录，不用每次重新登录。
8. 如果脚本提示当前自动化 Chrome 还没登录，那说明 publish 图片、标题和描述都已经识别成功，真正缺的是这个独立自动化 Chrome 的登录态，而不是本地素材问题。

## 回归检查

```bash
python tools/check_prompt_pollution.py
```

说明：
1. 自动检查图解02到图解06本地兜底文案是否出现固定菜词污染（豆腐/肉沫/黄豌豆）。
2. 自动检查图解02到图解04的 visual_focus 是否出现固定菜词污染。
3. 自动执行主流程相关脚本的 py_compile 校验。

## 输出规则

1. 主流程会在 output 下创建“日期时间戳_菜品名”单次输出目录。
2. 图解01 到图解06文案、图解01 到图解06 prompt、封面 prompt、最终图片、revised prompt、抖音图文标题与抖音图文描述，都会统一保存到该目录。
3. 图解文件名格式为：日期时间戳_菜名_图解序号_后缀。
4. 封面图相关文件名格式为：日期时间戳_菜名封面_后缀。
5. 单独调试生图时，原始 prompt 也会与图片一并落到同一个单次输出目录。
6. 若主流程开启 Photoshop 自动合成，本轮输出目录里的图解图和封面图会在最后一步被覆盖为 JPG。

## 当前主要文件

1. main.py
2. image_generator.py
3. guide_generator.py
4. guide_pages/
5. dish_name.txt
6. 临时调试prompt.txt
7. guanggaoyu.txt
8. config.env
9. 项目规范.md
10. 项目记忆.md
11. 版本记录.md
12. tools/check_prompt_pollution.py
13. tools/generate_images_from_prompt_folder.py
14. tools/generate_text_prompts_from_dish_name.py
15. tools/apply_photoshop_template_batch.py
16. tools/select_publish_images.py
17. run_photoshop_template_batch.cmd
18. tools/photoshop_template/
