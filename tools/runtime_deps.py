from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from tools.project_python import reexec_in_project_venv_if_needed, resolve_project_python

ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"

# import 名 -> pip 包名（为 None 时与 import 名相同）
RUNTIME_MODULES: tuple[tuple[str, str | None], ...] = (
    ("playwright", None),
    ("pyautogui", None),
    ("cv2", "opencv-python-headless"),
    ("numpy", None),
    ("pyperclip", None),
    ("PIL", "Pillow"),
)


def missing_runtime_modules() -> list[str]:
    missing_packages: list[str] = []
    for module_name, package_name in RUNTIME_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing_packages.append(package_name or module_name)
    return missing_packages


def ensure_project_runtime_dependencies() -> None:
    reexec_in_project_venv_if_needed()
    missing_packages = missing_runtime_modules()
    if not missing_packages:
        return

    if not REQUIREMENTS_FILE.exists():
        raise SystemExit(f"缺少依赖且未找到 requirements.txt：{REQUIREMENTS_FILE}")

    print(
        "检测到当前 Python 环境缺少发布脚本依赖："
        + "、".join(sorted(set(missing_packages)))
    )
    python_exe = resolve_project_python()
    print(f"正在使用 {python_exe} 安装 {REQUIREMENTS_FILE.name} ...")

    subprocess.check_call(
        [python_exe, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
    )

    still_missing = missing_runtime_modules()
    if still_missing:
        raise SystemExit(
            "自动安装后仍缺少依赖："
            + "、".join(sorted(set(still_missing)))
            + f"\n请手动执行：{python_exe} -m pip install -r {REQUIREMENTS_FILE}"
        )

    print("发布脚本依赖已安装完成。")
