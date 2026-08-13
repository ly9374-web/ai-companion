#!/usr/bin/env python3
"""Local browser-to-AIe relay that keeps AK, SK, and tokens off the frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ENV_FILE = BASE_DIR / ".env"
LOGGER = logging.getLogger("emotion-camera")


def load_local_env(path: Path = ENV_FILE) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_local_env()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少配置 {name}，请检查 {ENV_FILE}")
    return value


def cloud_ws_url() -> str:
    value = os.environ.get(
        "AIE_CLOUD_WS_URL",
        "wss://api.emorobot.com.cn/ws/realtime",
    ).strip()
    parsed = urlsplit(value)
    if parsed.scheme != "wss" or not parsed.netloc:
        raise RuntimeError("AIE_CLOUD_WS_URL 必须是有效的 wss:// 地址")
    return value


def cloud_http_base() -> str:
    parsed = urlsplit(cloud_ws_url())
    return f"https://{parsed.netloc}"


class CloudTokenCache:
    def __init__(self) -> None:
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._token = ""
        self._expires_at = 0.0

    async def get(self) -> str:
        now = time.time()
        if self._token and self._expires_at - now > 60:
            return self._token
        async with self._lock:
            now = time.time()
            if self._token and self._expires_at - now > 60:
                return self._token
            return await self._refresh()

    async def _refresh(self) -> str:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.post(
                f"{cloud_http_base()}/auth/token",
                data={"ak": required_env("AIE_AK"), "sk": required_env("AIE_SK")},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 200 or not payload.get("token"):
            message = payload.get("detail") or payload.get("msg") or "AK/SK 验证失败"
            raise RuntimeError(str(message))
        self._token = str(payload["token"]).strip()
        self._expires_at = time.time() + max(60.0, float(payload.get("expiresIn") or 3600))
        return self._token


TOKEN_CACHE = CloudTokenCache()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Emotion Camera Relay", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/health")
async def health() -> JSONResponse:
    configured = all(os.environ.get(key, "").strip() for key in ("AIE_AK", "AIE_SK"))
    return JSONResponse({"ok": configured, "configured": configured}, status_code=200 if configured else 503)


async def browser_to_cloud(browser: WebSocket, cloud: Any) -> None:
    while True:
        message = await browser.receive()
        if message.get("type") == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await cloud.send(message["bytes"])
        elif message.get("text") is not None:
            await cloud.send(message["text"])


async def cloud_to_browser(browser: WebSocket, cloud: Any) -> None:
    async for message in cloud:
        if isinstance(message, bytes):
            await browser.send_bytes(message)
        else:
            await browser.send_text(message)


@app.websocket("/ws")
async def realtime_relay(browser: WebSocket) -> None:
    await browser.accept()
    cloud = None
    try:
        token = await TOKEN_CACHE.get()
        cloud = await websockets.connect(
            cloud_ws_url(),
            open_timeout=20,
            close_timeout=5,
            compression=None,
            max_size=8 * 1024 * 1024,
            proxy=None,
        )
        await cloud.send(json.dumps({"type": "auth", "token": token}))

        first_message = await asyncio.wait_for(cloud.recv(), timeout=20)
        if isinstance(first_message, bytes):
            raise RuntimeError("云端认证响应格式异常")
        first_payload = json.loads(first_message)
        if first_payload.get("type") == "error":
            TOKEN_CACHE.invalidate()
            raise RuntimeError(str(first_payload.get("message") or "云端认证失败"))
        if first_payload.get("type") != "connected":
            raise RuntimeError("云端未返回 connected")

        await browser.send_json({"type": "connected"})
        upstream = asyncio.create_task(browser_to_cloud(browser, cloud))
        downstream = asyncio.create_task(cloud_to_browser(browser, cloud))
        done, pending = await asyncio.wait(
            {upstream, downstream},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOGGER.exception("AIe 实时转发失败")
        with suppress(Exception):
            await browser.send_json({"type": "error", "message": str(exc)})
        with suppress(Exception):
            await browser.close(code=1011, reason="AIe relay failed")
    finally:
        if cloud is not None:
            with suppress(Exception):
                await cloud.close()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8765")),
        log_level="info",
    )
