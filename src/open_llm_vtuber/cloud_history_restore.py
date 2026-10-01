"""Restore one local account's history from the owner's cloud server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .account_manager import (
    get_account_history_root,
    resolve_authenticated_session,
)


CLOUD_SSH_HOST = "root@8.219.247.250"
CLOUD_SSH_KEY = Path.home() / ".ssh" / "aliyun_sg.pem"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024

_connection_lock = threading.Lock()
_active_connections: dict[str, int] = {}
_restoring_accounts: set[str] = set()


def account_connection_opened(account: str) -> bool:
    with _connection_lock:
        if account in _restoring_accounts:
            return False
        _active_connections[account] = _active_connections.get(account, 0) + 1
        return True


def account_connection_closed(account: str) -> None:
    with _connection_lock:
        count = _active_connections.get(account, 0) - 1
        if count > 0:
            _active_connections[account] = count
        else:
            _active_connections.pop(account, None)


def _safe_relative_path(value: str) -> Path:
    posix_path = PurePosixPath(value)
    if (
        not value
        or posix_path.is_absolute()
        or not posix_path.parts
        or posix_path.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix_path.parts)
        or posix_path.parts[0] == ".account.json"
    ):
        raise ValueError("Invalid file path in cloud history archive")
    return Path(*posix_path.parts)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_finder_visible(path: Path) -> None:
    """Clear Finder's hidden flag from restored account contents."""
    if platform.system() == "Darwin":
        hidden_flag = getattr(stat, "UF_HIDDEN", 0x8000)
        paths = (path, *path.rglob("*")) if path.is_dir() else (path,)
        for item in paths:
            flags = item.stat().st_flags
            if flags & hidden_flag:
                os.chflags(item, flags & ~hidden_flag)


def _file_hashes(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink in account data: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _digest_file(path)
    return files


_REMOTE_ARCHIVE_SCRIPT = r'''
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

root = Path("/opt/ai-companion/chat_history") / sys.argv[1]
if not root.is_dir():
    raise SystemExit("Cloud account history does not exist")

files = {}
directories = []
total_bytes = 0
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SystemExit("Cloud account history contains a symlink")
        if path.is_dir():
            directories.append(relative)
            continue
        if not path.is_file() or relative == ".account.json":
            continue
        file_size = path.stat().st_size
        total_bytes += file_size
        if file_size > 128 * 1024 * 1024 or total_bytes > 512 * 1024 * 1024:
            raise SystemExit("Cloud account history exceeds the restore size limit")
        data = path.read_bytes()
        files[relative] = hashlib.sha256(data).hexdigest()
        entry = tarfile.TarInfo("data/" + relative)
        entry.size = len(data)
        entry.mode = 0o600
        archive.addfile(entry, io.BytesIO(data))
    manifest = json.dumps({
        "account": sys.argv[1],
        "files": files,
        "directories": directories,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    entry = tarfile.TarInfo("manifest.json")
    entry.size = len(manifest)
    entry.mode = 0o600
    archive.addfile(entry, io.BytesIO(manifest))

current_files = {}
for path in sorted(root.rglob("*")):
    if path.is_symlink():
        raise SystemExit("Cloud account history changed during download")
    if path.is_file():
        relative = path.relative_to(root).as_posix()
        if relative != ".account.json":
            current_files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
if current_files != files:
    raise SystemExit("Cloud account history changed during download; retry")
'''


def _download_archive(account: str, archive_path: Path) -> None:
    if not CLOUD_SSH_KEY.is_file():
        raise FileNotFoundError(f"SSH key not found: {CLOUD_SSH_KEY}")
    # The account name is sent as a shell-quoted argument, never interpolated
    # into the remote Python program.
    import shlex

    remote_command = f"python3 - {shlex.quote(account)}"
    command = [
        "ssh", "-i", str(CLOUD_SSH_KEY), "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        CLOUD_SSH_HOST, remote_command,
    ]
    with archive_path.open("wb") as output:
        process = subprocess.run(
            command,
            input=_REMOTE_ARCHIVE_SCRIPT.encode("utf-8"),
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Cloud download failed: {detail or process.returncode}")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Cloud history archive is too large")


def _extract_verified_archive(archive_path: Path, stage: Path, account: str) -> dict[str, str]:
    observed: dict[str, str] = {}
    manifest: dict | None = None
    total_bytes = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for entry in archive:
            if entry.name == "manifest.json":
                if manifest is not None or entry.size > 10 * 1024 * 1024:
                    raise ValueError("Invalid cloud history manifest")
                source = archive.extractfile(entry)
                if source is None:
                    raise ValueError("Missing cloud history manifest")
                manifest = json.load(source)
                continue
            if not entry.name.startswith("data/") or not entry.isfile():
                raise ValueError("Unexpected cloud history archive entry")
            relative_text = entry.name.removeprefix("data/")
            relative = _safe_relative_path(relative_text)
            if relative_text in observed or entry.size > MAX_FILE_BYTES:
                raise ValueError("Invalid cloud history file")
            total_bytes += entry.size
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise ValueError("Cloud history is too large")
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(entry)
            if source is None:
                raise ValueError("Cloud history file could not be read")
            digest = hashlib.sha256()
            with destination.open("wb") as target:
                remaining = entry.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Truncated cloud history file")
                    target.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            observed[relative_text] = digest.hexdigest()
    if not isinstance(manifest, dict) or manifest.get("account") != account:
        raise ValueError("Cloud history account does not match")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected or observed != expected:
        raise ValueError("Cloud history file checksum mismatch")
    directories = manifest.get("directories")
    if not isinstance(directories, list):
        raise ValueError("Invalid cloud history directory list")
    for name in directories:
        if not isinstance(name, str):
            raise ValueError("Invalid cloud history directory")
        (stage / _safe_relative_path(name)).mkdir(parents=True, exist_ok=True)
    return observed


def _restore_cloud_history(account: str) -> dict[str, object]:
    account_path = get_account_history_root(account)
    if not account_path.is_dir():
        raise FileNotFoundError("Local account history does not exist")
    project_root = account_path.parent.parent
    backup_root = project_root / "backups" / "cloud-restore"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_root / f"{account}-{stamp}-{uuid4().hex[:8]}"
    stage = Path(tempfile.mkdtemp(prefix=f".{account}-cloud-", dir=account_path.parent))
    archive_path = stage.parent / f".{account}-cloud-{uuid4().hex}.tar.gz"
    previous = account_path.parent / f".{account}-previous-{uuid4().hex}"
    moved_previous = False
    try:
        _download_archive(account, archive_path)
        remote_hashes = _extract_verified_archive(archive_path, stage, account)

        # Keep local passwords, sessions, and feature permissions. Only the
        # account's conversations and memory files are restored from cloud.
        marker = account_path / ".account.json"
        if not marker.is_file():
            raise ValueError("Local account marker is missing")
        shutil.copy2(marker, stage / marker.name)

        before_hashes = _file_hashes(account_path)
        shutil.copytree(account_path, backup_path)
        if _file_hashes(backup_path) != before_hashes:
            raise ValueError("Local backup verification failed")
        if _file_hashes(stage) != {**remote_hashes, ".account.json": _digest_file(marker)}:
            raise ValueError("Cloud staging verification failed")

        _make_finder_visible(stage)

        os.replace(account_path, previous)
        moved_previous = True
        try:
            os.replace(stage, account_path)
            _make_finder_visible(account_path)
            if _file_hashes(account_path) != {**remote_hashes, ".account.json": before_hashes[".account.json"]}:
                raise ValueError("Restored history verification failed")
        except Exception:
            if account_path.exists():
                shutil.rmtree(account_path)
            os.replace(previous, account_path)
            moved_previous = False
            raise
        moved_previous = False
        try:
            shutil.rmtree(previous)
        except OSError as exc:
            logger.warning("Could not remove previous account directory {}: {}", previous, exc)
        logger.info("Restored {} cloud files for {}. Local backup: {}", len(remote_hashes), account, backup_path)
        return {
            "restored_files": len(remote_hashes),
            "backup_files": len(before_hashes),
            "backup_path": str(backup_path),
        }
    finally:
        archive_path.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)
        if moved_previous and previous.exists() and not account_path.exists():
            os.replace(previous, account_path)


def init_local_cloud_restore_routes() -> APIRouter:
    router = APIRouter()

    @router.post("/api/local/cloud-restore")
    async def restore_cloud_history(request: Request):
        if platform.system() != "Darwin":
            return JSONResponse({"error": "Only available on the local Mac"}, status_code=404)
        try:
            payload = await request.json()
            account = resolve_authenticated_session(
                payload.get("account"), payload.get("sessionToken")
            )
        except Exception:
            return JSONResponse({"error": "Local login is invalid"}, status_code=401)
        if account is None:
            return JSONResponse({"error": "Local login has expired"}, status_code=401)

        with _connection_lock:
            if account in _restoring_accounts:
                return JSONResponse({"error": "Restore already in progress"}, status_code=409)
            _restoring_accounts.add(account)
        try:
            for _ in range(50):
                with _connection_lock:
                    active = _active_connections.get(account, 0)
                if active == 0:
                    break
                await asyncio.sleep(0.1)
            else:
                return JSONResponse({"error": "Close other local chats for this account first"}, status_code=409)
            result = await asyncio.to_thread(_restore_cloud_history, account)
            return JSONResponse(result)
        except Exception as exc:
            logger.exception("Cloud history restore failed for {}: {}", account, exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            with _connection_lock:
                _restoring_accounts.discard(account)

    return router
