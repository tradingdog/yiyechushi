from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import ensure_runtime_config_loaded


DEFAULT_TEMPLATE_FILE = ROOT_DIR / "tools" / "photoshop_template" / "template.psd"
DEFAULT_SMART_OBJECT_LAYER = "input_image"
DEFAULT_TEMPLATE_INPUT_SIZE = "1024x1536"
DEFAULT_JPEG_QUALITY = 12
DEFAULT_JOB_TIMEOUT_SECONDS = 900
SUPPORTED_INPUT_SUFFIXES = {".jpg", ".jpeg", ".png"}
LOCAL_POLL_INTERVAL_SECONDS = 0.5


def parse_template_input_size(size_text: str) -> tuple[int, int]:
    normalized = (size_text or DEFAULT_TEMPLATE_INPUT_SIZE).strip().lower().replace("×", "x")
    if "x" not in normalized:
        raise RuntimeError("PHOTOSHOP_TEMPLATE_INPUT_SIZE 必须是 宽x高，例如 1024x1536。")
    width_text, height_text = normalized.split("x", 1)
    try:
        width = int(width_text.strip())
        height = int(height_text.strip())
    except ValueError as exc:
        raise RuntimeError("PHOTOSHOP_TEMPLATE_INPUT_SIZE 必须是 宽x高，例如 1024x1536。") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError("PHOTOSHOP_TEMPLATE_INPUT_SIZE 的宽高必须大于 0。")
    return width, height


def resolve_template_input_size() -> tuple[int, int]:
    ensure_runtime_config_loaded()
    raw_value = os.getenv("PHOTOSHOP_TEMPLATE_INPUT_SIZE", DEFAULT_TEMPLATE_INPUT_SIZE).strip()
    return parse_template_input_size(raw_value or DEFAULT_TEMPLATE_INPUT_SIZE)


def prepare_image_for_template_input(source_file: Path, target_size: tuple[int, int]) -> tuple[Path, Path | None]:
    """若输入图尺寸与 PSD 智能对象画布不一致，先缩放到目标尺寸再交给 Photoshop。"""
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise RuntimeError("未安装 Pillow，无法为 Photoshop 模板对齐图片尺寸。") from exc

    with Image.open(source_file) as image:
        current_size = image.size
        if current_size == target_size:
            return source_file, None

        resized = image.convert("RGB").resize(target_size, Image.Resampling.LANCZOS)
        temp_path = source_file.with_name(f"{source_file.stem}__ps_fit{source_file.suffix}")
        save_kwargs: dict[str, Any] = {}
        if temp_path.suffix.lower() in {".jpg", ".jpeg"}:
            save_kwargs["quality"] = 95
        resized.save(temp_path, **save_kwargs)
        print(
            f"  输入图 {current_size[0]}x{current_size[1]} 与 PSD 画布 {target_size[0]}x{target_size[1]} 不一致，"
            f"已先缩放再合成。"
        )
        return temp_path, temp_path


LOCAL_PHOTOSHOP_JSX_TEMPLATE = r'''
app.displayDialogs = DialogModes.NO;

function writeResult(status, message, outputPath) {
    var resultFile = new File(__RESULT_PATH__);
    resultFile.encoding = "UTF8";
    resultFile.open("w");
    resultFile.write(status + "\n");
    resultFile.write((message || "") + "\n");
    resultFile.write((outputPath || "") + "\n");
    resultFile.close();
}

function findLayerByName(parent, targetName) {
    for (var i = 0; i < parent.layers.length; i++) {
        var layer = parent.layers[i];
        if (layer.name === targetName) {
            return layer;
        }
        if (layer.typename === "LayerSet") {
            var nested = findLayerByName(parent.layers[i], targetName);
            if (nested) {
                return nested;
            }
        }
    }
    return null;
}

function replaceSmartObjectContents(fileObj) {
    var replaceId = stringIDToTypeID("placedLayerReplaceContents");
    var desc = new ActionDescriptor();
    desc.putPath(charIDToTypeID("null"), fileObj);
    executeAction(replaceId, desc, DialogModes.NO);
}

function closeAllDocumentsWithoutSaving() {
    while (app.documents.length > 0) {
        app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
    }
}

var templateFile = new File(__TEMPLATE_PATH__);
var smartLayerName = __SMART_LAYER_NAME__;
var inputFile = new File(__INPUT_PATH__);
var outputFile = new File(__OUTPUT_PATH__);
var shouldProcessImage = __SHOULD_PROCESS_IMAGE__;
var jpegQuality = __JPEG_QUALITY__;

try {
    if (!templateFile.exists) {
        throw new Error("PSD 模板不存在：" + templateFile.fsName);
    }

    var documentRef = app.open(templateFile);
    var targetLayer = findLayerByName(documentRef, smartLayerName);
    if (!targetLayer) {
        throw new Error("PSD 模板里找不到智能对象层：" + smartLayerName);
    }

    if (shouldProcessImage) {
        if (!inputFile.exists) {
            throw new Error("输入图片不存在：" + inputFile.fsName);
        }
        app.activeDocument = documentRef;
        documentRef.activeLayer = targetLayer;
        replaceSmartObjectContents(inputFile);
        documentRef.flatten();

        var jpegOptions = new JPEGSaveOptions();
        jpegOptions.quality = jpegQuality;
        jpegOptions.embedColorProfile = true;
        documentRef.saveAs(outputFile, jpegOptions, true, Extension.LOWERCASE);

        if (!outputFile.exists) {
            throw new Error("JPG 导出失败：" + outputFile.fsName);
        }
    }

    documentRef.close(SaveOptions.DONOTSAVECHANGES);
    writeResult("OK", "", shouldProcessImage ? outputFile.fsName : "");
} catch (error) {
    try {
        closeAllDocumentsWithoutSaving();
    } catch (closeError) {}
    var errorMessage = error && error.message ? error.message : String(error);
    writeResult("ERROR", errorMessage, "");
}

try {
    app.quit();
} catch (quitError) {}
'''


@dataclass
class LocalPhotoshopSettings:
    input_dir: Path | None
    output_dir: Path | None
    template_file: Path
    smart_object_layer: str
    local_photoshop_exe: Path
    job_timeout_seconds: int
    jpeg_quality: int
    dry_run: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量把指定目录里的图片替换进 PSD 模板，再导出为高质量 JPG 覆盖原图。",
    )
    parser.add_argument("input_dir", nargs="?", help="要处理的图片目录。")
    parser.add_argument("--template-file", help="PSD 模板文件路径；不传时读取 PHOTOSHOP_TEMPLATE_FILE。")
    parser.add_argument("--smart-layer", help="PSD 模板中的智能对象层名称；不传时读取 PHOTOSHOP_TEMPLATE_SMART_OBJECT_LAYER。")
    parser.add_argument("--jpeg-quality", type=int, help="JPG 导出质量；不传时读取 PHOTOSHOP_JPEG_QUALITY。")
    parser.add_argument("--local-photoshop-exe", help="本地 Photoshop.exe 路径；不传时读取 PHOTOSHOP_LOCAL_EXE。")
    parser.add_argument("--job-timeout-seconds", type=int, help="单张图最长等待秒数；不传时读取 PHOTOSHOP_JOB_TIMEOUT_SECONDS。")
    parser.add_argument("--dry-run", action="store_true", help="只校验 Photoshop、PSD 模板和图层，不真正处理图片。")
    return parser.parse_args()


def parse_positive_int(name: str, raw_value: str | int | None, default: int) -> int:
    text = str(raw_value or default).strip() or str(default)
    try:
        number = int(text)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是正整数。") from exc
    if number <= 0:
        raise RuntimeError(f"{name} 必须大于 0。")
    return number


def resolve_path(path_text: str | Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (ROOT_DIR / candidate).resolve()


def resolve_local_photoshop_settings(
    *,
    input_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    template_file: str | Path | None = None,
    smart_object_layer: str | None = None,
    local_photoshop_exe: str | Path | None = None,
    job_timeout_seconds: int | None = None,
    jpeg_quality: int | None = None,
    dry_run: bool = False,
) -> LocalPhotoshopSettings:
    ensure_runtime_config_loaded()

    resolved_input_dir = resolve_path(input_dir) if input_dir else None
    resolved_output_dir = resolve_path(output_dir) if output_dir else None
    resolved_template_file = resolve_path(template_file or os.getenv("PHOTOSHOP_TEMPLATE_FILE") or DEFAULT_TEMPLATE_FILE)
    resolved_smart_layer = (smart_object_layer or os.getenv("PHOTOSHOP_TEMPLATE_SMART_OBJECT_LAYER") or DEFAULT_SMART_OBJECT_LAYER).strip()
    local_photoshop_exe_text = str(local_photoshop_exe or os.getenv("PHOTOSHOP_LOCAL_EXE") or "").strip()
    if not local_photoshop_exe_text:
        raise RuntimeError("缺少必要配置：PHOTOSHOP_LOCAL_EXE。")

    settings = LocalPhotoshopSettings(
        input_dir=resolved_input_dir,
        output_dir=resolved_output_dir,
        template_file=resolved_template_file,
        smart_object_layer=resolved_smart_layer,
        local_photoshop_exe=resolve_path(local_photoshop_exe_text),
        job_timeout_seconds=parse_positive_int(
            name="PHOTOSHOP_JOB_TIMEOUT_SECONDS",
            raw_value=job_timeout_seconds or os.getenv("PHOTOSHOP_JOB_TIMEOUT_SECONDS"),
            default=DEFAULT_JOB_TIMEOUT_SECONDS,
        ),
        jpeg_quality=parse_positive_int(
            name="PHOTOSHOP_JPEG_QUALITY",
            raw_value=jpeg_quality or os.getenv("PHOTOSHOP_JPEG_QUALITY"),
            default=DEFAULT_JPEG_QUALITY,
        ),
        dry_run=dry_run,
    )

    if not settings.smart_object_layer:
        raise RuntimeError("PHOTOSHOP_TEMPLATE_SMART_OBJECT_LAYER 不能为空。")
    if settings.jpeg_quality < 1 or settings.jpeg_quality > 12:
        raise RuntimeError("PHOTOSHOP_JPEG_QUALITY 只支持 1 到 12。")
    if not settings.local_photoshop_exe.exists() or not settings.local_photoshop_exe.is_file():
        raise RuntimeError(f"本地 Photoshop 可执行文件不存在：{settings.local_photoshop_exe}")
    if settings.local_photoshop_exe.name.lower() != "photoshop.exe":
        raise RuntimeError("本地 Photoshop 模式要求传入 Photoshop.exe 的完整路径。")
    if not settings.template_file.exists() or not settings.template_file.is_file():
        raise RuntimeError(f"PSD 模板不存在：{settings.template_file}")
    if settings.template_file.suffix.lower() != ".psd":
        raise RuntimeError("PSD 模板文件必须是 .psd。")
    if settings.input_dir is not None and (not settings.input_dir.exists() or not settings.input_dir.is_dir()):
        raise RuntimeError(f"图片目录不存在：{settings.input_dir}")

    return settings


def list_supported_images(input_dir: Path) -> list[Path]:
    return sorted(
        [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES],
        key=lambda item: item.name.lower(),
    )


def resolve_local_output_path(source_file: Path) -> Path:
    if source_file.suffix.lower() == ".jpg":
        return source_file
    return source_file.with_suffix(".jpg")


def write_jpeg_to_target_path(target_file: Path, rendered_jpeg_file: Path) -> Path:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    temp_output_path = target_file.with_name(f"{target_file.stem}__ps_tmp.jpg")
    temp_output_path.write_bytes(rendered_jpeg_file.read_bytes())
    temp_output_path.replace(target_file)
    return target_file


def overwrite_local_file_with_jpeg(source_file: Path, rendered_jpeg_file: Path) -> Path:
    final_output_path = resolve_local_output_path(source_file)
    result_path = write_jpeg_to_target_path(final_output_path, rendered_jpeg_file)
    if source_file != final_output_path and source_file.exists():
        source_file.unlink()
    return result_path


def is_local_photoshop_running() -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Photoshop.exe"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "Photoshop.exe" in result.stdout


def close_running_local_photoshop(*, wait_seconds: int = 30) -> bool:
    """若 Photoshop 已在运行则强制结束，确保批处理可独占启动。"""
    if not is_local_photoshop_running():
        return False

    print("检测到 Photoshop 已在运行，正在关闭以便执行模板合成…")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", "Photoshop.exe"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        raise RuntimeError("当前系统暂不支持自动关闭 Photoshop，请先手动关闭后再运行。")

    deadline = time.time() + max(5, wait_seconds)
    while time.time() < deadline:
        if not is_local_photoshop_running():
            print("Photoshop 已关闭。")
            return True
        time.sleep(0.5)

    raise RuntimeError("无法关闭正在运行的 Photoshop，请手动结束 Photoshop.exe 后重试。")


def build_local_photoshop_jsx(
    *,
    template_file: Path,
    smart_layer_name: str,
    result_file: Path,
    input_file: Path | None,
    output_file: Path,
    jpeg_quality: int,
    should_process_image: bool,
) -> str:
    script = LOCAL_PHOTOSHOP_JSX_TEMPLATE
    replacements = {
        "__RESULT_PATH__": json.dumps(result_file.as_posix()),
        "__TEMPLATE_PATH__": json.dumps(template_file.as_posix()),
        "__SMART_LAYER_NAME__": json.dumps(smart_layer_name),
        "__INPUT_PATH__": json.dumps((input_file or output_file).as_posix()),
        "__OUTPUT_PATH__": json.dumps(output_file.as_posix()),
        "__JPEG_QUALITY__": str(jpeg_quality),
        "__SHOULD_PROCESS_IMAGE__": "true" if should_process_image else "false",
    }
    for placeholder, value in replacements.items():
        script = script.replace(placeholder, value)
    return script


def terminate_local_photoshop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def parse_local_photoshop_result(result_file: Path) -> tuple[str, str, str]:
    if not result_file.exists():
        return "ERROR", "本地 Photoshop 脚本未生成结果文件，可能脚本未执行或被弹窗阻塞。", ""
    lines = result_file.read_text(encoding="utf-8", errors="replace").splitlines()
    status = lines[0].strip().upper() if lines else "ERROR"
    message = lines[1].strip() if len(lines) > 1 else ""
    output_path = lines[2].strip() if len(lines) > 2 else ""
    return status, message, output_path


def run_local_photoshop_job(
    settings: LocalPhotoshopSettings,
    *,
    input_file: Path | None,
    output_file: Path,
    should_process_image: bool,
    output_target: Path | None = None,
) -> Path | None:
    close_running_local_photoshop()
    with tempfile.TemporaryDirectory(prefix="yiye_ps_local_") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        result_file = temp_dir / "result.txt"
        jsx_file = temp_dir / "run.jsx"
        jsx_file.write_text(
            build_local_photoshop_jsx(
                template_file=settings.template_file,
                smart_layer_name=settings.smart_object_layer,
                result_file=result_file,
                input_file=input_file,
                output_file=output_file,
                jpeg_quality=settings.jpeg_quality,
                should_process_image=should_process_image,
            ),
            encoding="utf-8",
        )

        process = subprocess.Popen(
            [str(settings.local_photoshop_exe), str(jsx_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + settings.job_timeout_seconds
        while time.time() < deadline:
            if result_file.exists():
                break
            if process.poll() is not None and not result_file.exists():
                break
            time.sleep(LOCAL_POLL_INTERVAL_SECONDS)

        status, message, rendered_output_path = parse_local_photoshop_result(result_file)
        terminate_local_photoshop_process(process)
        if status != "OK":
            raise RuntimeError(f"本地 Photoshop 执行失败：{message}")
        if not should_process_image:
            return None

        rendered_file = Path(rendered_output_path) if rendered_output_path else output_file
        if not rendered_file.exists():
            raise RuntimeError("本地 Photoshop 已返回成功，但没有找到导出的 JPG 文件。")
        if output_target is not None:
            return write_jpeg_to_target_path(output_target, rendered_file)
        return overwrite_local_file_with_jpeg(source_file=input_file or output_file, rendered_jpeg_file=rendered_file)


def validate_local_photoshop_setup(
    *,
    template_file: str | Path | None = None,
    smart_object_layer: str | None = None,
    local_photoshop_exe: str | Path | None = None,
    job_timeout_seconds: int | None = None,
    jpeg_quality: int | None = None,
) -> None:
    settings = resolve_local_photoshop_settings(
        input_dir=None,
        template_file=template_file,
        smart_object_layer=smart_object_layer,
        local_photoshop_exe=local_photoshop_exe,
        job_timeout_seconds=job_timeout_seconds,
        jpeg_quality=jpeg_quality,
        dry_run=True,
    )
    close_running_local_photoshop()
    run_local_photoshop_job(
        settings,
        input_file=None,
        output_file=Path(tempfile.gettempdir()) / f"yiye_ps_dry_run_{int(time.time())}.jpg",
        should_process_image=False,
    )


def process_single_image_local(image_file: Path, index: int, total: int, settings: LocalPhotoshopSettings) -> Path:
    print(f"[{index}/{total}] 本地 Photoshop 替换模板：{image_file.name}")
    output_target: Path | None = None
    if settings.output_dir is not None:
        output_target = settings.output_dir / resolve_local_output_path(image_file).name
    template_input_size = resolve_template_input_size()
    prepared_input, temp_input = prepare_image_for_template_input(image_file, template_input_size)
    try:
        with tempfile.TemporaryDirectory(prefix="yiye_ps_render_") as temp_dir_text:
            output_file = Path(temp_dir_text) / f"{image_file.stem}.jpg"
            final_output_path = run_local_photoshop_job(
                settings,
                input_file=prepared_input,
                output_file=output_file,
                should_process_image=True,
                output_target=output_target,
            )
    finally:
        if temp_input is not None and temp_input.exists():
            temp_input.unlink()
    if final_output_path is None:
        raise RuntimeError("本地 Photoshop 没有返回输出文件路径。")
    if settings.output_dir is not None:
        print(f"[{index}/{total}] 已导出到 final：{final_output_path.name}")
    else:
        print(f"[{index}/{total}] 已覆盖本地文件：{final_output_path.name}")
    return final_output_path


def apply_photoshop_template_to_image(
    image_file: str | Path,
    *,
    template_file: str | Path | None = None,
    smart_object_layer: str | None = None,
    local_photoshop_exe: str | Path | None = None,
    job_timeout_seconds: int | None = None,
    jpeg_quality: int | None = None,
) -> str:
    resolved_image_file = resolve_path(image_file)
    if not resolved_image_file.exists() or not resolved_image_file.is_file():
        raise RuntimeError(f"图片文件不存在：{resolved_image_file}")
    if resolved_image_file.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise RuntimeError(f"暂不支持该图片类型：{resolved_image_file.suffix}")

    settings = resolve_local_photoshop_settings(
        input_dir=resolved_image_file.parent,
        template_file=template_file,
        smart_object_layer=smart_object_layer,
        local_photoshop_exe=local_photoshop_exe,
        job_timeout_seconds=job_timeout_seconds,
        jpeg_quality=jpeg_quality,
        dry_run=False,
    )

    print(f"开始单图 Photoshop 模板合成：{resolved_image_file.name}")
    validate_local_photoshop_setup(
        template_file=settings.template_file,
        smart_object_layer=settings.smart_object_layer,
        local_photoshop_exe=settings.local_photoshop_exe,
        job_timeout_seconds=settings.job_timeout_seconds,
        jpeg_quality=settings.jpeg_quality,
    )

    output_path = process_single_image_local(
        image_file=resolved_image_file,
        index=1,
        total=1,
        settings=settings,
    )
    return str(output_path.resolve())


def apply_photoshop_template_batch_to_files(
    image_files: Sequence[str | Path],
    *,
    output_dir: str | Path | None = None,
    template_file: str | Path | None = None,
    smart_object_layer: str | None = None,
    local_photoshop_exe: str | Path | None = None,
    job_timeout_seconds: int | None = None,
    jpeg_quality: int | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    resolved_files = [resolve_path(path) for path in image_files if str(path).strip()]
    if not resolved_files:
        print("没有需要 Photoshop 合成的图片。")
        return {}

    parent_dir = resolved_files[0].parent
    settings = resolve_local_photoshop_settings(
        input_dir=parent_dir,
        output_dir=output_dir,
        template_file=template_file,
        smart_object_layer=smart_object_layer,
        local_photoshop_exe=local_photoshop_exe,
        job_timeout_seconds=job_timeout_seconds,
        jpeg_quality=jpeg_quality,
        dry_run=dry_run,
    )

    input_files = [path for path in resolved_files if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES]
    if not input_files:
        print("指定列表中没有可处理的 .jpg/.jpeg/.png 文件。")
        return {}

    print(f"已指定 {len(input_files)} 个待处理文件，开始校验本地 Photoshop 模板。")
    print(f"当前 PSD 模板：{settings.template_file}")
    if settings.output_dir is not None:
        print(f"当前导出目录：{settings.output_dir}")
    print(f"当前本地 Photoshop：{settings.local_photoshop_exe}")
    print(f"当前 JPG 输出质量：{settings.jpeg_quality}")
    validate_local_photoshop_setup(
        template_file=settings.template_file,
        smart_object_layer=settings.smart_object_layer,
        local_photoshop_exe=settings.local_photoshop_exe,
        job_timeout_seconds=settings.job_timeout_seconds,
        jpeg_quality=settings.jpeg_quality,
    )
    print(f"本地 Photoshop 模板校验通过，已找到智能对象层：{settings.smart_object_layer}")

    if settings.dry_run:
        print("dry-run 模式已完成：本地 Photoshop、PSD 模板和智能对象层都通过校验，未真正处理图片。")
        return {}

    processed_file_map: dict[str, str] = {}
    generated_output_paths: list[Path] = []
    for index, image_file in enumerate(input_files, start=1):
        output_path = process_single_image_local(
            image_file=image_file,
            index=index,
            total=len(input_files),
            settings=settings,
        )
        processed_file_map[str(image_file.resolve())] = str(output_path.resolve())
        generated_output_paths.append(output_path)

    print(f"批量处理完成：成功 {len(generated_output_paths)} 个文件。")
    for output_path in generated_output_paths:
        print(f"- {output_path}")
    return processed_file_map


def apply_photoshop_template_batch_to_dir(
    input_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    template_file: str | Path | None = None,
    smart_object_layer: str | None = None,
    local_photoshop_exe: str | Path | None = None,
    job_timeout_seconds: int | None = None,
    jpeg_quality: int | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    settings = resolve_local_photoshop_settings(
        input_dir=input_dir,
        output_dir=output_dir,
        template_file=template_file,
        smart_object_layer=smart_object_layer,
        local_photoshop_exe=local_photoshop_exe,
        job_timeout_seconds=job_timeout_seconds,
        jpeg_quality=jpeg_quality,
        dry_run=dry_run,
    )
    if settings.input_dir is None:
        raise RuntimeError("缺少要处理的图片目录。")

    input_files = list_supported_images(settings.input_dir)
    if not input_files:
        print("指定目录下没有可处理的图片文件；当前只处理 .jpg/.jpeg/.png。")
        return {}

    return apply_photoshop_template_batch_to_files(
        input_files,
        output_dir=settings.output_dir,
        template_file=settings.template_file,
        smart_object_layer=settings.smart_object_layer,
        local_photoshop_exe=settings.local_photoshop_exe,
        job_timeout_seconds=settings.job_timeout_seconds,
        jpeg_quality=settings.jpeg_quality,
        dry_run=settings.dry_run,
    )


def main() -> int:
    args = parse_args()
    if not args.input_dir:
        print("请传入要处理的图片目录，例如：python tools/apply_photoshop_template_batch.py output\\某个目录")
        return 1

    try:
        apply_photoshop_template_batch_to_dir(
            input_dir=args.input_dir,
            template_file=args.template_file,
            smart_object_layer=args.smart_layer,
            local_photoshop_exe=args.local_photoshop_exe,
            job_timeout_seconds=args.job_timeout_seconds,
            jpeg_quality=args.jpeg_quality,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
