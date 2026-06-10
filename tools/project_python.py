from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_venv_python() -> Path | None:
    candidate = repo_root() / ".venv" / "Scripts" / "python.exe"
    return candidate if candidate.is_file() else None


def resolve_project_python() -> str:
    venv_py = project_venv_python()
    if venv_py is not None:
        return str(venv_py)
    return sys.executable


def running_in_project_venv() -> bool:
    venv_py = project_venv_python()
    if venv_py is None:
        return False
    try:
        return Path(sys.executable).resolve() == venv_py.resolve()
    except OSError:
        return Path(sys.executable) == venv_py


def reexec_in_project_venv_if_needed() -> None:
    """若用全局 python 启动面板/脚本，自动切换到项目 .venv。"""
    venv_py = project_venv_python()
    if venv_py is None or running_in_project_venv():
        return
    print(f"[项目] 切换到虚拟环境：{venv_py}", flush=True)
    os.execv(str(venv_py), [str(venv_py), *sys.argv])
