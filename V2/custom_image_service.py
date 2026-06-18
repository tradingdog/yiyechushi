from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from v2_core import (
    build_openai_image_client,
    generate_images_by_prompt,
    generate_images_from_references,
    get_image_settings,
    get_timestamp,
    save_generated_images,
    sync_v2_openai_image_settings,
)

ROOT_DIR = Path(__file__).resolve().parent
CUSTOM_IMAGE_DIR = ROOT_DIR / "custom_image_gen"
CUSTOM_IMAGE_IMAGES_DIR = CUSTOM_IMAGE_DIR / "images"
CUSTOM_IMAGE_REFS_DIR = CUSTOM_IMAGE_DIR / "refs"
CUSTOM_IMAGE_HISTORY_FILE = CUSTOM_IMAGE_DIR / "history.json"

CUSTOM_IMAGE_LOCK = threading.Lock()
CUSTOM_IMAGE_RUNNING = False
CUSTOM_IMAGE_ERROR = ""
CUSTOM_IMAGE_JOBS: list[dict[str, Any]] = []

IMAGE_NAME_PATTERN = re.compile(r"^image_\d+$", re.IGNORECASE)
REF_NAME_PATTERN = re.compile(r"^ref_\d+$", re.IGNORECASE)


def ensure_custom_image_dirs() -> None:
    CUSTOM_IMAGE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_IMAGE_REFS_DIR.mkdir(parents=True, exist_ok=True)


def is_custom_image_path(target: Path) -> bool:
    root = CUSTOM_IMAGE_DIR.resolve()
    resolved = target.resolve()
    return resolved == root or root in resolved.parents


def load_custom_image_history() -> list[dict[str, Any]]:
    ensure_custom_image_dirs()
    if not CUSTOM_IMAGE_HISTORY_FILE.exists():
        return []
    try:
        payload = json.loads(CUSTOM_IMAGE_HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    jobs = payload.get("jobs")
    return jobs if isinstance(jobs, list) else []


def save_custom_image_history(jobs: list[dict[str, Any]]) -> None:
    ensure_custom_image_dirs()
    CUSTOM_IMAGE_HISTORY_FILE.write_text(
        json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def init_custom_image_service() -> None:
    global CUSTOM_IMAGE_JOBS
    with CUSTOM_IMAGE_LOCK:
        CUSTOM_IMAGE_JOBS = load_custom_image_history()


def _new_job_id() -> str:
    return f"{get_timestamp()}_{uuid.uuid4().hex[:8]}"


def _build_settings(image_count: int) -> dict[str, Any]:
    sync_v2_openai_image_settings()
    settings = get_image_settings()
    settings["image_count"] = max(1, min(4, int(image_count)))
    return settings


def _append_job_locked(job: dict[str, Any]) -> None:
    CUSTOM_IMAGE_JOBS.insert(0, job)
    if len(CUSTOM_IMAGE_JOBS) > 200:
        del CUSTOM_IMAGE_JOBS[200:]
    save_custom_image_history(CUSTOM_IMAGE_JOBS)


def _update_job_locked(job_id: str, **updates: Any) -> None:
    for index, job in enumerate(CUSTOM_IMAGE_JOBS):
        if job.get("id") == job_id:
            CUSTOM_IMAGE_JOBS[index] = {**job, **updates}
            save_custom_image_history(CUSTOM_IMAGE_JOBS)
            return


def _mark_slot_done(job_id: str, slot_index: int, image_path: str) -> None:
    with CUSTOM_IMAGE_LOCK:
        for job in CUSTOM_IMAGE_JOBS:
            if job.get("id") != job_id:
                continue
            slots = list(job.get("slots") or [])
            if 0 <= slot_index < len(slots):
                slots[slot_index] = {"status": "done", "path": image_path}
                job["slots"] = slots
                save_custom_image_history(CUSTOM_IMAGE_JOBS)
            return


def custom_image_worker(
    *,
    job_id: str,
    prompt: str,
    image_count: int,
    reference_paths: list[str],
) -> None:
    global CUSTOM_IMAGE_RUNNING, CUSTOM_IMAGE_ERROR
    try:
        sync_v2_openai_image_settings()
        client = build_openai_image_client()
        settings = _build_settings(image_count)
        timestamp = get_timestamp()
        output_dir = CUSTOM_IMAGE_IMAGES_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        ref_paths = [str(path) for path in reference_paths if Path(path).exists()]
        accumulated: list[dict[str, str]] = []
        max_attempts = 3
        attempt = 0
        while len(accumulated) < image_count and attempt < max_attempts:
            attempt += 1
            remaining = image_count - len(accumulated)
            if ref_paths:
                batch_items = generate_images_from_references(
                    client=client,
                    prompt_text=prompt,
                    reference_paths=ref_paths,
                    settings={**settings, "image_count": remaining},
                )
            else:
                batch_items = generate_images_by_prompt(
                    client=client,
                    prompt_text=prompt,
                    settings={**settings, "image_count": remaining},
                )
            if not batch_items:
                continue
            take_count = min(len(batch_items), remaining)
            for offset in range(take_count):
                slot_index = len(accumulated)
                saved = save_generated_images(
                    batch_items[offset : offset + 1],
                    output_dir=output_dir,
                    timestamp=timestamp,
                    dish_name="custom",
                    name_suffix=f"{slot_index + 1:02d}",
                )
                if saved:
                    accumulated.append({"path": saved[0]})
                    _mark_slot_done(job_id, slot_index, saved[0])

        if len(accumulated) < image_count:
            raise RuntimeError(f"仅生成 {len(accumulated)}/{image_count} 张图片。")

        with CUSTOM_IMAGE_LOCK:
            _update_job_locked(
                job_id,
                status="done",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
    except Exception as exc:  # noqa: BLE001
        CUSTOM_IMAGE_ERROR = str(exc)
        with CUSTOM_IMAGE_LOCK:
            _update_job_locked(
                job_id,
                status="failed",
                error=str(exc),
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        traceback.print_exc()
    finally:
        CUSTOM_IMAGE_RUNNING = False


def start_custom_image_generation(*, prompt: str, image_count: int, reference_files: list[tuple[str, bytes]]) -> dict[str, Any]:
    global CUSTOM_IMAGE_RUNNING, CUSTOM_IMAGE_ERROR
    prompt_text = prompt.strip()
    if not prompt_text:
        raise ValueError("生图提示词不能为空。")
    count = max(1, min(4, int(image_count)))

    with CUSTOM_IMAGE_LOCK:
        if CUSTOM_IMAGE_RUNNING:
            raise RuntimeError("已有自定义生图任务在执行，请等待完成后再试。")
        CUSTOM_IMAGE_RUNNING = True
        CUSTOM_IMAGE_ERROR = ""

    job_id = _new_job_id()
    ref_dir = CUSTOM_IMAGE_REFS_DIR / job_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    saved_refs: list[str] = []
    for index, (filename, content) in enumerate(reference_files, start=1):
        safe_name = Path(filename).name or f"ref_{index:02d}.png"
        ref_path = ref_dir / safe_name
        ref_path.write_bytes(content)
        saved_refs.append(str(ref_path.resolve()))

    slots = [{"status": "pending", "path": ""} for _ in range(count)]
    job = {
        "id": job_id,
        "prompt": prompt_text,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "requested_count": count,
        "status": "running",
        "refs": saved_refs,
        "slots": slots,
        "error": "",
        "finished_at": "",
    }
    with CUSTOM_IMAGE_LOCK:
        _append_job_locked(job)

    worker = threading.Thread(
        target=custom_image_worker,
        kwargs={
            "job_id": job_id,
            "prompt": prompt_text,
            "image_count": count,
            "reference_paths": saved_refs,
        },
        daemon=True,
    )
    worker.start()
    return {"ok": True, "job_id": job_id}


def custom_image_status_snapshot() -> dict[str, Any]:
    with CUSTOM_IMAGE_LOCK:
        jobs = [dict(job) for job in CUSTOM_IMAGE_JOBS]
        running = CUSTOM_IMAGE_RUNNING
        error = CUSTOM_IMAGE_ERROR
    tiles: list[dict[str, Any]] = []
    for job in jobs:
        for slot_index, slot in enumerate(job.get("slots") or []):
            tiles.append(
                {
                    "job_id": job.get("id", ""),
                    "slot_index": slot_index,
                    "status": slot.get("status", "pending"),
                    "path": slot.get("path", ""),
                    "prompt": job.get("prompt", ""),
                    "created_at": job.get("created_at", ""),
                    "job_status": job.get("status", ""),
                }
            )
    current_job = next((job for job in jobs if job.get("status") == "running"), None)
    return {
        "running": running,
        "error": error,
        "current_job": current_job or {},
        "jobs": jobs,
        "tiles": tiles,
        "images_dir": str(CUSTOM_IMAGE_IMAGES_DIR.resolve()),
    }


def parse_multipart_form(body: bytes, content_type: str) -> dict[str, Any]:
    if "boundary=" not in content_type:
        raise ValueError("缺少 multipart boundary。")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary
    end = delimiter + b"--"
    parts = body.split(delimiter)
    fields: dict[str, str] = {}
    files: list[tuple[str, str, bytes]] = []

    for part in parts:
        chunk = part.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        header_blob, _, content = chunk.partition(b"\r\n\r\n")
        if not content:
            continue
        content = content.rstrip(b"\r\n")
        headers = header_blob.decode("utf-8", errors="ignore")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if filename_match is not None:
            filename = filename_match.group(1) or f"{name}.bin"
            files.append((name, filename, content))
        else:
            fields[name] = content.decode("utf-8", errors="ignore")
    return {"fields": fields, "files": files}


def handle_custom_image_generate(body: bytes, content_type: str) -> dict[str, Any]:
    parsed = parse_multipart_form(body, content_type)
    fields = parsed["fields"]
    prompt = str(fields.get("prompt", "")).strip()
    try:
        image_count = int(str(fields.get("count", "1")).strip() or "1")
    except ValueError:
        image_count = 1

    image_gen_payload = {
        key: str(fields.get(key, "")).strip()
        for key in (
            "image_provider",
            "image_aspect_ratio",
            "image_resolution_tier",
            "image_quality",
        )
        if str(fields.get(key, "")).strip()
    }
    if image_gen_payload:
        from image_gen_profile import apply_image_gen_controls

        apply_image_gen_controls(image_gen_payload)

    reference_files: list[tuple[str, bytes]] = []
    for name, filename, content in parsed["files"]:
        if not content:
            continue
        if name in {"prompt", "count"}:
            continue
        if REF_NAME_PATTERN.match(name) or IMAGE_NAME_PATTERN.match(name) or name.startswith("ref"):
            reference_files.append((filename, content))

    return start_custom_image_generation(
        prompt=prompt,
        image_count=image_count,
        reference_files=reference_files,
    )
