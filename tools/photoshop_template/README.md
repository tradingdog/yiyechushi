# Photoshop 模板目录

把你的 PSD 模板放在这个目录下。

## 命名规则

程序按**生图尺寸**自动选择模板，文件名格式：

`template_{宽}x{高}.psd`

例如：

- `template_1024x1536.psd` — 2:3 竖版 1K（也可用默认 `template.psd` 代替）
- `template_1376x2064.psd` — 2:3 竖版 2K
- `template_2336x3504.psd` — 2:3 竖版 4K

## 智能对象要求

1. 在 PSD 里放一层智能对象，名称改成 **input_image**。
2. **input_image 画布尺寸必须与生图尺寸完全一致**（宽×高像素）。
3. 把这层智能对象拖到你希望的最底层位置。
4. 混合模式、纹理层、光效层、文字层放在 input_image 上方。

## 需要制作的全部尺寸（8 种比例 × 3 档，去重后 24 个文件）

| 文件名 | 比例 | 档位 | 画布尺寸 |
| --- | --- | --- | --- |
| template_672x1008.psd | 2:3 | 1K* | 672×1008 |
| template_1024x1536.psd | 2:3 | 1K | 1024×1536（可用 template.psd） |
| template_1376x2064.psd | 2:3 | 2K | 1376×2064 |
| template_2336x3504.psd | 2:3 | 4K | 2336×3504 |
| template_768x1024.psd | 3:4 | 1K | 768×1024 |
| template_1536x2048.psd | 3:4 | 2K | 1536×2048 |
| template_2448x3264.psd | 3:4 | 4K | 2448×3264 |
| template_720x1280.psd | 9:16 | 1K | 720×1280 |
| template_1152x2048.psd | 9:16 | 2K | 1152×2048 |
| template_2160x3840.psd | 9:16 | 4K | 2160×3840 |
| template_1024x1024.psd | 1:1 | 1K | 1024×1024 |
| template_2048x2048.psd | 1:1 | 2K | 2048×2048 |
| template_2880x2880.psd | 1:1 | 4K | 2880×2880 |
| template_832x1040.psd | 4:5 | 1K | 832×1040 |
| template_1664x2080.psd | 4:5 | 2K | 1664×2080 |
| template_2560x3200.psd | 4:5 | 4K | 2560×3200 |
| template_1008x672.psd | 3:2 | 1K | 1008×672 |
| template_2064x1376.psd | 3:2 | 2K | 2064×1376 |
| template_3504x2336.psd | 3:2 | 4K | 3504×2336 |
| template_1280x720.psd | 16:9 | 1K | 1280×720 |
| template_2048x1152.psd | 16:9 | 2K | 2048×1152 |
| template_3840x2160.psd | 16:9 | 4K | 3840×2160 |
| template_1024x768.psd | 4:3 | 1K | 1024×768 |
| template_2048x1536.psd | 4:3 | 2K | 2048×1536 |
| template_3264x2448.psd | 4:3 | 4K | 3264×2448 |

\* 2:3 的 1K 默认使用 **1024×1536**（与项目历史一致），不必单独做 672×1008。

建议优先制作常用的 **2:3** 三档：`template.psd`（1024×1536）、`template_1376x2064.psd`、`template_2336x3504.psd`。

## 测试步骤

1. 放好对应尺寸的 PSD。
2. 运行 dry-run：`python tools/apply_photoshop_template_batch.py 你的图片目录 --dry-run`
3. 确认模板图层名无误后，再正式跑批量处理。
