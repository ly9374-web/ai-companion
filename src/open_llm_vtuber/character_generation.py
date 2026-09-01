"""Background character creation with Replicate and local anime matting."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import numpy as np
import onnxruntime as ort
import yaml
from loguru import logger
from PIL import Image, ImageOps, UnidentifiedImageError

from prompts import prompt_loader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHARACTERS_DIR = PROJECT_ROOT / "characters"
AVATARS_DIR = PROJECT_ROOT / "avatars"
EXPRESSION_TEMPLATE_DIR = PROJECT_ROOT / "expression_algernon"
MODEL_PATH = PROJECT_ROOT / "models" / "isnet-anime.onnx"
MODEL_URL = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
    "isnet-anime.onnx"
)
MODEL_MD5 = "6f184e756bb3bd901c8849220a83e38e"
REPLICATE_MODEL_URL = (
    "https://api.replicate.com/v1/models/openai/gpt-image-2/predictions"
)
REPLICATE_FILES_URL = "https://api.replicate.com/v1/files"
CANVAS_SIZE = (1152, 1536)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
EMOTIONS = (
    "不满",
    "生气",
    "慌乱",
    "害羞",
    "哭泣",
    "惊讶",
    "疑惑",
    "兴奋",
    "愉快",
)
EMOTION_PROMPT_KEYS = {
    "不满": "character_generation.expression_prompts.dissatisfied",
    "生气": "character_generation.expression_prompts.angry",
    "慌乱": "character_generation.expression_prompts.flustered",
    "害羞": "character_generation.expression_prompts.shy",
    "哭泣": "character_generation.expression_prompts.crying",
    "惊讶": "character_generation.expression_prompts.surprised",
    "兴奋": "character_generation.expression_prompts.excited",
    "疑惑": "character_generation.expression_prompts.confused",
    "愉快": "character_generation.expression_prompts.happy",
}
DEFAULT_PERSONA_PROMPT_KEY = "generated_default"


class CharacterGenerationError(RuntimeError):
    """A user-facing character generation error."""


class CharacterGenerationConflict(CharacterGenerationError):
    """A character or job with the requested identity already exists."""


@dataclass
class PlacementProfile:
    subject_height: int
    center_x: int
    bottom_y: int


@dataclass
class CharacterGenerationJob:
    id: str
    owner: str
    character_name: str
    status: str = "queued"
    completed: int = 0
    total: int = len(EMOTIONS)
    current_emotion: str | None = None
    error: str | None = None
    character_filename: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "character_name": self.character_name,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "current_emotion": self.current_emotion,
            "error": self.error,
            "character_filename": self.character_filename,
        }


_INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_SESSION: ort.InferenceSession | None = None
_SESSION_LOCK = threading.Lock()


def normalize_character_name(raw_name: str) -> str:
    if not isinstance(raw_name, str):
        raise CharacterGenerationError("请输入角色名称")
    name = unicodedata.normalize("NFC", raw_name).strip()
    if not name or len(name) > 48:
        raise CharacterGenerationError("角色名称长度需为 1-48 个字符")
    if name in {".", ".."} or name.endswith((".", " ")):
        raise CharacterGenerationError("角色名称格式无效")
    if _INVALID_NAME.search(name):
        raise CharacterGenerationError("角色名称不能包含路径或文件特殊字符")
    if name.casefold() in _WINDOWS_RESERVED:
        raise CharacterGenerationError("该角色名称是系统保留名称")
    return name


def normalize_uploaded_image(data: bytes, label: str) -> bytes:
    if not data:
        raise CharacterGenerationError(f"请上传{label}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise CharacterGenerationError(f"{label}不能超过 20MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_IMAGE_FORMATS:
                raise CharacterGenerationError(f"{label}仅支持 PNG、JPEG 或 WebP")
            width, height = probe.size
            if width < 64 or height < 64:
                raise CharacterGenerationError(f"{label}分辨率过低")
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                raise CharacterGenerationError(f"{label}的单边分辨率不能超过 8192")
            probe.verify()
        with Image.open(io.BytesIO(data)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            output = io.BytesIO()
            normalized.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except CharacterGenerationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CharacterGenerationError(f"{label}不是有效图片") from exc


def _download_isnet_model() -> Path:
    if MODEL_PATH.is_file() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = MODEL_PATH.with_name(f".{MODEL_PATH.name}.{uuid4().hex}.download")
    digest = hashlib.md5(usedforsecurity=False)
    try:
        with httpx.Client(follow_redirects=True, timeout=300.0) as client:
            with client.stream("GET", MODEL_URL) as response:
                response.raise_for_status()
                with temp_path.open("wb") as model_file:
                    for chunk in response.iter_bytes(1024 * 1024):
                        digest.update(chunk)
                        model_file.write(chunk)
        if digest.hexdigest() != MODEL_MD5:
            raise CharacterGenerationError("背景删除模型校验失败")
        os.replace(temp_path, MODEL_PATH)
        return MODEL_PATH
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _get_matting_session() -> ort.InferenceSession:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            model_path = _download_isnet_model()
            _SESSION = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )
    return _SESSION


def _remove_background(image_bytes: bytes) -> Image.Image:
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    session = _get_matting_session()
    resized = image.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    array = np.asarray(resized, dtype=np.float32)
    array /= max(float(np.max(array)), 1e-6)
    means = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
    array = (array - means) / np.ones(3, dtype=np.float32)
    tensor = np.transpose(array, (2, 0, 1))[None, ...].astype(np.float32)
    input_name = session.get_inputs()[0].name
    prediction = session.run(None, {input_name: tensor})[0][:, 0, :, :]
    minimum = float(np.min(prediction))
    maximum = float(np.max(prediction))
    if maximum <= minimum:
        raise CharacterGenerationError("无法从图片中识别人物背景")
    mask_array = np.squeeze((prediction - minimum) / (maximum - minimum))
    mask = Image.fromarray((mask_array * 255).astype(np.uint8), mode="L")
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(mask)
    return image


def _foreground_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A").point(lambda value: 255 if value > 8 else 0)
    bbox = alpha.getbbox()
    if bbox is None:
        raise CharacterGenerationError("抠图后未检测到有效人物")
    return bbox


def _reference_profile(image: Image.Image) -> PlacementProfile:
    left, top, right, bottom = _foreground_bbox(image)
    width, height = image.size
    subject_height = max(1, round((bottom - top) / height * CANVAS_SIZE[1]))
    center_x = round(((left + right) / 2) / width * CANVAS_SIZE[0])
    bottom_y = round(bottom / height * CANVAS_SIZE[1])
    return PlacementProfile(
        subject_height=min(subject_height, round(CANVAS_SIZE[1] * 0.98)),
        center_x=max(0, min(CANVAS_SIZE[0], center_x)),
        bottom_y=max(1, min(CANVAS_SIZE[1], bottom_y)),
    )


def _place_on_canvas(image: Image.Image, profile: PlacementProfile) -> Image.Image:
    bbox = _foreground_bbox(image)
    subject = image.crop(bbox)
    scale = profile.subject_height / max(subject.height, 1)
    target_width = max(1, round(subject.width * scale))
    target_height = profile.subject_height
    max_width = round(CANVAS_SIZE[0] * 0.98)
    if target_width > max_width:
        correction = max_width / target_width
        target_width = max_width
        target_height = max(1, round(target_height * correction))
    subject = subject.resize((target_width, target_height), Image.Resampling.LANCZOS)
    x = round(profile.center_x - target_width / 2)
    y = profile.bottom_y - target_height
    x = max(0, min(CANVAS_SIZE[0] - target_width, x))
    y = max(0, min(CANVAS_SIZE[1] - target_height, y))
    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    canvas.alpha_composite(subject, (x, y))
    return canvas


def _save_png(image: Image.Image, path: Path) -> None:
    image.save(path, format="PNG", optimize=True)


def _replicate_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("detail", "error", "title"):
        detail = payload.get(key)
        if detail:
            return str(detail).strip()[:300]
    return ""


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    failure_message: str,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(url, headers=headers)
            if response.status_code not in {429, 502, 503, 504} or attempt == 2:
                return response
        except httpx.TransportError as exc:
            last_error = exc
            if attempt == 2:
                break
        await asyncio.sleep(2**attempt)
    raise CharacterGenerationError(failure_message) from last_error


async def _upload_replicate_reference(
    client: httpx.AsyncClient,
    token: str,
    image_bytes: bytes,
) -> tuple[str, str]:
    try:
        response = await client.post(
            REPLICATE_FILES_URL,
            headers={"Authorization": f"Token {token}"},
            files={"content": ("reference.png", image_bytes, "image/png")},
        )
    except httpx.TransportError as exc:
        raise CharacterGenerationError(
            "上传参考图到 Replicate 时连接中断，请检查网络后重试"
        ) from exc
    if response.status_code in (401, 403):
        raise CharacterGenerationError("Replicate Key 无效或无权限")
    if response.status_code >= 400:
        detail = _replicate_error_detail(response)
        suffix = f"：{detail}" if detail else ""
        raise CharacterGenerationError(
            f"Replicate 参考图上传失败（HTTP {response.status_code}）{suffix}"
        )
    try:
        uploaded = response.json()
    except ValueError as exc:
        raise CharacterGenerationError("Replicate 未返回有效的参考图上传结果") from exc
    file_id = uploaded.get("id") if isinstance(uploaded, dict) else None
    file_url = (uploaded.get("urls") or {}).get("get") if isinstance(uploaded, dict) else None
    if not isinstance(file_id, str) or not isinstance(file_url, str):
        raise CharacterGenerationError("Replicate 未返回可用的参考图文件地址")
    return file_id, file_url


async def _delete_replicate_reference(
    client: httpx.AsyncClient,
    token: str,
    file_id: str,
) -> None:
    try:
        response = await client.delete(
            f"{REPLICATE_FILES_URL}/{file_id}",
            headers={"Authorization": f"Token {token}"},
        )
        if response.status_code not in {200, 202, 204, 404}:
            logger.warning(
                "Replicate 临时参考图清理失败：file_id={}, status={}",
                file_id,
                response.status_code,
            )
    except httpx.TransportError:
        logger.warning("Replicate 临时参考图清理时网络中断：file_id={}", file_id)


async def _run_replicate(
    client: httpx.AsyncClient,
    token: str,
    reference_file_url: str,
    prompt: str,
) -> bytes:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Cancel-After": "20m",
    }
    try:
        response = await client.post(
            REPLICATE_MODEL_URL,
            headers=headers,
            json={
                "input": {
                    "prompt": prompt,
                    "quality": "high",
                    "background": "opaque",
                    "moderation": "auto",
                    "aspect_ratio": "2:3",
                    "input_images": [reference_file_url],
                    "output_format": "jpeg",
                    "number_of_images": 1,
                    "output_compression": 90,
                }
            },
        )
    except httpx.TransportError as exc:
        raise CharacterGenerationError(
            "连接 Replicate 创建生成任务时中断；为避免重复计费，请先到 Replicate 控制台确认后再重试"
        ) from exc
    if response.status_code in (401, 403):
        raise CharacterGenerationError("Replicate Key 无效或无权限")
    if response.status_code >= 400:
        detail = _replicate_error_detail(response)
        suffix = f"：{detail}" if detail else ""
        raise CharacterGenerationError(
            f"Replicate 创建生成任务失败（HTTP {response.status_code}）{suffix}"
        )
    try:
        prediction = response.json()
    except ValueError as exc:
        raise CharacterGenerationError("Replicate 未返回有效的生成任务") from exc
    deadline = time.monotonic() + 20 * 60
    while prediction.get("status") not in {"succeeded", "failed", "canceled"}:
        if time.monotonic() >= deadline:
            raise CharacterGenerationError("Replicate 图片生成超时")
        get_url = (prediction.get("urls") or {}).get("get")
        if not isinstance(get_url, str):
            raise CharacterGenerationError("Replicate 未返回可查询的生成任务")
        await asyncio.sleep(2)
        poll_response = await _get_with_retries(
            client,
            get_url,
            headers={"Authorization": f"Bearer {token}"},
            failure_message="查询 Replicate 生成进度时网络持续中断，请稍后重试",
        )
        if poll_response.status_code >= 400:
            raise CharacterGenerationError(
                f"查询 Replicate 生成进度失败（HTTP {poll_response.status_code}）"
            )
        try:
            prediction = poll_response.json()
        except ValueError as exc:
            raise CharacterGenerationError("Replicate 返回了无效的生成进度") from exc

    if prediction.get("status") != "succeeded":
        detail = prediction.get("error")
        message = str(detail).strip() if detail else "未知错误"
        raise CharacterGenerationError(f"Replicate 图片生成失败：{message[:300]}")
    output = prediction.get("output")
    output_url = output[0] if isinstance(output, list) and output else output
    if not isinstance(output_url, str):
        raise CharacterGenerationError("Replicate 未返回图片文件")
    file_response = await _get_with_retries(
        client,
        output_url,
        failure_message="下载 Replicate 生成图片时网络持续中断，请稍后重试",
    )
    if file_response.status_code >= 400:
        raise CharacterGenerationError(
            f"下载 Replicate 生成图片失败（HTTP {file_response.status_code}）"
        )
    return file_response.content


def _target_paths(character_name: str) -> tuple[Path, Path, Path]:
    return (
        CHARACTERS_DIR / f"{character_name}.yaml",
        AVATARS_DIR / f"{character_name}.png",
        PROJECT_ROOT / f"expression_{character_name}",
    )


def _casefold_collision(parent: Path, desired_name: str) -> bool:
    if not parent.is_dir():
        return False
    folded = desired_name.casefold()
    return any(child.name.casefold() == folded for child in parent.iterdir())


def _configured_character_name_collision(character_name: str) -> bool:
    folded = character_name.casefold()
    config_paths = [PROJECT_ROOT / "conf.yaml"]
    if CHARACTERS_DIR.is_dir():
        config_paths.extend(CHARACTERS_DIR.rglob("*.yaml"))
    for config_path in config_paths:
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        character_config = payload.get("character_config")
        if not isinstance(character_config, dict):
            continue
        configured_name = character_config.get("conf_name")
        if isinstance(configured_name, str) and configured_name.casefold() == folded:
            return True
    return False


def _write_expression_feature(expression_dir: Path, images: dict[str, Image.Image]) -> None:
    frontend_dir = expression_dir / "frontend"
    frontend_dir.mkdir(parents=True)
    shutil.copy2(EXPRESSION_TEMPLATE_DIR / "backend_filter.py", expression_dir)
    shutil.copy2(EXPRESSION_TEMPLATE_DIR / "frontend" / "index.js", frontend_dir)
    for emotion, image in images.items():
        _save_png(image, expression_dir / f"{emotion}.png")
    manifest = {
        "id": "static-expression",
        "version": 3,
        "enabled": True,
        "frontend_entry": "frontend/index.js",
        "default_emotion": "中性",
        "transition": {
            "enabled": True,
            "duration_ms": 160,
            "easing": "ease-out",
        },
        "emotions": {
            emotion: f"{emotion}.png" for emotion in (*EMOTIONS, "中性")
        },
    }
    (expression_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_character_config(path: Path, character_name: str, expression_dir: str) -> None:
    config = {
        "character_config": {
            "conf_name": character_name,
            "conf_uid": character_name,
            "live2d_model_name": "Algernon",
            "character_name": character_name,
            "avatar": f"{character_name}.png",
            "persona_prompt_file": DEFAULT_PERSONA_PROMPT_KEY,
            "welcome_message": f"你好，我是{character_name}。",
            "expression_dir": expression_dir,
        }
    }
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class CharacterGenerationManager:
    """Owns non-blocking, process-local character generation jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, CharacterGenerationJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_owners: set[str] = set()
        self._reserved_names: set[str] = set()
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        *,
        owner: str,
        character_name: str,
        replicate_token: str,
        reference_image: bytes,
        avatar_image: bytes,
    ) -> CharacterGenerationJob:
        name = normalize_character_name(character_name)
        token = replicate_token.strip() if isinstance(replicate_token, str) else ""
        if not token or len(token) > 4096:
            raise CharacterGenerationError("请先填写有效的 Replicate Key")

        config_path, avatar_path, expression_path = _target_paths(name)
        reservation = name.casefold()
        async with self._lock:
            if owner in self._active_owners:
                raise CharacterGenerationConflict("当前账号已有角色正在生成")
            if reservation in self._reserved_names:
                raise CharacterGenerationConflict("同名角色正在生成")
            collisions = (
                _casefold_collision(config_path.parent, config_path.name),
                _casefold_collision(avatar_path.parent, avatar_path.name),
                _casefold_collision(expression_path.parent, expression_path.name),
                _configured_character_name_collision(name),
            )
            if any(collisions):
                raise CharacterGenerationConflict("同名角色或资源已存在")

            job = CharacterGenerationJob(
                id=uuid4().hex,
                owner=owner,
                character_name=name,
            )
            self._jobs[job.id] = job
            self._active_owners.add(owner)
            self._reserved_names.add(reservation)
            task = asyncio.create_task(
                self._execute_job(
                    job,
                    token=token,
                    reference_image=reference_image,
                    avatar_image=avatar_image,
                )
            )
            self._tasks[job.id] = task
            task.add_done_callback(lambda _task, job_id=job.id: self._tasks.pop(job_id, None))
            return job

    async def get_job(self, job_id: str, owner: str) -> CharacterGenerationJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job if job is not None and job.owner == owner else None

    def _update(self, job: CharacterGenerationJob, **updates: Any) -> None:
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = time.time()

    async def _execute_job(
        self,
        job: CharacterGenerationJob,
        *,
        token: str,
        reference_image: bytes,
        avatar_image: bytes,
    ) -> None:
        staging_root = Path(
            tempfile.mkdtemp(prefix=f".character-generation-{job.id}-", dir=PROJECT_ROOT)
        )
        published: list[Path] = []
        try:
            self._update(job, status="preparing")
            neutral_cutout = await asyncio.to_thread(_remove_background, reference_image)
            profile = _reference_profile(neutral_cutout)
            images: dict[str, Image.Image] = {
                "中性": _place_on_canvas(neutral_cutout, profile)
            }
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=300.0, pool=30.0)
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                uploaded_file_id: str | None = None
                try:
                    logger.info(
                        "上传 Replicate 参考图：job_id={}, bytes={}",
                        job.id,
                        len(reference_image),
                    )
                    uploaded_file_id, reference_file_url = await _upload_replicate_reference(
                        client,
                        token,
                        reference_image,
                    )
                    for index, emotion in enumerate(EMOTIONS, start=1):
                        self._update(
                            job,
                            status="generating",
                            current_emotion=emotion,
                            completed=index - 1,
                        )
                        generated = await _run_replicate(
                            client,
                            token,
                            reference_file_url,
                            prompt_loader.load_prompt(EMOTION_PROMPT_KEYS[emotion]),
                        )
                        cutout = await asyncio.to_thread(_remove_background, generated)
                        images[emotion] = _place_on_canvas(cutout, profile)
                        self._update(job, completed=index)
                finally:
                    if uploaded_file_id is not None:
                        await _delete_replicate_reference(client, token, uploaded_file_id)

            self._update(job, status="publishing", current_emotion=None)
            expression_name = f"expression_{job.character_name}"
            staged_expression = staging_root / expression_name
            staged_avatar = staging_root / f"{job.character_name}.png"
            staged_config = staging_root / f"{job.character_name}.yaml"
            await asyncio.to_thread(_write_expression_feature, staged_expression, images)
            staged_avatar.write_bytes(avatar_image)
            _write_character_config(staged_config, job.character_name, expression_name)

            config_path, avatar_path, expression_path = _target_paths(job.character_name)
            CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
            AVATARS_DIR.mkdir(parents=True, exist_ok=True)
            if config_path.exists() or avatar_path.exists() or expression_path.exists():
                raise CharacterGenerationConflict("同名角色或资源已存在")

            os.replace(staged_expression, expression_path)
            published.append(expression_path)
            os.replace(staged_avatar, avatar_path)
            published.append(avatar_path)
            os.replace(staged_config, config_path)
            published.append(config_path)
            self._update(
                job,
                status="succeeded",
                character_filename=config_path.name,
                completed=len(EMOTIONS),
            )
            logger.info("新角色已生成：{}", job.character_name)
        except Exception as exc:
            for path in reversed(published):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    logger.warning("无法回滚未完成的角色资源：{}", path)
            message = str(exc).strip() or "角色生成失败"
            self._update(job, status="failed", error=message[:500])
            logger.exception("角色生成失败：{}", job.character_name)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
            async with self._lock:
                self._active_owners.discard(job.owner)
                self._reserved_names.discard(job.character_name.casefold())
