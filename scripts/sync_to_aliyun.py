#!/usr/bin/env python3
"""Incrementally deploy local code to the Aliyun host over SSH.

Usage: python3 scripts/sync_to_aliyun.py [--dry-run]

The server's chat history, memories, account data, configuration, models, and
dependencies are outside the upload list. Server-only content files are kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("AI_COMPANION_SSH_HOST", "root@8.219.247.250")
REMOTE_ROOT = os.environ.get("AI_COMPANION_REMOTE_ROOT", "/opt/ai-companion")
KEY = Path(os.environ.get("AI_COMPANION_SSH_KEY", str(ROOT / "钥匙1.pem"))).expanduser()

# Only these source/build trees and root files can be uploaded. Runtime data is
# deliberately absent, regardless of whether Git happens to track it.
SOURCE_DIRS = (
    "src", "frontend-src", "frontend", "content", "prompts", "scripts",
    "docs", "config_templates", "tests", "摄像头",
)
ROOT_FILES = (
    "AGENTS.md", "README.md", "build_frontend.sh", "run.sh",
    "run_server.py", "pyproject.toml", "uv.lock", "model_dict.json",
)
SKIP_DIRS = {
    ".git", ".venv", ".cache", ".uv-cache", "__pycache__", "node_modules",
    "dist", "out", "models", "cache", "logs", "chat_history", "backups",
    "knowledge_base", "private", "tmp", "legacy", "asset",
}
SKIP_NAMES = {
    ".DS_Store", ".metadata_never_index", "conf.yaml", "mcp_servers.json",
    "api_keys.py", "user_credentials.json", "mem.json", "server.log",
    ".account.json", "long_term_memory.md",
}
SKIP_SUFFIXES = (".pem", ".key", ".p12", ".pyc", ".pyo")

# Server-maintained files. They are never uploaded, but the deploy reports when
# one is missing so a half-configured server cannot look healthy.
PREFLIGHT_PATHS = ("frontend/libs", "frontend/assets", "conf.yaml", "mcp_servers.json")


REMOTE_PREFLIGHT = r'''
import json, re, shutil, sys
from pathlib import Path

request = json.load(sys.stdin)
root = Path(request["root"])
report = {
    "fatal": [],
    "warnings": [],
    "mcp": {"uvx": False, "use_mcpp": None, "servers": [], "minimax_key": False},
}
if not root.is_dir():
    report["fatal"].append("服务器上找不到项目目录：" + str(root))
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0)

for relative in request["check_paths"]:
    if not (root / relative).exists():
        report["warnings"].append("服务器上没有 " + relative + "（本脚本不上传，需在服务器上单独维护）")

leftover = sorted(path.name for path in root.parent.glob("ai-companion-stage-*"))
if leftover:
    report["warnings"].append("发现上次失败残留的暂存目录：" + "、".join(leftover[:5]))

report["mcp"]["uvx"] = shutil.which("uvx") is not None

config = root / "conf.yaml"
if config.is_file():
    config_text = config.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*use_mcpp\s*:\s*(\S+)", config_text, re.M)
    report["mcp"]["use_mcpp"] = match.group(1).lower() if match else None
    report["mcp"]["servers"] = [
        name for name in ("MiniMax", "ddg-search") if name in config_text
    ]
else:
    report["warnings"].append("服务器上没有 conf.yaml")

servers = root / "mcp_servers.json"
if servers.is_file():
    servers_text = servers.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'"MINIMAX_API_KEY"\s*:\s*"([^"]*)"', servers_text)
    report["mcp"]["minimax_key"] = bool(match and match.group(1).strip())
else:
    report["warnings"].append("服务器上没有 mcp_servers.json")

print(json.dumps(report, ensure_ascii=False))
'''


REMOTE_DIFF = r'''
import hashlib, json, sys
from pathlib import Path, PurePosixPath

request = json.load(sys.stdin)
root = Path(request["root"])
if not root.is_dir():
    raise SystemExit("Remote project directory is missing")

def safe_path(relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in path.parts):
        raise ValueError("Unsafe relative path")
    target = root.joinpath(*path.parts)
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ValueError("Symlink in deployment path: " + relative)
    return target

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

changed, conflicts = [], []
for item in request["files"]:
    relative = item["path"]
    target = safe_path(relative)
    if not target.exists():
        changed.append(relative)
    elif not target.is_file():
        conflicts.append(relative + " (remote path is not a regular file)")
    elif target.stat().st_size != item["size"] or digest(target) != item["sha256"]:
        if target.stat().st_mtime_ns > item["mtime_ns"] + 2_000_000_000:
            conflicts.append(relative + " (remote copy is newer)")
        else:
            changed.append(relative)

local_assets = {item["path"] for item in request["files"] if item["path"].startswith("frontend/assets/")}
assets_dir = root / "frontend/assets"
stale_assets = []
if assets_dir.is_dir():
    for path in assets_dir.rglob("*"):
        if path.is_symlink():
            conflicts.append(str(path.relative_to(root)) + " (remote asset is a symlink)")
        elif path.is_file() and path.relative_to(root).as_posix() not in local_assets:
            stale_assets.append(path.relative_to(root).as_posix())
print(json.dumps({"changed": changed, "conflicts": conflicts, "stale_assets": sorted(stale_assets)}, ensure_ascii=False))
'''


REMOTE_STAGE = r'''
import json, tempfile, sys
from pathlib import Path
root = Path(json.load(sys.stdin)["root"])
if not root.is_dir():
    raise SystemExit("Remote project directory is missing")
print(json.dumps({"stage": tempfile.mkdtemp(prefix="ai-companion-stage-", dir=root.parent)}))
'''


REMOTE_APPLY = r'''
import hashlib, json, os, pwd, shutil, subprocess, sys, time, urllib.request, uuid
from pathlib import Path, PurePosixPath

request = json.load(sys.stdin)
root = Path(request["root"])
stage = Path(request["stage"])
changed = request["changed"]
stale = request["stale_assets"]
expected = request["sha256"]
if stage.parent != root.parent or not stage.name.startswith("ai-companion-stage-"):
    raise SystemExit("Invalid staging directory")
owner = pwd.getpwnam("aicompanion")
rollback = stage / ".rollback"
old = {}
touched = []
keep_stage = False

def safe_path(relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in path.parts):
        raise ValueError("Unsafe relative path")
    target = root.joinpath(*path.parts)
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ValueError("Symlink in deployment path: " + relative)
    return target

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def ensure_parent(target, uid, gid):
    """Create missing parents and hand them to the service user too, otherwise a
    newly added subpackage would stay root-owned and break runtime writes."""
    missing = []
    current = target.parent
    while not current.exists() and current != root.parent:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        os.chown(directory, uid, gid)

def put(source, target, uid, gid):
    ensure_parent(target, uid, gid)
    temporary = target.with_name(target.name + ".deploy-" + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

# Startup warms up ASR and the RAG embedding model before uvicorn binds the
# port, which takes roughly 26s on the two-core host. Wait far longer than that
# so a slow cold start is never mistaken for a broken deployment.
READY_ATTEMPTS = 120

def http_ok(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:12393/" + path, timeout=3) as response:
            return response.status == 200
    except Exception:
        return False

def restart_and_check():
    subprocess.run(["systemctl", "restart", "ai-companion.service"], check=True, capture_output=True, text=True)
    for _ in range(READY_ATTEMPTS):
        if http_ok("m.html"):
            return
        time.sleep(1)
    raise RuntimeError(
        "Service restarted, but /m.html was still unreachable after "
        + str(READY_ATTEMPTS) + " seconds"
    )

def verify_web_files():
    """The web root is mounted at /, so every fresh page or bundle must answer.
    camera.html is skipped on purpose: the server answers 404 for it unless the
    camera launch mode is active."""
    prefix = "frontend/"
    targets = [item[len(prefix):] for item in changed if item.startswith("frontend/assets/")]
    targets += [
        item[len(prefix):] for item in changed
        if item in ("frontend/index.html", "frontend/m.html")
    ]
    unreachable = [path for path in targets if not http_ok(path)]
    if unreachable:
        raise RuntimeError("部署后无法通过 HTTP 访问：" + "、".join(unreachable))

try:
    for relative in changed:
        source = stage / relative
        if not source.is_file() or digest(source) != expected[relative]:
            raise ValueError("Staged file failed SHA-256 verification: " + relative)
    for relative in changed + stale:
        target = safe_path(relative)
        if target.exists():
            if not target.is_file():
                raise ValueError("Remote path is not a regular file: " + relative)
            saved = rollback / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            stat = target.stat()
            shutil.copy2(target, saved)
            old[relative] = (saved, stat.st_uid, stat.st_gid)
        else:
            old[relative] = None
    for relative in changed:
        put(stage / relative, safe_path(relative), owner.pw_uid, owner.pw_gid)
        touched.append(relative)
    for relative in stale:
        safe_path(relative).unlink()
        touched.append(relative)
    restart_and_check()
    verify_web_files()
    print(json.dumps({"ok": True, "updated": len(changed), "removed_old_assets": len(stale)}))
except Exception as error:
    rollback_error = None
    try:
        for relative in reversed(touched):
            target = safe_path(relative)
            previous = old[relative]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                saved, uid, gid = previous
                put(saved, target, uid, gid)
        if touched:
            restart_and_check()
    except Exception as failure:
        rollback_error = str(failure)
        keep_stage = True
    print(json.dumps({"ok": False, "error": str(error), "rollback_error": rollback_error}))
finally:
    if not keep_stage:
        shutil.rmtree(stage, ignore_errors=True)
'''


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_files() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for here, dirs, files in os.walk(base):
            dirs[:] = [
                name for name in dirs
                if name not in SKIP_DIRS and not name.startswith(".character-generation-")
            ]
            for name in files:
                path = Path(here) / name
                if (name in SKIP_NAMES or name.startswith(".env") or name.startswith("._")
                        or name.startswith("memory.db") or name.endswith((".sqlite", ".sqlite3", ".db", *SKIP_SUFFIXES))):
                    continue
                if path.is_symlink():
                    raise ValueError(f"Source contains a symlink: {path}")
                if path.is_file():
                    paths.append(path)
    return sorted(paths)


def frontend_needs_build() -> bool:
    output = ROOT / "frontend/index.html"
    if not output.is_file():
        return True
    built_at = output.stat().st_mtime_ns
    for directory in (ROOT / "frontend-src/src", ROOT / "frontend-src/resources", ROOT / "摄像头/frontend-src"):
        if not directory.is_dir():
            continue
        for here, dirs, files in os.walk(directory):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            if any((Path(here) / name).stat().st_mtime_ns > built_at for name in files):
                return True
    return any(path.is_file() and path.stat().st_mtime_ns > built_at for path in (ROOT / "frontend-src/vite.config.ts", ROOT / "frontend-src/package.json"))


def remote(ssh: list[str], script: str, request: dict, *, timeout: int = 120) -> dict:
    try:
        result = subprocess.run(
            ssh + ["python3 -c " + shlex.quote(script)],
            input=json.dumps(request, ensure_ascii=False).encode(),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SSH 命令超过 {timeout} 秒没有返回，已中止") from None
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "SSH command failed")
    return json.loads(result.stdout)


def report_preflight(ssh: list[str]) -> int | None:
    """Print the server-side readiness report. Returns an exit code when the
    deploy must stop before touching anything."""
    report = remote(ssh, REMOTE_PREFLIGHT, {"root": REMOTE_ROOT, "check_paths": PREFLIGHT_PATHS})
    for problem in report["fatal"]:
        print("  致命", problem, file=sys.stderr)
    for warning in report["warnings"]:
        print("  提醒", warning)
    mcp = report["mcp"]
    print(
        "  服务器 MCP：use_mcpp={} 服务={} uvx={} MiniMax Key={}".format(
            mcp["use_mcpp"], "、".join(mcp["servers"]) or "未列出",
            "有" if mcp["uvx"] else "无",
            "已配置" if mcp["minimax_key"] else "缺失",
        )
    )
    return 3 if report["fatal"] else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Compare files without building or uploading")
    args = parser.parse_args()
    if not KEY.is_file():
        parser.error(f"SSH key not found: {KEY}")
    if frontend_needs_build():
        if args.dry_run:
            print("提示：前端源码比构建产物新；预览结束后部署时会先运行 build_frontend.sh。")
        else:
            print("前端源码有更新，正在生成部署文件……", flush=True)
            subprocess.run([str(ROOT / "build_frontend.sh")], cwd=ROOT, check=True)

    files = source_files()
    manifest = [
        {"path": path.relative_to(ROOT).as_posix(), "size": path.stat().st_size,
         "mtime_ns": path.stat().st_mtime_ns, "sha256": digest(path)}
        for path in files
    ]
    with tempfile.TemporaryDirectory(prefix="ai-companion-sync-") as temp:
        # The project copy of 钥匙1.pem can be world-readable. OpenSSH needs 0600.
        private_key = Path(temp) / "deploy-key.pem"
        shutil.copyfile(KEY, private_key)
        private_key.chmod(0o600)
        ssh = [
            "ssh", "-T", "-i", str(private_key),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4",
            HOST,
        ]
        print(f"目标：{HOST} 的 {REMOTE_ROOT}（密钥 {KEY.name}）", flush=True)
        stopped = report_preflight(ssh)
        if stopped is not None:
            return stopped
        diff = remote(ssh, REMOTE_DIFF, {"root": REMOTE_ROOT, "files": manifest})
        changed = diff["changed"]
        stale = diff["stale_assets"]
        conflicts = diff["conflicts"]
        sizes = {item["path"]: item["size"] for item in manifest}
        print(f"对比完成：{len(changed)} 个文件需上传（原始大小 {sum(sizes[p] for p in changed):,} 字节），{len(stale)} 个旧前端文件可清理。")
        for path in changed:
            print("  更新", path)
        for path in stale:
            print("  清理", path)
        if conflicts:
            print("服务器存在更新的同名文件，已中止，避免覆盖：", file=sys.stderr)
            for path in conflicts:
                print(" ", path, file=sys.stderr)
            return 2
        if args.dry_run or not (changed or stale):
            return 0

        stage = remote(ssh, REMOTE_STAGE, {"root": REMOTE_ROOT})["stage"]
        apply_started = False
        try:
            archive = Path(temp) / "changes.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for relative in changed:
                    path = ROOT / relative
                    info = tarfile.TarInfo(relative)
                    info.size = path.stat().st_size
                    info.mtime = int(path.stat().st_mtime)
                    info.mode = path.stat().st_mode & 0o777
                    with path.open("rb") as stream:
                        bundle.addfile(info, stream)
            with archive.open("rb") as stream:
                transfer = subprocess.run(
                    ssh + ["tar -xzf - -C " + shlex.quote(stage)],
                    stdin=stream, capture_output=True, timeout=900,
                )
            if transfer.returncode:
                raise RuntimeError(transfer.stderr.decode(errors="replace").strip() or "Upload failed")
            apply_started = True
            result = remote(ssh, REMOTE_APPLY, {
                "root": REMOTE_ROOT, "stage": stage, "changed": changed,
                "stale_assets": stale,
                "sha256": {item["path"]: item["sha256"] for item in manifest if item["path"] in changed},
            }, timeout=600)
            if not result["ok"]:
                raise RuntimeError(f"Deployment failed: {result['error']}; rollback: {result['rollback_error'] or 'completed'}")
            print(f"部署完成：更新 {result['updated']} 个文件，清理 {result['removed_old_assets']} 个旧前端文件。")
            return 0
        finally:
            # Once apply begins, its rollback may need the staged backup. Leave
            # the directory intact if the SSH session ends unexpectedly.
            if not apply_started:
                cleanup = ("import json,shutil,sys;from pathlib import Path;"
                           "x=json.load(sys.stdin);p=Path(x['stage']);r=Path(x['root']);"
                           "assert p.parent==r.parent and p.name.startswith('ai-companion-stage-');"
                           "shutil.rmtree(p,ignore_errors=True)")
                remote(ssh, cleanup, {"stage": stage, "root": REMOTE_ROOT})


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"部署中止：{error}", file=sys.stderr)
        raise SystemExit(1) from None
