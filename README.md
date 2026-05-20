# 一页厨师图片生成器

这是第一阶段的最小可运行版本，用于读取本地 .env 中的 OpenAI 配置，调用 gpt-image-2 生成图片，并将结果保存到 image 目录。

## 当前版本

v0.02

## 使用方式

1. 在 .env 中配置 OPENAI_API_KEY。
2. 按需修改 临时调试prompt.txt 中的 prompt。
3. 运行 main.py。

## 当前状态

1. 已实测运行 main.py，并成功生成首张图片。
2. 本地 git 仓库已初始化并完成首个中文提交。
3. GitHub 远程仓库创建仍需先完成 gh 登录。

## 运行命令

```bash
python main.py
```

## 输出规则

1. 图片保存到 image 目录。
2. 文件名格式为：日期时间戳_菜名_序号.png。
3. 若接口返回 revised prompt，会一并保存到 image 目录。

## 当前主要文件

1. main.py
2. image_generator.py
3. 临时调试prompt.txt
4. 项目规范.md
5. 项目记忆.md
6. 版本记录.md
