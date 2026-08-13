export class AieWebSocketClient {
  constructor({ serverUrl, onResult, onConnectionLost, onStatus = () => {} }) {
    this.serverUrl = serverUrl;
    this.onResult = onResult;
    this.onConnectionLost = onConnectionLost;
    this.onStatus = onStatus;
    this.socket = null;
    this.running = false;
    this.reconnectTimer = null;
    this.retryCount = 0;
    this.reportedFirstMessage = false;
    this.ready = false;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  connect() {
    if (!this.running || this.socket) return;
    try {
      const socket = new WebSocket(this.serverUrl);
      socket.binaryType = 'arraybuffer';
      this.socket = socket;

      socket.onopen = () => {
        if (this.socket !== socket) return;
        this.retryCount = 0;
        this.ready = false;
        socket.cameraEmotionOpenedAt = Date.now();
        socket.cameraEmotionReceivedMessage = false;
        this.onStatus('websocket_open', {
          protocol: socket.protocol || '',
          extensions: socket.extensions || '',
        });
      };

      socket.onmessage = (event) => {
        if (typeof event.data !== 'string') return;
        socket.cameraEmotionReceivedMessage = true;
        try {
          const message = JSON.parse(event.data);
          if (!this.reportedFirstMessage) {
            this.reportedFirstMessage = true;
            this.onStatus('first_message_received', {
              type: String(message.type || ''),
              top_level_keys: Object.keys(message || {}).slice(0, 30),
            });
          }
          if (message.type === 'connected') {
            this.ready = true;
            socket.send(JSON.stringify({ type: 'control', action: 'start_detection' }));
            this.onStatus('authenticated', {});
          } else if (message.type === 'error') {
            this.ready = false;
            this.onStatus('service_error', {
              code: String(message.code || ''),
              message: String(message.message || '').slice(0, 300),
            });
            console.warn('[CameraEmotion] AIE 服务返回错误:', message.code, message.message);
            this.onConnectionLost(String(message.message || 'AIe 鉴权或连接失败'));
            if (message.code === 'PIPELINE_NOT_READY' || message.code === 'REALTIME_SESSION_BUSY') {
              socket.close();
            }
          } else {
            this.onResult(message);
          }
        } catch (error) {
          console.warn('[CameraEmotion] 无法解析 AIE 消息:', error);
        }
      };

      socket.onerror = () => {
        this.onStatus('websocket_error', {
          ready_state: socket.readyState,
          lifetime_ms: socket.cameraEmotionOpenedAt
            ? Date.now() - socket.cameraEmotionOpenedAt
            : 0,
        });
      };

      socket.onclose = (event) => {
        if (this.socket === socket) this.socket = null;
        this.ready = false;
        const reconnecting = this.running;
        this.onStatus('websocket_closed', {
          code: event.code,
          reason: String(event.reason || '').slice(0, 300),
          was_clean: event.wasClean,
          reconnecting,
          lifetime_ms: socket.cameraEmotionOpenedAt
            ? Date.now() - socket.cameraEmotionOpenedAt
            : 0,
          received_message: socket.cameraEmotionReceivedMessage === true,
        });
        this.onConnectionLost(reconnecting ? '表情识别连接中断，正在重连…' : null);
        this.scheduleReconnect();
      };
    } catch (error) {
      this.socket = null;
      console.warn('[CameraEmotion] 无法建立 AIE WebSocket:', error);
      this.onConnectionLost('无法建立表情识别连接，正在重连…');
      this.scheduleReconnect();
    }
  }

  scheduleReconnect() {
    if (!this.running || this.reconnectTimer) return;
    const delay = Math.min(2000 * (1.5 ** this.retryCount), 30000);
    this.retryCount += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  sendFrame(blob, maxBufferedBytes) {
    const socket = this.socket;
    if (!this.ready || !socket || socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > maxBufferedBytes) return;
    socket.send(blob);
  }

  stop() {
    this.running = false;
    this.ready = false;
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      if (socket.readyState === WebSocket.OPEN) {
        try {
          socket.send(JSON.stringify({ type: 'control', action: 'stop_detection' }));
        } catch (_error) {
          // Best-effort shutdown only.
        }
      }
      socket.close();
    }
    this.onConnectionLost(null);
  }
}
