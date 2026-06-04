from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO


ROOT_DIR = Path(__file__).resolve().parent
LOG_ROOT_DIR = ROOT_DIR / "logs"

_INITIALIZED_LOG_PATH: Path | None = None
_LOG_HANDLE: TextIO | None = None
_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr


class TeeTextStream:
    def __init__(self, console_stream: TextIO, log_stream: TextIO) -> None:
        self.console_stream = console_stream
        self.log_stream = log_stream

    def write(self, text: str) -> int:
        if not text:
            return 0
        if not isinstance(text, str):
            text = str(text)
        self.console_stream.write(text)
        self.log_stream.write(text)
        return len(text)

    def flush(self) -> None:
        self.console_stream.flush()
        self.log_stream.flush()

    def isatty(self) -> bool:
        isatty = getattr(self.console_stream, "isatty", None)
        if callable(isatty):
            return bool(isatty())
        return False

    def fileno(self) -> int:
        return self.console_stream.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self.console_stream, "encoding", "utf-8")


def sanitize_log_label(script_path: Path) -> str:
    try:
        relative_path = script_path.resolve().relative_to(ROOT_DIR)
    except ValueError:
        relative_path = script_path.resolve()
    raw_label = "_".join(relative_path.with_suffix("").parts)
    normalized = re.sub(r"[^\w.-]+", "_", raw_label, flags=re.UNICODE).strip("._")
    return normalized or "script"


def build_log_path(script_path: str | Path, started_at: datetime | None = None) -> Path:
    script_file = Path(script_path)
    started_at = started_at or datetime.now()
    LOG_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{started_at.strftime('%Y%m%d_%H%M%S')}_{sanitize_log_label(script_file)}_{os.getpid()}.log"
    return LOG_ROOT_DIR / file_name


def shutdown_script_logging() -> None:
    global _LOG_HANDLE
    if _LOG_HANDLE is None:
        return

    current_stdout = sys.stdout
    current_stderr = sys.stderr
    sys.stdout = _ORIGINAL_STDOUT
    sys.stderr = _ORIGINAL_STDERR

    try:
        if current_stdout is not _ORIGINAL_STDOUT:
            current_stdout.flush()
        if current_stderr is not _ORIGINAL_STDERR and current_stderr is not current_stdout:
            current_stderr.flush()
    finally:
        _LOG_HANDLE.close()
        _LOG_HANDLE = None


def setup_script_logging(script_path: str | Path, argv: list[str] | None = None) -> Path:
    global _INITIALIZED_LOG_PATH, _LOG_HANDLE
    if _INITIALIZED_LOG_PATH is not None:
        return _INITIALIZED_LOG_PATH

    started_at = datetime.now()
    log_path = build_log_path(script_path, started_at=started_at)
    log_handle = log_path.open("a", encoding="utf-8", buffering=1)

    sys.stdout = TeeTextStream(_ORIGINAL_STDOUT, log_handle)
    sys.stderr = TeeTextStream(_ORIGINAL_STDERR, log_handle)

    _INITIALIZED_LOG_PATH = log_path
    _LOG_HANDLE = log_handle
    atexit.register(shutdown_script_logging)

    command_line = subprocess.list2cmdline(argv or sys.argv)
    print(f"[日志] 已写入：{log_path}")
    print(f"[日志] 启动时间：{started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[日志] 启动命令：{command_line}")
    return log_path
