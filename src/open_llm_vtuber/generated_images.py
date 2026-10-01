"""Account-private generated images retained for one day."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from loguru import logger

from .account_manager import CHAT_HISTORY_ROOT, get_account_history_root


IMAGE_TTL_SECONDS = 24 * 60 * 60
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")


def _image_dir(account: str) -> Path:
    return get_account_history_root(account) / "generated_images"


async def _check_public_https(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Image URL must be public HTTPS")
    if parsed.port not in (None, 443):
        raise ValueError("Unexpected image URL port")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname, 443, type=socket.SOCK_STREAM
    )
    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global for address in addresses
    ):
        raise ValueError("Image URL resolves to a non-public address")


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("MiniMax result is not a supported image")


async def _download_image(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        for _ in range(4):
            await _check_public_https(url)
            async with client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Image redirect has no destination")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("Generated image exceeds 20 MB")
                    chunks.append(chunk)
                data = b"".join(chunks)
                return data, _image_mime(data)
    raise ValueError("Too many image redirects")


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def save_generated_images(
    account: str, conf_uid: str, history_uid: str, urls: list[str]
) -> list[str]:
    """Copy MiniMax image URLs into private storage before they expire."""
    image_dir = _image_dir(account)
    image_dir.mkdir(parents=True, exist_ok=True)
    async def save_one(url: str) -> str:
        try:
            data, mime_type = await _download_image(url)
            image_id = uuid4().hex
            created_at = time.time()
            _atomic_bytes(image_dir / f"{image_id}.img", data)
            _atomic_bytes(
                image_dir / f"{image_id}.json",
                json.dumps({
                    "id": image_id,
                    "conf_uid": conf_uid,
                    "history_uid": history_uid,
                    "created_at": created_at,
                    "mime_type": mime_type,
                }).encode("utf-8"),
            )
            return image_id
        except (OSError, ValueError, httpx.HTTPError) as exc:
            logger.warning("Could not retain MiniMax image for 24 hours: {}", exc)
            return ""

    return list(await asyncio.gather(*(save_one(url) for url in urls[:9])))


def _read_metadata(account: str, image_id: str) -> dict | None:
    if not _IMAGE_ID.fullmatch(image_id):
        return None
    metadata_path = _image_dir(account) / f"{image_id}.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            metadata.get("id") == image_id
            and isinstance(metadata.get("created_at"), (int, float))
            and metadata.get("mime_type") in (
                "image/png", "image/jpeg", "image/gif", "image/webp"
            )
            and time.time() - metadata["created_at"] < IMAGE_TTL_SECONDS
            and (_image_dir(account) / f"{image_id}.img").is_file()
        ):
            return metadata
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def get_generated_image(account: str, image_id: str) -> tuple[Path, str] | None:
    metadata = _read_metadata(account, image_id)
    if not metadata:
        return None
    return _image_dir(account) / f"{image_id}.img", metadata["mime_type"]


def list_history_images(account: str, conf_uid: str, history_uid: str) -> list[dict]:
    image_dir = _image_dir(account)
    if not image_dir.is_dir():
        return []
    images = []
    for path in image_dir.glob("*.json"):
        metadata = _read_metadata(account, path.stem)
        if metadata and metadata.get("conf_uid") == conf_uid and metadata.get("history_uid") == history_uid:
            images.append({"id": metadata["id"], "created_at": metadata["created_at"]})
    return sorted(images, key=lambda item: item["created_at"])


def cleanup_expired_images() -> None:
    """Delete expired files, including image files left by interrupted writes."""
    if not CHAT_HISTORY_ROOT.is_dir():
        return
    cutoff = time.time() - IMAGE_TTL_SECONDS
    for account_dir in CHAT_HISTORY_ROOT.iterdir():
        image_dir = account_dir / "generated_images"
        if not image_dir.is_dir():
            continue
        for path in image_dir.iterdir():
            if path.suffix not in (".img", ".json"):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove expired generated image: {}", exc)


async def cleanup_expired_images_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_expired_images)
        except OSError as exc:
            logger.warning("Generated image cleanup failed: {}", exc)
        await asyncio.sleep(5 * 60)
