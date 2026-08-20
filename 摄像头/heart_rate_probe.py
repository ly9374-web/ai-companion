#!/usr/bin/env python3
"""临时探针：持续发送真实人脸照片，观察 heart_rate 字段的出现时序。用完即删。"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

PROXY_WS = "ws://127.0.0.1:8765/ws"
FRAME_INTERVAL = 1 / 12
SECONDS = 75


async def probe(send_start_detection: bool) -> None:
    label = "B-发start_detection" if send_start_detection else "A-不发start_detection"
    print(f"\n===== [{label}] =====")
    frame = Path("/Users/jason/Desktop/ai陪伴/摄像头/probe_face.jpg").read_bytes()
    last_heart_rate_print = None
    try:
        async with websockets.connect(PROXY_WS, open_timeout=20) as socket:
            first = await asyncio.wait_for(socket.recv(), timeout=20)
            print("connected:", str(first)[:100])
            if send_start_detection:
                await socket.send(json.dumps({"type": "control", "action": "start_detection"}))
            end = asyncio.get_event_loop().time() + SECONDS
            result_count = 0
            while asyncio.get_event_loop().time() < end:
                await socket.send(frame)
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message.get("type") != "result":
                    print("non-result:", str(raw)[:200])
                    continue
                result_count += 1
                face = message.get("face") or {}
                hr = message.get("heart_rate")
                stage = {
                    "stageStatus": message.get("stageStatus"),
                    "stageElapsed": message.get("stageElapsedSeconds"),
                    "next": message.get("nextStageInSeconds"),
                    "completed": message.get("stageCompletedCount"),
                }
                marker = f"face_detected={face.get('face_detected')} stage={stage}"
                # 心率出现/变化时打印；stage 每 10 秒打印一次进度
                hr_repr = json.dumps(hr, ensure_ascii=False) if hr is not None else "null"
                if hr_repr != last_heart_rate_print:
                    last_heart_rate_print = hr_repr
                    print(f"[{result_count}] heart_rate={hr_repr} | {marker}")
                elif result_count % 120 == 0:
                    print(f"[{result_count}] heart_rate={hr_repr} | {marker}")
                await asyncio.sleep(FRAME_INTERVAL)
    except Exception as exc:  # noqa: BLE001
        print(f"probe failed: {exc}")


async def main() -> None:
    # 只跑不发 start_detection 的模式（与参考实现一致）
    await probe(send_start_detection=False)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
