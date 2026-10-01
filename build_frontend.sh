#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
frontend_source_dir="$project_dir/frontend-src"
frontend_build_dir="$frontend_source_dir/dist/web"
frontend_deploy_dir="$project_dir/frontend"

if [ ! -f "$frontend_source_dir/package.json" ]; then
    echo "[ERROR] Editable frontend source is missing: $frontend_source_dir"
    exit 1
fi

if [ ! -d "$frontend_source_dir/node_modules" ]; then
    echo "[ERROR] Frontend dependencies are missing. Run: npm --prefix frontend-src install"
    exit 1
fi

echo "[INFO] Building editable React frontend..."
npm --prefix "$frontend_source_dir" run build:web

if [ ! -f "$frontend_build_dir/index.html" ]; then
    echo "[ERROR] Frontend build did not produce index.html"
    exit 1
fi

echo "[INFO] Syncing build output to frontend/..."
rsync -a --delete \
    --exclude='.git/' \
    --exclude='.DS_Store' \
    "$frontend_build_dir/" \
    "$frontend_deploy_dir/"

if [ -f "$project_dir/摄像头/optional-feature.json" ]; then
    echo "[INFO] Building the optional camera page..."
    AI_COMPANION_CAMERA_BUILD=1 npm --prefix "$frontend_source_dir" run build:web
    camera_build_dir="$frontend_source_dir/dist/camera"
    if [ ! -f "$camera_build_dir/index.html" ]; then
        echo "[ERROR] Camera build did not produce index.html"
        exit 1
    fi
    cp "$camera_build_dir/index.html" "$frontend_deploy_dir/camera.html"
    rsync -a "$camera_build_dir/assets/" "$frontend_deploy_dir/assets/"
fi

echo "[OK] Desktop, mobile, and optional camera pages deployed to frontend/."
