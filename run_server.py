import os
import sys
import atexit
import asyncio
import argparse
import socket
from pathlib import Path
import tomllib
import uvicorn
from loguru import logger

from src.open_llm_vtuber.server import WebSocketServer
from src.open_llm_vtuber.config_manager import Config, read_yaml, validate_config

os.environ["HF_HOME"] = str(Path(__file__).parent / "models")
os.environ["MODELSCOPE_CACHE"] = str(Path(__file__).parent / "models")


def get_version() -> str:
    with open("pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    return pyproject["project"]["version"]


def init_logger(console_log_level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=console_log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | {message}",
        colorize=True,
    )
    logger.add(
        "logs/debug_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message} | {extra}",
        backtrace=True,
        diagnose=True,
    )


def check_frontend():
    """Check if the frontend directory has the required files."""
    frontend_path = Path(__file__).parent / "frontend" / "index.html"
    if not frontend_path.exists():
        logger.error(
            "Frontend files not found. Make sure the 'frontend' directory "
            "is present and contains the built frontend."
        )


def ensure_port_is_available(host: str, port: int) -> None:
    """Refuse to start when an older backend is already serving this port."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for family, socktype, proto, _, address in addresses:
        with socket.socket(family, socktype, proto) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(address) == 0:
                raise RuntimeError(
                    f"Port {host}:{port} is already in use by another backend. "
                    "Stop the existing process before starting a new one; otherwise "
                    "the browser may keep using stale configuration and prompts."
                )


def parse_args():
    parser = argparse.ArgumentParser(description="Open-LLM-VTuber Server (simplified)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--hf_mirror", action="store_true", help="Use Hugging Face mirror"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable hot-reload (auto-restart on code changes)",
    )
    return parser.parse_args()


@logger.catch
def run(console_log_level: str, reload: bool = False):
    init_logger(console_log_level)
    logger.info(f"Open-LLM-VTuber (simplified), version v{get_version()}")

    check_frontend()

    config: Config = validate_config(read_yaml("conf.yaml"))
    server_config = config.system_config

    try:
        ensure_port_is_available(server_config.host, server_config.port)
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    atexit.register(WebSocketServer.clean_cache)

    if server_config.enable_proxy:
        logger.info("Proxy mode enabled - /proxy-ws endpoint will be available")

    server = WebSocketServer(config=config)

    logger.info("Initializing server context...")
    try:
        asyncio.run(server.initialize())
        logger.info("Server context initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize server context: {e}")
        sys.exit(1)

    if reload:
        logger.info("Hot-reload enabled — server will restart on code changes")
        logger.info("Watching: src/, prompts/, conf.yaml")

    logger.info(f"Starting server on {server_config.host}:{server_config.port}")
    uvicorn.run(
        app=server.app,
        host=server_config.host,
        port=server_config.port,
        log_level=console_log_level.lower(),
        reload=reload,
        reload_dirs=["src", "prompts"] if reload else None,
    )


if __name__ == "__main__":
    args = parse_args()
    console_log_level = "DEBUG" if args.verbose else "INFO"
    if args.verbose:
        logger.info("Running in verbose mode")
    else:
        logger.info(
            "Running in standard mode. For detailed debug logs, use: ./run.sh --verbose"
        )
    if args.hf_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    run(console_log_level=console_log_level, reload=args.reload)
