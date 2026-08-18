import os
import json
import re
from uuid import uuid4
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import numpy as np
from datetime import datetime
from fastapi import APIRouter, WebSocket, UploadFile, File, Response
from starlette.responses import FileResponse, JSONResponse
from starlette.websockets import WebSocketDisconnect
from loguru import logger
from .service_context import ServiceContext
from .websocket_handler import WebSocketHandler
from .proxy_handler import ProxyHandler
from .optional_features import (
    get_optional_feature,
    get_expression_feature_dir,
    get_expression_manifest,
)
from .account_manager import (
    AccountAlreadyExists,
    AuthenticationFailed,
    InvalidAccountName,
    InvalidPassword,
    authenticate_account,
    create_persistent_session,
    has_conversation_starters,
    register_account,
    resolve_authenticated_session,
    revoke_persistent_session,
)


def _account_features(account: str) -> dict[str, bool]:
    return {"conversationStarters": has_conversation_starters(account)}


def init_account_routes() -> APIRouter:
    """Create local password authentication and persistent-session endpoints."""
    router = APIRouter()

    @router.post("/api/accounts/login")
    async def login_account(payload: dict):
        try:
            account = authenticate_account(
                payload.get("account"), payload.get("password")
            )
            session_token = create_persistent_session(account)
        except AuthenticationFailed as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except Exception as exc:
            logger.exception("Failed to read account profile: {}", exc)
            return JSONResponse(
                {"error": "账号数据读取失败"},
                status_code=500,
            )
        return JSONResponse(
            {
                "account": account,
                "sessionToken": session_token,
                "features": _account_features(account),
            }
        )

    @router.post("/api/accounts/register")
    async def create_account(payload: dict):
        try:
            account = register_account(payload.get("account"), payload.get("password"))
            session_token = create_persistent_session(account)
        except InvalidAccountName as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except InvalidPassword as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except AccountAlreadyExists as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:
            logger.exception("Failed to create account profile: {}", exc)
            return JSONResponse(
                {"error": "账号数据创建失败"},
                status_code=500,
            )
        return JSONResponse(
            {
                "account": account,
                "sessionToken": session_token,
                "features": _account_features(account),
            },
            status_code=201,
        )

    @router.post("/api/accounts/session")
    async def restore_account_session(payload: dict):
        try:
            account = resolve_authenticated_session(
                payload.get("account"), payload.get("sessionToken")
            )
        except Exception as exc:
            logger.exception("Failed to restore account session: {}", exc)
            return JSONResponse({"error": "账号数据读取失败"}, status_code=500)
        if account is None:
            return JSONResponse({"error": "登录已失效"}, status_code=401)
        return JSONResponse(
            {"account": account, "features": _account_features(account)}
        )

    @router.post("/api/accounts/logout")
    async def logout_account(payload: dict):
        try:
            revoke_persistent_session(
                payload.get("account"), payload.get("sessionToken")
            )
        except Exception as exc:
            logger.exception("Failed to revoke account session: {}", exc)
            return JSONResponse({"error": "退出登录失败"}, status_code=500)
        return Response(status_code=204)

    return router


def init_client_ws_route(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling the `/client-ws` WebSocket connections.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()
    ws_handler = WebSocketHandler(default_context_cache)

    @router.websocket("/client-ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for client connections"""
        try:
            account = resolve_authenticated_session(
                websocket.query_params.get("account"),
                websocket.query_params.get("session"),
            )
        except Exception as exc:
            logger.exception("Failed to authenticate WebSocket account: {}", exc)
            await websocket.close(code=1011, reason="Account storage unavailable")
            return
        if account is None:
            await websocket.close(code=4401, reason="Account login required")
            return
        await websocket.accept()
        client_uid = str(uuid4())

        try:
            await ws_handler.handle_new_connection(websocket, client_uid, account)
            await ws_handler.handle_websocket_communication(websocket, client_uid)
        except WebSocketDisconnect:
            await ws_handler.handle_disconnect(client_uid)
        except Exception as e:
            logger.error(f"Error in WebSocket connection: {e}")
            await ws_handler.handle_disconnect(client_uid)
            raise

    return router


def init_proxy_route(server_url: str) -> APIRouter:
    """
    Create and return API routes for handling proxy connections.

    Args:
        server_url: The WebSocket URL of the actual server

    Returns:
        APIRouter: Configured router with proxy WebSocket endpoint
    """
    router = APIRouter()
    proxy_handlers: dict[str, ProxyHandler] = {}

    def account_server_url(account: str, session_token: str) -> str:
        parsed = urlsplit(server_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["account"] = account
        query["session"] = session_token
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    @router.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        """WebSocket endpoint for proxy connections"""
        session_token = websocket.query_params.get("session")
        try:
            account = resolve_authenticated_session(
                websocket.query_params.get("account"), session_token
            )
        except Exception as exc:
            logger.exception("Failed to authenticate proxy account: {}", exc)
            await websocket.close(code=1011, reason="Account storage unavailable")
            return
        if account is None:
            await websocket.close(code=4401, reason="Account login required")
            return

        proxy_handler = proxy_handlers.get(account)
        if proxy_handler is None:
            proxy_handler = ProxyHandler(account_server_url(account, session_token))
            proxy_handlers[account] = proxy_handler
        try:
            await proxy_handler.handle_client_connection(websocket)
        except Exception as e:
            logger.error(f"Error in proxy connection: {e}")
            raise
        finally:
            if (
                not proxy_handler.clients
                and proxy_handlers.get(account) is proxy_handler
            ):
                proxy_handlers.pop(account, None)

    return router


def init_webtool_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling web tool interactions.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()

    @router.get("/optional-features/expression/manifest")
    async def get_optional_expression_manifest(dir: str | None = None):
        manifest = get_expression_manifest(dir)
        if manifest is None:
            return JSONResponse({"available": False})

        version = manifest.get("version", 1)
        if not isinstance(version, (str, int)):
            version = 1
        entry = manifest["frontend_entry"]
        query_params = {"v": version}
        if dir:
            query_params["dir"] = dir
        query = f"?{urlencode(query_params)}"
        emotion_urls = {
            emotion: f"/optional-features/expression/files/{filename}{query}"
            for emotion, filename in manifest["emotions"].items()
        }
        return JSONResponse(
            {
                "available": True,
                "frontend_entry": (
                    f"/optional-features/expression/files/{entry}{query}"
                ),
                "config": {
                    "default_emotion": manifest.get("default_emotion", "中性"),
                    "transition": manifest.get("transition", {}),
                    "emotions": emotion_urls,
                },
            }
        )

    @router.get("/optional-features/expression/files/{relative_path:path}")
    async def get_optional_expression_file(relative_path: str, dir: str | None = None):
        feature_dir = get_expression_feature_dir(dir)
        manifest = get_expression_manifest(dir)
        if manifest is None:
            return Response(status_code=404)

        allowed_files = {manifest["frontend_entry"], *manifest["emotions"].values()}
        if relative_path not in allowed_files:
            return Response(status_code=404)
        requested_path = (feature_dir / relative_path).resolve()
        if (
            feature_dir.resolve() not in requested_path.parents
            or not requested_path.is_file()
        ):
            return Response(status_code=404)
        media_type = (
            "application/javascript"
            if relative_path.endswith(".js")
            else "image/png"
        )
        return FileResponse(
            requested_path,
            media_type=media_type,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @router.get("/optional-feature/manifest")
    async def get_optional_feature_manifest():
        feature = get_optional_feature()
        if feature is None:
            return JSONResponse({"available": False})
        _feature_dir, manifest = feature
        entry = manifest.get("frontend_entry")
        if not isinstance(entry, str) or not entry.endswith(".js"):
            return JSONResponse({"available": False})
        version = manifest.get("version", 1)
        if not isinstance(version, (str, int)):
            version = 1
        return JSONResponse(
            {
                "available": True,
                "frontend_entry": (
                    f"/optional-feature/files/{entry}?v={version}"
                ),
                "config": manifest.get("config", {}),
            }
        )

    @router.get("/optional-feature/files/{relative_path:path}")
    async def get_optional_feature_file(relative_path: str):
        feature = get_optional_feature()
        if feature is None or not relative_path.endswith(".js"):
            return Response(status_code=404)
        feature_dir, _manifest = feature
        frontend_root = (feature_dir / "frontend").resolve()
        requested_path = (feature_dir / relative_path).resolve()
        if frontend_root not in requested_path.parents or not requested_path.is_file():
            return Response(status_code=404)
        return FileResponse(
            requested_path,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @router.post("/optional-feature/diagnostics")
    async def post_optional_feature_diagnostic(payload: dict):
        event = payload.get("event")
        details = payload.get("details")
        if not isinstance(event, str) or not re.fullmatch(r"[a-z_]{1,64}", event):
            return Response(status_code=400)
        if not isinstance(details, dict):
            details = {}
        safe_details = {
            str(key)[:64]: value
            for key, value in list(details.items())[:30]
            if isinstance(value, (str, int, float, bool, list))
        }
        logger.info("Optional feature diagnostic: {} | {}", event, safe_details)
        return JSONResponse({"ok": True})

    @router.get("/web-tool")
    async def web_tool_redirect():
        """Redirect /web-tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/web_tool")
    async def web_tool_redirect_alt():
        """Redirect /web_tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/live2d-models/info")
    async def get_live2d_folder_info():
        """Get information about available Live2D models"""
        live2d_dir = "live2d-models"
        if not os.path.exists(live2d_dir):
            return JSONResponse(
                {"error": "Live2D models directory not found"}, status_code=404
            )

        valid_characters = []
        supported_extensions = [".png", ".jpg", ".jpeg"]

        for entry in os.scandir(live2d_dir):
            if entry.is_dir():
                folder_name = entry.name.replace("\\", "/")
                model3_file = os.path.join(
                    live2d_dir, folder_name, f"{folder_name}.model3.json"
                ).replace("\\", "/")

                if os.path.isfile(model3_file):
                    # Find avatar file if it exists
                    avatar_file = None
                    for ext in supported_extensions:
                        avatar_path = os.path.join(
                            live2d_dir, folder_name, f"{folder_name}{ext}"
                        )
                        if os.path.isfile(avatar_path):
                            avatar_file = avatar_path.replace("\\", "/")
                            break

                    valid_characters.append(
                        {
                            "name": folder_name,
                            "avatar": avatar_file,
                            "model_path": model3_file,
                        }
                    )
        return JSONResponse(
            {
                "type": "live2d-models/info",
                "count": len(valid_characters),
                "characters": valid_characters,
            }
        )

    @router.post("/asr")
    async def transcribe_audio(file: UploadFile = File(...)):
        """
        Endpoint for transcribing audio using the ASR engine
        """
        logger.info(f"Received audio file for transcription: {file.filename}")

        try:
            contents = await file.read()

            # Validate minimum file size
            if len(contents) < 44:  # Minimum WAV header size
                raise ValueError("Invalid WAV file: File too small")

            # Decode the WAV header and get actual audio data
            wav_header_size = 44  # Standard WAV header size
            audio_data = contents[wav_header_size:]

            # Validate audio data size
            if len(audio_data) % 2 != 0:
                raise ValueError("Invalid audio data: Buffer size must be even")

            # Convert to 16-bit PCM samples to float32
            try:
                audio_array = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            except ValueError as e:
                raise ValueError(
                    f"Audio format error: {str(e)}. Please ensure the file is 16-bit PCM WAV format."
                )

            # Validate audio data
            if len(audio_array) == 0:
                raise ValueError("Empty audio data")

            text = await default_context_cache.asr_engine.async_transcribe_np(
                audio_array
            )
            logger.info(f"Transcription result: {text}")
            return {"text": text}

        except ValueError as e:
            logger.error(f"Audio format error: {e}")
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return Response(
                content=json.dumps(
                    {"error": "Internal server error during transcription"}
                ),
                status_code=500,
                media_type="application/json",
            )

    @router.websocket("/tts-ws")
    async def tts_endpoint(websocket: WebSocket):
        """WebSocket endpoint for TTS generation"""
        await websocket.accept()
        logger.info("TTS WebSocket connection established")

        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text")
                if not text:
                    continue

                logger.info(f"Received text for TTS: {text}")

                # Split text into sentences
                sentences = [s.strip() for s in text.split(".") if s.strip()]

                try:
                    # Generate and send audio for each sentence
                    for sentence in sentences:
                        sentence = sentence + "."  # Add back the period
                        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid4())[:8]}"
                        audio_path = (
                            await default_context_cache.tts_engine.async_generate_audio(
                                text=sentence, file_name_no_ext=file_name
                            )
                        )
                        logger.info(
                            f"Generated audio for sentence: {sentence} at: {audio_path}"
                        )

                        await websocket.send_json(
                            {
                                "status": "partial",
                                "audioPath": audio_path,
                                "text": sentence,
                            }
                        )

                    # Send completion signal
                    await websocket.send_json({"status": "complete"})

                except Exception as e:
                    logger.error(f"Error generating TTS: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info("TTS WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error in TTS WebSocket connection: {e}")
            await websocket.close()

    return router
