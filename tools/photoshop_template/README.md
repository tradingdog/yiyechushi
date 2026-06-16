# Photoshop 模板目录

把你的 PSD 模板放在这个目录下，默认文件名使用 template.psd。

首版批量工具默认读取：

1. PSD 模板路径：tools/photoshop_template/template.psd
2. 承接输入图片的智能对象层名称：input_image

请按下面方式准备模板：

1. 在 PSD 里放一层智能对象，名称改成 input_image。
2. 把这层智能对象拖到你希望的最底层位置。工具不会再帮你改层级，而是直接替换这层的内容。
3. 你想保留的混合模式、纹理层、光效层、文字层，都放在这个智能对象层上方。
4. 如果你要“自然饱和度降低 10%”，请在模板里提前做一层自然饱和度调整层，并把数值设成 -10。首版工具会保留模板里的所有混合层与调整层，然后直接导出平面 JPG。
5. 若你不想用默认图层名 input_image，可在 config.env 里改 PHOTOSHOP_TEMPLATE_SMART_OBJECT_LAYER，或运行脚本时传 --smart-layer。
6. **生图尺寸须与智能对象画布一致**（默认 1024×1536，见 `PHOTOSHOP_TEMPLATE_INPUT_SIZE`）。若输入图更大，工具会先缩放到模板尺寸再合成，避免发布图被裁切。

推荐测试步骤：

1. 先放好 template.psd。
2. 若你已经在 config.env 里配好 PHOTOSHOP_LOCAL_EXE，请先关闭 Photoshop，再运行一次 dry-run：python tools/apply_photoshop_template_batch.py 你的图片目录 --dry-run
3. 若你临时想覆盖 Photoshop 路径，再运行：python tools/apply_photoshop_template_batch.py 你的图片目录 --dry-run --local-photoshop-exe "D:\Program Files\Photoshop\App\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
4. 确认模板图层名无误后，再正式跑批量处理。

说明：

1. 主流程在 PHOTOSHOP_AUTO_COMPOSITE=1 时，也会自动调用同一套本地 Photoshop 合成逻辑。
2. 当前机器实测可用的本地调用方式是把 JSX 文件路径直接传给 Photoshop.exe；不依赖 Photoshop.exe 的 -r 参数。
3. 本地模式会逐张打开模板、替换 input_image、导出 JPG，再覆盖回原目录原图；若原图是 png，会转成同名 jpg 并删除原 png。