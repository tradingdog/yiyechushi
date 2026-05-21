# 一页厨师图片生成器

这是第一阶段的完整可运行版本。程序会读取 dish_name.txt 中的菜名创意和补充说明，先生成一整张一页菜谱文案，再生成文生图 prompt，最后调用 gpt-image-2 生成竖版 2:3 的一页菜谱图片，并统一保存到 output 目录下。

## 当前版本

v0.11

## 使用方式

1. 在 .env 中配置 OPENAI_API_KEY。
2. 在 dish_name.txt 第一行填写菜名或菜品创意，第二行按需填写补充说明。
3. 如需调整固定关注文案，修改 guanggaoyu.txt。
4. 如需调整固定 VI 风格参考，修改 临时调试prompt.txt。
5. 运行 main.py。

## 当前状态

1. main.py 已切换为完整流程入口，可自动完成“创意菜谱 -> 文生图 prompt -> 最终图片”。
2. 本地 git 仓库已初始化并完成首个中文提交。
3. GitHub 远程仓库已创建并完成首次推送。
4. 关注文案改为从 guanggaoyu.txt 动态注入到 prompt。
5. 当前临时调试模板已固定为截图1风格的爆款海报版式，作为 VI 参考。
6. 当前广告语变量已更新为“一页一道新菜”。
7. 当前主标题强制使用 dish_name.txt 第一行原文，不再由模型改菜名。
8. 当前默认每次生成 2 张高质量图片，便于比选。
9. 当前主图会强制加入“筷子夹起一块主菜”的动作镜头，提升食欲感。
10. 文本接口超时时，程序会自动切换备用模型，仍失败则改用本地模板兜底，不让整条流程中断。
11. 新增头像生成脚本 touxiang_generator.py，可批量生成 10 个头像到 touxiang 目录。

## 仓库地址

1. https://github.com/tradingdog/yiyechushi

## 运行命令

```bash
python main.py
```

## 输出规则

1. 最终图片保存到 output/image 目录。
2. 创意菜谱文本保存到 output/chuangyi 目录。
3. 文生图 prompt 保存到 output/prompt 目录。
4. 最终图片保存到 output/image 目录。
5. 文件名格式为：日期时间戳_菜名_后缀。
6. 若接口返回 revised prompt，会一并保存到 output/prompt 目录。
7. 运行 touxiang_generator.py 会将 10 个头像及对应 prompt 保存到 touxiang 目录。

## 当前主要文件

1. main.py
2. image_generator.py
3. touxiang_generator.py
4. dish_name.txt
5. 临时调试prompt.txt
6. guanggaoyu.txt
7. 项目规范.md
8. 项目记忆.md
9. 版本记录.md
