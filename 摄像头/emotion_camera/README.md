# 本地 AIe 鉴权代理

云端实时接口要求先使用 AK/SK 获取临时 Token。浏览器不读取任何密钥，只连接本机代理；代理负责 HTTPS 取 Token、以 WSS 连接云端并转发 JPEG 与结果 JSON。

双击 `start.command` 启动。服务地址为 `http://127.0.0.1:8765`，正式摄像头模块使用 `ws://127.0.0.1:8765/ws`。

表情判断算法位于上级目录的 `frontend/emotion-tracker.js`，保持参考版本的基线、个人噪声、AU 比例、原型匹配和按有效时长总结逻辑。
