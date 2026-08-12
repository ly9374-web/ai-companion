#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIE 云端实时摄像头检测客户端 - 最终稳定版
功能：通过 AK/SK 自动获取 Token，连接 WebSocket 服务器进行实时人脸/情绪/心率检测
版本：3.4.0-continuous-streaming
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import platform
import random
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import cv2
import numpy as np
import requests
import websockets
from websockets.exceptions import ConnectionClosed

# 可选导入：用于中文预览显示
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# 可选导入：启动弹窗（tkinter 是 Python 标准库，通常可用）
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

# ======================== 默认配置 ========================
SERVER_URL = "wss://api.emorobot.com.cn/ws/realtime"
TOKEN_SERVER_URL = "https://api.emorobot.com.cn/auth/token"
CAMERA_INDEX = 0
CAMERA_BACKEND = "auto"
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
TARGET_FPS = 28          # 默认 28，范围 25-30
FPS_MIN = 25
FPS_MAX = 30
JPEG_QUALITY = 72
USE_MJPEG = True
SHOW_PREVIEW = True
PRINT_MODE = "summary"
PRINT_INTERVAL = 0.0
INCLUDE_LANDMARKS = False
AUTO_RECONNECT = True
MAX_RETRIES = 5
RETRY_DELAY = 2.0
# ========================================================


# ======================== 自定义异常 ========================
class TokenFetchError(RuntimeError):
    pass

class TokenInvalid(RuntimeError):
    pass

class CameraError(RuntimeError):
    pass


# ======================== 配置数据类 ========================
@dataclass
class ClientConfig:
    server_url: str = SERVER_URL
    token_server_url: str = TOKEN_SERVER_URL
    ak: str = ""
    sk: str = ""
    token: str = ""
    camera_index: int = CAMERA_INDEX
    camera_backend: str = CAMERA_BACKEND
    width: int = FRAME_WIDTH
    height: int = FRAME_HEIGHT
    fps: float = TARGET_FPS
    jpeg_quality: int = JPEG_QUALITY
    use_mjpeg: bool = USE_MJPEG
    show_preview: bool = SHOW_PREVIEW
    print_mode: str = PRINT_MODE
    print_interval: float = PRINT_INTERVAL
    include_landmarks: bool = INCLUDE_LANDMARKS
    auto_reconnect: bool = AUTO_RECONNECT
    max_retries: int = MAX_RETRIES
    retry_delay: float = RETRY_DELAY


# ======================== Token 管理 ========================
def _fetch_token_sync(config: ClientConfig) -> str:
    if not config.ak or not config.sk:
        raise TokenFetchError("AK 或 SK 未提供")
    data = {"ak": config.ak, "sk": config.sk}
    try:
        resp = requests.post(config.token_server_url, data=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 200:
            token = result.get("token")
            if not token:
                raise TokenFetchError("服务端未返回 token 字段")
            return token
        raise TokenFetchError(f"获取 Token 失败: {result.get('msg', '未知错误')}")
    except requests.exceptions.Timeout:
        raise TokenFetchError("获取 Token 超时")
    except requests.exceptions.ConnectionError:
        raise TokenFetchError("无法连接 Token 服务器")
    except Exception as e:
        raise TokenFetchError(f"获取 Token 错误: {e}")


async def fetch_token(config: ClientConfig) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_token_sync, config)


# ======================== 启动弹窗 ========================
def show_credential_dialog(parent_ak: str = "", parent_sk: str = "") -> Optional[Tuple[str, str]]:
    """
    弹出 GUI 对话框，让用户输入 AK / SK。
    返回 (ak, sk) 或 None（用户取消）。
    使用 tkinter（Python 标准库，无需额外安装）。
    """
    if not TKINTER_AVAILABLE:
        # tkinter 不可用 → 退化为命令行输入
        return _prompt_credentials_console()

    result: dict[str, Optional[str]] = {"ak": None, "sk": None}

    root = tk.Tk()
    root.title("AIE 实时检测 - 请输入认证信息")
    root.geometry("460x260")
    root.resizable(False, False)

    # 居中窗口
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (460 // 2)
    y = (root.winfo_screenheight() // 2) - (260 // 2)
    root.geometry(f"460x260+{x}+{y}")

    # 尝试设置图标（可选，没有也不报错）
    try:
        root.iconname("AIE")
    except Exception:
        pass

    # 变量
    ak_var = tk.StringVar(value=parent_ak)
    sk_var = tk.StringVar(value=parent_sk)
    status_var = tk.StringVar(value="请输入 Access Key 和 Secret Key")

    # ---- 样式 ----
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # ---- 控件 ----
    main = ttk.Frame(root, padding=20)
    main.pack(fill="both", expand=True)

    ttk.Label(main, text="AIE 云端实时检测", font=("Segoe UI", 14, "bold")).pack(pady=(0, 5))
    ttk.Label(main, text="请输入您的 AK / SK 进行认证", font=("Segoe UI", 9)).pack(pady=(0, 15))

    # AK
    ak_frame = ttk.Frame(main)
    ak_frame.pack(fill="x", pady=5)
    ttk.Label(ak_frame, text="AK:", width=6).pack(side="left")
    ak_entry = ttk.Entry(ak_frame, textvariable=ak_var, width=40, font=("Consolas", 10))
    ak_entry.pack(side="left", padx=5, fill="x", expand=True)

    # SK
    sk_frame = ttk.Frame(main)
    sk_frame.pack(fill="x", pady=5)
    ttk.Label(sk_frame, text="SK:", width=6).pack(side="left")
    sk_entry = ttk.Entry(sk_frame, textvariable=sk_var, width=40, show="●", font=("Consolas", 10))
    sk_entry.pack(side="left", padx=5, fill="x", expand=True)

    # 显示/隐藏 SK
    show_sk = tk.BooleanVar(value=False)
    def _toggle_sk():
        sk_entry.config(show="" if show_sk.get() else "●")
    ttk.Checkbutton(main, text="显示 SK", variable=show_sk, command=_toggle_sk).pack(anchor="w", pady=5)

    # 状态栏
    ttk.Label(main, textvariable=status_var, foreground="#c05050", font=("Segoe UI", 9)).pack(pady=(5, 10))

    # 按钮
    btn_frame = ttk.Frame(main)
    btn_frame.pack(fill="x")

    def _on_ok():
        ak = ak_var.get().strip()
        sk = sk_var.get().strip()
        if not ak or not sk:
            status_var.set("❌ AK 和 SK 不能为空")
            return
        result["ak"] = ak
        result["sk"] = sk
        root.destroy()

    def _on_cancel():
        root.destroy()

    def _on_enter(e):
        _on_ok()

    ttk.Button(btn_frame, text="确定", command=_on_ok, width=12).pack(side="right", padx=5)
    ttk.Button(btn_frame, text="取消", command=_on_cancel, width=10).pack(side="right", padx=5)

    # 绑定回车
    root.bind("<Return>", _on_enter)
    root.bind("<Escape>", lambda e: _on_cancel())

    # 聚焦 AK 输入框
    ak_entry.focus_set()
    ak_entry.select_range(0, "end")

    root.protocol("WM_DELETE_WINDOW", _on_cancel)
    root.mainloop()

    ak = result.get("ak")
    sk = result.get("sk")
    if ak and sk:
        return (ak, sk)
    return None


def _prompt_credentials_console() -> Optional[Tuple[str, str]]:
    """tkinter 不可用时的命令行降级方案"""
    try:
        ak = input("请输入 AK: ").strip()
        sk = input("请输入 SK: ").strip()
        if ak and sk:
            return (ak, sk)
    except (KeyboardInterrupt, EOFError):
        pass
    return None


# ======================== 工具函数 ========================
def _valid_number(v: Any, invalid: float = 9999.0) -> Optional[float]:
    try:
        return None if float(v) == invalid else float(v)
    except (TypeError, ValueError):
        return None


def compact_result(data: dict[str, Any]) -> dict[str, Any]:
    face = data.get("face") or {}
    gaze = data.get("gaze") or {}
    emotion = data.get("emotion") or {}
    heart = data.get("heart_rate") or {}
    blink = data.get("blink") or {}
    top3 = data.get("au_action_top3") or {}

    aus = [{
        "label": r.get("label"),
        "label_en": r.get("label_en"),
        "intensity": r.get("intensity"),
    } for r in (top3.get("top_actions") or [])]

    rr = None
    if heart.get("respiratory_rate_is_valid"):
        rr = _valid_number(heart.get("respiratory_rate"))

    return {
        "seq": data.get("image_sequence"),
        "face": bool(face.get("face_detected", False)),
        "emo": emotion.get("emotion_label"),
        "emo_conf": emotion.get("emotion_confidence"),
        "hr": _valid_number(heart.get("heart_rate")),
        "hrv": _valid_number(heart.get("hrv")),
        "rr": rr,
        "blink": blink.get("blink_count"),
        "eye": blink.get("eye_state"),
        "pitch": gaze.get("pitch"),
        "yaw": gaze.get("yaw"),
        "focus": gaze.get("focusing"),
        "aus": aus,
        "recording_active": data.get("recordingActive"),
        "task_id": data.get("recordingTaskId"),
        "stage_window_seconds": data.get("stageWindowSeconds"),
        "stage_elapsed_seconds": data.get("stageElapsedSeconds"),
        "stage_completed_count": data.get("stageCompletedCount"),
        "next_stage_in_seconds": data.get("nextStageInSeconds"),
        "latest_stage_result": data.get("latestStageResult"),
    }


def _camera_backend(name: str) -> Optional[int]:
    return {
        "dshow": getattr(cv2, "CAP_DSHOW", None),
        "msmf": getattr(cv2, "CAP_MSMF", None),
        "v4l2": getattr(cv2, "CAP_V4L2", None),
    }.get(name.lower())


def _find_chinese_font(size: int = 22) -> Optional[Any]:
    """查找中文字体，返回 PIL Font 对象"""
    if not PIL_AVAILABLE:
        return None
    paths = []
    sysname = platform.system()
    if sysname == "Windows":
        paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttf",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ]
    elif sysname == "Darwin":
        paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
    for p in paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None


# ======================== 预览渲染 ========================
class PreviewRenderer:
    """
    文字直接叠加在视频画面上，无背景条。
    - 有中文字体 → PIL 渲染（支持中文）
    - 无中文字体 → OpenCV 渲染（英文）
    - 文字带黑色描边/阴影，确保任何背景下都清晰可读
    """

    # 颜色 (BGR)
    C_GREEN   = (0, 255, 0)
    C_YELLOW  = (0, 255, 255)
    C_RED     = (0, 0, 255)
    C_CYAN    = (255, 255, 0)
    C_WHITE   = (255, 255, 255)
    C_ORANGE  = (0, 165, 255)
    C_SHADOW  = (0, 0, 0)  # 黑色阴影

    PADDING_TOP = 10       # 顶部边距
    LINE_SPACING = 30      # 行间距
    TEXT_LEFT = 12          # 左侧边距
    BOTTOM_MARGIN = 40      # 底部保留空间

    def __init__(self, width: int, height: int, font: Optional[Any] = None):
        self.width = width
        self.height = height
        self.font = font
        self.use_pil = font is not None
        # OpenCV 文字参数
        self.cv2_font = cv2.FONT_HERSHEY_SIMPLEX
        self.cv2_scale = 0.65
        self.cv2_thickness = 2

    # ---------- 公共入口 ----------
    def render(self, frame: np.ndarray, ctx: dict[str, Any]) -> np.ndarray:
        if not self.use_pil:
            return self._render_cv2(frame, ctx)
        return self._render_pil(frame, ctx)

    # ---------- OpenCV 渲染（中文，直接叠加，带阴影）----------
    def _render_cv2(self, frame: np.ndarray, ctx: dict[str, Any]) -> np.ndarray:
        lines = self._build_lines(ctx)
        y = self.PADDING_TOP + 20  # 第一行基线位置

        for text, color in lines:
            # 黑色阴影（右下偏移2px）
            cv2.putText(frame, text, (self.TEXT_LEFT + 2, y + 2),
                        self.cv2_font, self.cv2_scale, self.C_SHADOW,
                        self.cv2_thickness + 1, cv2.LINE_AA)
            # 主文字
            cv2.putText(frame, text, (self.TEXT_LEFT, y),
                        self.cv2_font, self.cv2_scale, color,
                        self.cv2_thickness, cv2.LINE_AA)
            y += self.LINE_SPACING

        # 右上角系统时间（白色，每秒更新）
        clock_text = ctx.get("clock", "")
        if clock_text:
            clock_scale = 0.6
            clock_thickness = 2
            clock_size = cv2.getTextSize(clock_text, self.cv2_font, clock_scale, clock_thickness)[0]
            clock_x = self.width - clock_size[0] - 15  # 右对齐，留15px边距
            clock_y = 25
            # 阴影
            cv2.putText(frame, clock_text, (clock_x + 2, clock_y + 2),
                        self.cv2_font, clock_scale, self.C_SHADOW,
                        clock_thickness + 1, cv2.LINE_AA)
            # 主文字
            cv2.putText(frame, clock_text, (clock_x, clock_y),
                        self.cv2_font, clock_scale, self.C_WHITE,
                        clock_thickness, cv2.LINE_AA)

        # 未检测到人脸 → 屏幕中央显示警告
        if not ctx.get("face_detected", True):
            warn_text = "没有检测到人脸"
            warn_scale = 1.0
            warn_thickness = 3
            warn_size = cv2.getTextSize(warn_text, self.cv2_font, warn_scale, warn_thickness)[0]
            warn_x = (self.width - warn_size[0]) // 2
            warn_y = self.height // 2
            # 黑色阴影
            cv2.putText(frame, warn_text, (warn_x + 3, warn_y + 3),
                        self.cv2_font, warn_scale, self.C_SHADOW,
                        warn_thickness + 1, cv2.LINE_AA)
            # 红色主文字
            cv2.putText(frame, warn_text, (warn_x, warn_y),
                        self.cv2_font, warn_scale, self.C_RED,
                        warn_thickness, cv2.LINE_AA)

        # 已暂停 → 屏幕中央偏下显示提示
        if ctx.get("paused", False):
            pause_text = "⏸ 已暂停（等待人脸）"
            pause_scale = 0.8
            pause_thickness = 2
            pause_size = cv2.getTextSize(pause_text, self.cv2_font, pause_scale, pause_thickness)[0]
            pause_x = (self.width - pause_size[0]) // 2
            pause_y = self.height // 2 + 50  # 在"没有检测到人脸"下方
            # 黑色阴影
            cv2.putText(frame, pause_text, (pause_x + 2, pause_y + 2),
                        self.cv2_font, pause_scale, self.C_SHADOW,
                        pause_thickness + 1, cv2.LINE_AA)
            # 黄色主文字
            cv2.putText(frame, pause_text, (pause_x, pause_y),
                        self.cv2_font, pause_scale, self.C_YELLOW,
                        pause_thickness, cv2.LINE_AA)

        # 底部提示（小字）
        hint_y = self.height - 15
        hint = "Q=退出并生成最终报告"
        cv2.putText(frame, hint, (self.TEXT_LEFT + 2, hint_y + 2),
                    self.cv2_font, 0.45, self.C_SHADOW, 2, cv2.LINE_AA)
        cv2.putText(frame, hint, (self.TEXT_LEFT, hint_y),
                    self.cv2_font, 0.45, self.C_WHITE, 1, cv2.LINE_AA)

        return frame

    # ---------- PIL 渲染（中文，直接叠加，带阴影）----------
    def _render_pil(self, frame: np.ndarray, ctx: dict[str, Any]) -> np.ndarray:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil_img)

        font = self.font
        clock_font = font
        warn_font = font  # 警告文字用同字体
        lines = self._build_lines(ctx)

        y = self.PADDING_TOP + 4

        for text, color_rgb in lines:
            r, g, b = color_rgb[2], color_rgb[1], color_rgb[0]
            draw.text((self.TEXT_LEFT + 2, y + 2), text, fill=(0, 0, 0), font=font)
            draw.text((self.TEXT_LEFT, y), text, fill=(r, g, b), font=font)
            y += self.LINE_SPACING

        # 右上角系统时间
        clock_text = ctx.get("clock", "")
        if clock_text:
            try:
                bbox = draw.textbbox((0, 0), clock_text, font=clock_font)
                text_w = bbox[2] - bbox[0]
            except AttributeError:
                text_w = len(clock_text) * 12
            clock_x = self.width - text_w - 15
            clock_y = 10
            draw.text((clock_x + 2, clock_y + 2), clock_text, fill=(0, 0, 0), font=clock_font)
            draw.text((clock_x, clock_y), clock_text, fill=(255, 255, 255), font=clock_font)

        # 未检测到人脸 → 屏幕中央显示警告
        if not ctx.get("face_detected", True):
            warn_text = "没有检测到人脸"
            try:
                warn_bbox = draw.textbbox((0, 0), warn_text, font=warn_font)
                warn_w = warn_bbox[2] - warn_bbox[0]
                warn_h = warn_bbox[3] - warn_bbox[1]
            except AttributeError:
                warn_w = len(warn_text) * 20
                warn_h = 24
            warn_x = (self.width - warn_w) // 2
            warn_y = (self.height - warn_h) // 2
            # 黑色阴影
            draw.text((warn_x + 3, warn_y + 3), warn_text, fill=(0, 0, 0), font=warn_font)
            # 红色主文字
            draw.text((warn_x, warn_y), warn_text, fill=(0, 0, 255), font=warn_font)

        # 已暂停 → 屏幕中央偏下显示提示
        if ctx.get("paused", False):
            pause_text = "⏸ 已暂停（等待人脸）"
            try:
                pause_bbox = draw.textbbox((0, 0), pause_text, font=warn_font)
                pause_w = pause_bbox[2] - pause_bbox[0]
            except AttributeError:
                pause_w = len(pause_text) * 16
            pause_x = (self.width - pause_w) // 2
            pause_y = (self.height // 2) + 50
            # 黑色阴影
            draw.text((pause_x + 2, pause_y + 2), pause_text, fill=(0, 0, 0), font=warn_font)
            # 黄色主文字 (RGB: 黄=BGR(0,255,255) → RGB(255,255,0))
            draw.text((pause_x, pause_y), pause_text, fill=(255, 255, 0), font=warn_font)

        # 底部提示
        hint = "Q=退出并生成最终报告"
        hint_y = self.height - 35
        draw.text((self.TEXT_LEFT + 2, hint_y + 2), hint, fill=(0, 0, 0), font=font)
        draw.text((self.TEXT_LEFT, hint_y), hint, fill=(255, 255, 255), font=font)

        result = np.array(pil_img)
        return cv2.cvtColor(result, cv2.COLOR_RGB2BGR)

    # ---------- 情绪英文→中文映射 ----------
    EMOTION_CN = {
        "happy": "开心",
        "sad": "悲伤",
        "angry": "愤怒",
        "anger": "生气",
        "surprise": "惊讶",
        "surprised": "惊讶",
        "fear": "恐惧",
        "disgust": "厌恶",
        "neutral": "中性",
        "contempt": "轻蔑",
        "anxiety": "焦虑",
        "confused": "困惑",
        "calm": "平静",
        "excited": "兴奋",
        "bored": "无聊",
        "stressed": "压力",
        "tired": "疲惫",
        "focused": "专注",
        "distracted": "分心",
    }

    # ---------- 构建显示内容 ----------
    def _build_lines(self, ctx: dict[str, Any]) -> list[tuple[str, tuple]]:
        """构建所有要显示的行，返回 [(text, color_bgr), ...]"""
        lines = []

        # 第1行：FPS + 状态 + 帧数（绿色）
        fps = ctx.get("fps", 0)
        status = ctx.get("status", "")
        sent = ctx.get("sent", 0)
        lines.append((f"帧率:{fps:.1f} | {status} | 帧数:{sent}", self.C_GREEN))

        # 第2行：心率（黄色）
        hr = ctx.get("hr")
        if hr:
            lines.append((f"心率: {hr:.0f} BPM", self.C_YELLOW))

        # 第3行：情绪（黄色）— 英文 + 中文
        emo = ctx.get("emo")
        if emo:
            conf = ctx.get("emo_conf")
            conf_str = f" ({conf:.0%})" if conf else ""
            emo_cn = self.EMOTION_CN.get(emo.lower(), "")
            emo_display = f"{emo}/{emo_cn}" if emo_cn else emo
            lines.append((f"情绪: {emo_display}{conf_str}", self.C_YELLOW))

        # 第4行：注意力（绿色/红色）
        focus = ctx.get("focus")
        if focus is not None:
            text = f"专注: {'是' if focus else '否'}"
            color = self.C_GREEN if focus else self.C_RED
            lines.append((text, color))

        # 第5行：俯仰角 Pitch（橙色）
        pitch = ctx.get("pitch")
        if pitch is not None:
            lines.append((f"俯仰角: {pitch:+.1f}°", self.C_ORANGE))

        # 第6行：偏航角 Yaw（橙色）
        yaw = ctx.get("yaw")
        if yaw is not None:
            lines.append((f"偏航角: {yaw:+.1f}°", self.C_ORANGE))

        return lines


# ======================== 主客户端类 ========================
class RealtimeCameraClient:
    """实时摄像头检测客户端"""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.camera: Optional[cv2.VideoCapture] = None
        self.latest_result: dict[str, Any] = {}
        self.stop_requested = False
        self.connection_status = "初始化"
        self.last_print_time = 0.0
        self.sent_frames = 0
        self.token_verified = False
        self.had_successful_session = False
        self.consecutive_failures = 0

        # 当前 AIE：start_detection 会自动创建录制任务。
        self.recording_task_id: Optional[str] = None
        self.recording_active = False
        self.recording_finalizing = False
        self.latest_stage_result: Optional[dict[str, Any]] = None
        self.stage_completed_count = 0
        self.recording_finished_event: Optional[asyncio.Event] = None

        self.camera_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="camera"
        )

        # 预览渲染器（带中文字体支持）
        font = _find_chinese_font(22) if config.show_preview else None
        self.renderer = PreviewRenderer(config.width, config.height, font)

        self.frame_times: list[float] = []  # 存储墙钟时间戳（非处理耗时）
        self.start_time = time.time()

    # ---------- 摄像头 ----------
    def open_camera(self) -> None:
        backend = _camera_backend(self.config.camera_backend)
        api_pref = backend if backend is not None else cv2.CAP_ANY

        self.camera = cv2.VideoCapture(self.config.camera_index, api_pref)
        if not self.camera.isOpened():
            raise CameraError(f"无法打开摄像头 {self.config.camera_index}")

        self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if self.config.use_mjpeg:
            self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.camera.set(cv2.CAP_PROP_FPS, self.config.fps)

        for _ in range(5):
            self.camera.read()

        w = int(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.camera.get(cv2.CAP_PROP_FPS)
        self._log("info", f"摄像头已打开: {w}x{h} @ {actual_fps:.0f}fps")

    def release(self) -> None:
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        cv2.destroyAllWindows()
        self.camera_executor.shutdown(wait=False)

    # ---------- 日志 ----------
    def _log(self, level: str, msg: str, **kw) -> None:
        print(json.dumps({"type": level, "message": msg, **kw}, ensure_ascii=False), flush=True)

    # ---------- 渲染上下文 ----------
    def _build_render_ctx(self) -> dict[str, Any]:
        fps = self._get_real_fps()
        ctx: dict[str, Any] = {
            "fps": fps,
            "status": self.connection_status,
            "sent": self.sent_frames,
            "clock": self._get_clock_text(),
            "face_detected": True,
            "paused": False,  # 暂停状态
        }
        if self.latest_result:
            d = compact_result(self.latest_result)
            ctx.update({
                "hr": d.get("hr"),
                "emo": d.get("emo"),
                "emo_conf": d.get("emo_conf"),
                "focus": d.get("focus"),
                "blink": d.get("blink"),
                "eye": d.get("eye"),
                "pitch": d.get("pitch"),
                "yaw": d.get("yaw"),
                "face_detected": d.get("face", True),
            })
        # 连续流式模式不再因无人脸暂停发送。
        ctx["paused"] = False
        return ctx

    # ---------- 预览/编码 ----------
    def _draw_preview(self, frame: np.ndarray) -> np.ndarray:
        if not self.config.show_preview:
            return frame
        return self.renderer.render(frame, self._build_render_ctx())

    def _get_real_fps(self) -> float:
        """计算实时帧率（基于墙钟时间，包含 sleep 等待）"""
        if len(self.frame_times) < 2:
            return 0.0
        recent = self.frame_times[-10:]
        if len(recent) < 2:
            return 0.0
        elapsed = recent[-1] - recent[0]
        if elapsed <= 0:
            return 0.0
        return (len(recent) - 1) / elapsed

    def _get_clock_text(self) -> str:
        """获取当前系统时间字符串，每秒更新一次（缓存避免每帧重复格式化）"""
        now = time.localtime()
        current_second = now.tm_sec
        # 用分钟+秒做key，确保每秒最多格式化一次
        cache_key = (now.tm_min, current_second)
        if not hasattr(self, '_clock_cache') or self._clock_cache[0] != cache_key:
            text = time.strftime("%Y-%m-%d %H:%M:%S", now)
            self._clock_cache = (cache_key, text)
        return self._clock_cache[1]

    def _encode_frame(self, frame: np.ndarray) -> Optional[bytes]:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.config.jpeg_quality]
        ret, buf = cv2.imencode('.jpg', frame, params)
        return buf.tobytes() if ret else None

    async def _read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        if self.camera is None:
            return False, None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.camera_executor, self.camera.read)

    # ---------- WebSocket 接收 ----------
    async def _receive_messages(self, ws: Any) -> None:
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                t = data.get("type")

                if t == "connected":
                    self.connection_status = "已连接"
                    self._log("info", f"服务器连接成功，目标帧率: {data.get('target_fps', 'N/A')}")

                elif t == "result":
                    self.latest_result = data
                    self.had_successful_session = True

                    # 当前服务端会在普通 result 中同步录制/阶段统计状态。
                    if data.get("recordingTaskId"):
                        self.recording_task_id = str(data.get("recordingTaskId"))
                    if data.get("recordingActive") is not None:
                        self.recording_active = bool(data.get("recordingActive"))
                    if data.get("recordingFinalizing") is not None:
                        self.recording_finalizing = bool(data.get("recordingFinalizing"))
                    if data.get("stageCompletedCount") is not None:
                        try:
                            self.stage_completed_count = int(data.get("stageCompletedCount") or 0)
                        except Exception:
                            pass
                    if isinstance(data.get("latestStageResult"), dict):
                        self.latest_stage_result = data.get("latestStageResult")

                    if self.config.print_mode == "none":
                        continue
                    now = time.monotonic()
                    if now - self.last_print_time < self.config.print_interval:
                        continue
                    self.last_print_time = now

                    output = compact_result(data) if self.config.print_mode == "summary" else dict(data)
                    if self.config.print_mode == "full" and not self.config.include_landmarks:
                        output.pop("landmarks", None)

                    print(json.dumps(output, ensure_ascii=False), flush=True)

                elif t == "recording_started":
                    self.recording_task_id = str(data.get("taskId") or "") or None
                    self.recording_active = True
                    self.recording_finalizing = False
                    self.stage_completed_count = 0
                    self.latest_stage_result = None
                    self._log(
                        "info",
                        "start_detection 已自动开始记录"
                        + (f"，taskId={self.recording_task_id}" if self.recording_task_id else ""),
                    )

                elif t == "stage_result":
                    stage = data.get("stageResult")
                    if isinstance(stage, dict):
                        self.latest_stage_result = stage
                        try:
                            self.stage_completed_count = max(
                                self.stage_completed_count,
                                int(stage.get("index", 0) or 0),
                            )
                        except Exception:
                            pass
                        self._log(
                            "stage_result",
                            f"阶段统计完成（检测继续）: {stage.get('window', '-')}",
                            taskId=data.get("taskId") or self.recording_task_id,
                            stageResult=stage,
                        )

                elif t == "recording_finalizing":
                    self.recording_active = False
                    self.recording_finalizing = True
                    if data.get("taskId"):
                        self.recording_task_id = str(data.get("taskId"))
                    self._log(
                        "info",
                        "检测结束，服务端正在汇总最终结果"
                        + (f"，taskId={self.recording_task_id}" if self.recording_task_id else ""),
                    )

                elif t == "recording_result":
                    self.recording_active = False
                    self.recording_finalizing = False
                    if data.get("taskId"):
                        self.recording_task_id = str(data.get("taskId"))
                    game_data = data.get("gameData") or {}
                    stages = game_data.get("stageResults") or []
                    self.stage_completed_count = len(stages)
                    if stages and isinstance(stages[-1], dict):
                        self.latest_stage_result = stages[-1]
                    if self.stop_requested:
                        self._log(
                            "recording_result",
                            "用户主动结束检测，最终报告已返回",
                            taskId=self.recording_task_id,
                            stageCount=len(stages),
                            gameData=game_data,
                        )
                        if self.recording_finished_event is not None:
                            self.recording_finished_event.set()
                    else:
                        # 正常流式运行中不应该因为一分钟阶段结果而结束。
                        # 如果服务端意外返回 recording_result，只记录告警，不主动退出客户端。
                        self._log(
                            "warning",
                            "运行中收到 recording_result；客户端不会退出，将继续保持实时检测",
                            taskId=self.recording_task_id,
                            stageCount=len(stages),
                        )

                elif t == "error":
                    code = str(data.get("code", "SERVER_ERROR"))
                    msg = str(data.get("message", ""))
                    self._log("error", f"[{code}] {msg}")
                    if code == "TOKEN_INVALID":
                        raise TokenInvalid(msg)
                    # 结束阶段如果 stop_detection/stop_recording 恰遇到服务端已完成，
                    # 不应把整个客户端当成认证失败。
                    if code in {"NO_ACTIVE_RECORDING"}:
                        self.recording_active = False
                        self.recording_finalizing = False
                        if self.recording_finished_event is not None:
                            self.recording_finished_event.set()
                        continue
                    raise RuntimeError(msg)

                elif t == "control_ack":
                    action = data.get("action")
                    if data.get("accepted"):
                        if data.get("taskId"):
                            self.recording_task_id = str(data.get("taskId"))
                        if data.get("autoRecording"):
                            self.recording_active = True
                        self._log(
                            "info",
                            f"指令确认: {action}"
                            + (f"，taskId={self.recording_task_id}" if self.recording_task_id else ""),
                        )
        except ConnectionClosed as e:
            if e.code in (1005, 1008):
                raise TokenInvalid("token 无效或已过期")
            raise

    # ---------- WebSocket 发送 ----------
    async def _send_frames(self, ws: Any) -> None:
        await ws.send(json.dumps({"type": "control", "action": "start_detection"}))
        self._log("info", "开始发送图像帧...")

        target_fps = max(FPS_MIN, min(FPS_MAX, self.config.fps))
        frame_interval = 1.0 / target_fps
        loop = asyncio.get_running_loop()

        # 用墙钟时间戳追踪真实帧率
        self.frame_times.clear()
        self.frame_times.append(time.perf_counter())

        # 连续流式模式：
        # 不再根据“无人脸”暂停发送。即使当前帧无人脸，也持续把摄像头帧发送给服务端，
        # 这样服务端才能继续检测，并在人物重新进入画面后自动恢复有效结果。
        self._paused = False

        while not self.stop_requested:
            t0 = time.perf_counter()

            ok, frame = await self._read_frame()
            if not ok or frame is None:
                self.consecutive_failures += 1
                if self.consecutive_failures > 50:
                    raise CameraError("摄像头连续读取失败")
                await asyncio.sleep(min(0.1 * (1.5 ** self.consecutive_failures), 2.0))
                continue
            self.consecutive_failures = 0

            # ---- 连续流式模式 ----
            # 人脸是否存在只影响服务端返回的 frame_valid / face_detected 等结果，
            # 不再影响客户端是否发送下一帧。
            # 这样即使短暂离开画面，人物回来后也能自动恢复检测，不会形成客户端自锁。

            # ---- 预览始终更新 ----
            preview_future = None
            if self.config.show_preview:
                # 在画面上叠加"已暂停"提示
                display_frame = frame.copy()
                preview_future = loop.run_in_executor(
                    self.camera_executor, self._draw_preview, display_frame
                )

            # ---- 始终编码并发送 ----
            # 只要用户没有主动退出，就保持连续视频流。
            encode_future = loop.run_in_executor(
                self.camera_executor,
                self._encode_frame,
                frame,
            )
            jpeg = await encode_future
            if jpeg:
                try:
                    await ws.send(jpeg)
                    self.sent_frames += 1
                except ConnectionClosed:
                    break

            # ---- 显示预览 ----
            if preview_future:
                try:
                    preview = await asyncio.wait_for(preview_future, timeout=0.008)
                    if preview is not None:
                        cv2.imshow("AIE 实时检测", preview)
                except (asyncio.TimeoutError, Exception):
                    pass

            # 键盘
            if self.config.show_preview and self.sent_frames % 5 == 0:
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    self.stop_requested = True
                    break
                elif key == ord("c"):
                    # 当前协议 start_detection 会自动进入记录状态。
                    # 为保证连续流式检测，运行中不触发会中断记录状态的重新校准。
                    self._log(
                        "warning",
                        "连续流式检测中不执行重新校准；如需重新校准，请先退出后重新启动客户端",
                    )

            # 记录墙钟时间戳
            now_wall = time.perf_counter()
            self.frame_times.append(now_wall)
            if len(self.frame_times) > 30:
                self.frame_times.pop(0)

            # 精确帧率控制
            elapsed = now_wall - t0
            sleep_needed = frame_interval - elapsed
            if sleep_needed > 0.001:
                await asyncio.sleep(sleep_needed)

        # 当前 AIE 中 start_detection 会自动开始记录。
        # 正常退出必须先停止记录并等待最终汇总，再停止检测/关闭 WebSocket；
        # 否则服务端会拒绝 stop_detection，且最终 result_json 可能来不及生成。
        try:
            if self.recording_active or self.recording_finalizing or self.recording_task_id:
                if self.recording_active:
                    self._log(
                        "info",
                        "正在停止记录并等待最终结果"
                        + (f"，taskId={self.recording_task_id}" if self.recording_task_id else ""),
                    )
                    await ws.send(json.dumps({
                        "type": "control",
                        "action": "stop_recording",
                    }))

                if self.recording_finished_event is not None:
                    try:
                        await asyncio.wait_for(
                            self.recording_finished_event.wait(),
                            timeout=120.0,
                        )
                    except asyncio.TimeoutError:
                        self._log(
                            "warning",
                            "等待 recording_result 超过120秒，将结束 WebSocket；"
                            "可稍后使用 taskId 调用 /result 查询",
                            taskId=self.recording_task_id,
                        )

            await ws.send(json.dumps({
                "type": "control",
                "action": "stop_detection",
            }))
            await asyncio.sleep(0.1)
        except ConnectionClosed:
            pass

    # ---------- 单次连接 ----------
    async def _run_connection(self, token: str) -> None:
        self.connection_status = "连接中"
        self._log("info", f"正在连接: {self.config.server_url}")

        async with websockets.connect(
            self.config.server_url,
            max_size=None, max_queue=64,
            ping_interval=20, ping_timeout=20,
            open_timeout=10, close_timeout=5,
            compression=None,
        ) as ws:
            if not self.token_verified:
                self._log("info", "已发送 token，等待验证...")

            await ws.send(json.dumps({"type": "auth", "token": token}))

            try:
                await asyncio.sleep(0.2)
            except ConnectionClosed as e:
                if e.code in (1005, 1008):
                    raise TokenInvalid("token 无效或已过期")
                raise

            if not self.token_verified:
                self._log("info", "token 验证通过")
                self.token_verified = True

            self.connection_status = "已认证"

            # 每条 WebSocket 连接对应一条自动录制任务。
            self.recording_task_id = None
            self.recording_active = False
            self.recording_finalizing = False
            self.latest_stage_result = None
            self.stage_completed_count = 0
            self.recording_finished_event = asyncio.Event()

            recv_task = asyncio.create_task(self._receive_messages(ws))
            send_task = asyncio.create_task(self._send_frames(ws))

            done, pending = await asyncio.wait(
                {recv_task, send_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                if t.exception():
                    raise t.exception()

    # ---------- 主循环 ----------
    async def run(self) -> None:
        token = self.config.token
        if not token:
            try:
                self._log("info", "正在通过 AK/SK 获取 Token...")
                token = await fetch_token(self.config)
                self._log("info", "Token 获取成功")
            except TokenFetchError as e:
                self._log("error", str(e))
                sys.exit(2)

        try:
            self.open_camera()
        except CameraError as e:
            self._log("error", str(e))
            sys.exit(3)

        retry = 0
        try:
            while not self.stop_requested:
                try:
                    await self._run_connection(token)
                    if not self.stop_requested:
                        raise RuntimeError("连接已断开")
                except TokenInvalid:
                    raise
                except KeyboardInterrupt:
                    self.stop_requested = True
                except Exception as e:
                    if self.stop_requested:
                        break

                    # 旧逻辑只要曾成功收到过一次 result，后续任何断线都会直接退出，
                    # 实际上把 AUTO_RECONNECT 变成了“仅首次连接失败时才重连”。
                    # 连续流式模式下，意外断线必须继续重连。
                    if not self.config.auto_reconnect:
                        raise

                    # 如果此前已经有过正常检测结果，说明网络/服务曾经是可用的，
                    # 将重试计数重新从 1 开始，避免长时间运行后一次偶发断线就永久退出。
                    if self.had_successful_session:
                        retry = 0

                    if retry >= self.config.max_retries:
                        raise

                    retry += 1
                    delay = min(
                        self.config.retry_delay * (1.5 ** (retry - 1)),
                        30.0,
                    )
                    self.connection_status = f"重试 {retry}/{self.config.max_retries}"
                    self._log(
                        "warning",
                        f"实时连接意外中断：{e}；{delay:.1f}s 后第 {retry} 次重连...",
                    )
                    await asyncio.sleep(delay)
        finally:
            self.release()
            runtime = time.time() - self.start_time
            self._log("info", f"运行结束，时长: {runtime:.1f}s，发送: {self.sent_frames} 帧")


# ======================== CLI ========================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AIE 云端实时摄像头检测客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  %(prog)s --ak AK --sk SK\n  %(prog)s --token TOKEN --fps 30\n",
    )

    g = p.add_argument_group("认证")
    g.add_argument("--url", default=SERVER_URL)
    g.add_argument("--token-url", default=TOKEN_SERVER_URL, help="默认使用 AIE /auth/token，以便同步登记 WebSocket Token")
    g.add_argument("--ak", default=os.environ.get("AIE_AK", ""))
    g.add_argument("--sk", default=os.environ.get("AIE_SK", ""))
    g.add_argument("--token", default=os.environ.get("AIE_TOKEN", ""))

    g = p.add_argument_group("摄像头")
    g.add_argument("--camera", type=int, default=CAMERA_INDEX)
    g.add_argument("--backend", choices=["auto", "dshow", "msmf", "v4l2"], default=CAMERA_BACKEND)
    g.add_argument("--width", type=int, default=FRAME_WIDTH)
    g.add_argument("--height", type=int, default=FRAME_HEIGHT)
    g.add_argument("--fps", type=float, default=TARGET_FPS,
                   help=f"目标帧率（{FPS_MIN}-{FPS_MAX}）")
    g.add_argument("--quality", type=int, default=JPEG_QUALITY, choices=range(1, 101), metavar="1-100")
    g.add_argument("--no-mjpeg", action="store_true")
    g.add_argument("--no-preview", action="store_true")

    g = p.add_argument_group("输出")
    g.add_argument("--print-mode", choices=["summary", "full", "none"], default=PRINT_MODE)
    g.add_argument("--print-interval", type=float, default=PRINT_INTERVAL)
    g.add_argument("--include-landmarks", action="store_true")

    g = p.add_argument_group("重连")
    g.add_argument("--no-reconnect", action="store_true")
    g.add_argument("--max-retries", type=int, default=MAX_RETRIES)
    g.add_argument("--retry-delay", type=float, default=RETRY_DELAY)

    return p


def config_from_args(args: argparse.Namespace) -> ClientConfig:
    # 帧率限制在 25-30
    fps = max(FPS_MIN, min(FPS_MAX, args.fps)) if hasattr(args, 'fps') else TARGET_FPS

    return ClientConfig(
        server_url=args.url,
        token_server_url=args.token_url,
        ak=args.ak, sk=args.sk, token=args.token,
        camera_index=args.camera, camera_backend=args.backend,
        width=max(1, args.width), height=max(1, args.height),
        fps=fps,
        jpeg_quality=args.quality,
        use_mjpeg=not args.no_mjpeg,
        show_preview=not args.no_preview,
        print_mode=args.print_mode,
        print_interval=max(0.0, args.print_interval),
        include_landmarks=args.include_landmarks,
        auto_reconnect=not args.no_reconnect,
        max_retries=max(0, args.max_retries),
        retry_delay=max(0.1, args.retry_delay),
    )


def main() -> int:
    args = build_parser().parse_args()
    cfg = config_from_args(args)

    # ---- 弹窗获取 AK/SK（如果未通过参数/环境变量提供）----
    # 优先级：--token > --ak/--sk > 环境变量 > 弹窗
    if not cfg.token and (not cfg.ak or not cfg.sk):
        print(json.dumps({
            "type": "info", "message": "未检测到 AK/SK，弹出输入对话框..."
        }, ensure_ascii=False), flush=True)

        # 如果预览窗口会占用摄像头，先不打开
        dialog_result = show_credential_dialog(cfg.ak, cfg.sk)

        if dialog_result is None:
            # 用户取消
            print(json.dumps({
                "type": "error", "code": "CANCELLED",
                "message": "用户取消了认证信息输入"
            }, ensure_ascii=False), file=sys.stderr, flush=True)
            return 1

        ak, sk = dialog_result
        cfg.ak = ak
        cfg.sk = sk

        # 重新打印（覆盖掉敏感信息不要打印）
        print(json.dumps({
            "type": "info", "message": "AK/SK 已输入，正在获取 Token..."
        }, ensure_ascii=False), flush=True)

    try:
        asyncio.run(RealtimeCameraClient(cfg).run())
        return 0
    except KeyboardInterrupt:
        return 0
    except TokenInvalid as e:
        print(json.dumps({"type": "error", "code": "TOKEN_INVALID", "message": str(e)},
                        ensure_ascii=False), file=sys.stderr, flush=True)
        return 2
    except CameraError as e:
        print(json.dumps({"type": "error", "code": "CAMERA_ERROR", "message": str(e)},
                        ensure_ascii=False), file=sys.stderr, flush=True)
        return 3
    except Exception as e:
        print(json.dumps({"type": "error", "code": "UNKNOWN", "message": str(e),
                          "traceback": traceback.format_exc()},
                        ensure_ascii=False), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
