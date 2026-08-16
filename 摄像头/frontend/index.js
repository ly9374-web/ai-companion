import { AieWebSocketClient } from './aie-websocket-client.js';
import { EmotionDisplay } from './emotion-display.js';
import { EmotionTracker } from './emotion-tracker.js';
import { JpegFramePump } from './jpeg-frame-pump.js';

export function createCameraEmotionFeature(config = {}) {
  const display = new EmotionDisplay();
  let restoreWebSocketSend = null;
  let reportedFirstResult = false;
  let reportedFirstRejectedResult = false;
  const reportDiagnostic = (event, details = {}) => {
    void fetch('/optional-feature/diagnostics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, details }),
    }).catch(() => {});
  };
  const tracker = new EmotionTracker({
    maxResultAgeMs: config.max_result_age_ms,
    onLiveEmotion: (emotions) => display.setLive(emotions),
    onAuDeltas: (items) => display.setAuDeltas(items),
    onCalibrationState: (detail) => display.setCalibrationState(detail),
  });
  const client = new AieWebSocketClient({
    serverUrl: config.server_url || 'ws://127.0.0.1:8765/ws',
    onResult: (result) => {
      const accepted = tracker.updateResult(result);
      if (accepted && !reportedFirstResult) {
        reportedFirstResult = true;
        reportDiagnostic('first_result_accepted', {
          top_level_keys: Object.keys(result || {}).slice(0, 30),
          data_keys: Object.keys(result?.data || {}).slice(0, 30),
          result_keys: Object.keys(result?.result || {}).slice(0, 30),
        });
      } else if (!accepted && !reportedFirstRejectedResult) {
        reportedFirstRejectedResult = true;
        reportDiagnostic('first_result_rejected', {
          top_level_keys: Object.keys(result || {}).slice(0, 30),
          data_keys: Object.keys(result?.data || {}).slice(0, 30),
          result_keys: Object.keys(result?.result || {}).slice(0, 30),
        });
      }
    },
    onConnectionLost: (message) => {
      tracker.clearLatestResult();
      if (message) {
        tracker.connectionFailed(message);
      }
    },
    onStatus: (event, details) => {
      reportDiagnostic(event, details);
      if (event === 'authenticated') {
        tracker.connectionRestored();
      } else if (event === 'service_error') {
        const message = String(details?.message || 'AIe 鉴权或连接失败');
        tracker.connectionFailed(
          message.includes('token') || message.includes('鉴权')
            ? 'AIe 鉴权失败，请检查 emotion_camera/.env'
            : `AIe 服务错误：${message}`,
        );
      } else if (event === 'websocket_closed' && details?.reconnecting) {
        const reason = String(details?.reason || '');
        tracker.connectionFailed(
          reason.includes('Invalid token')
            ? 'AIe 鉴权失败，请检查 emotion_camera/.env'
            : '表情识别连接中断，正在重连…',
        );
      } else if (event === 'websocket_error') {
        tracker.connectionFailed('表情识别连接异常，正在重连…');
      }
    },
  });
  const framePump = new JpegFramePump({
    fps: config.fps,
    jpegQuality: config.jpeg_quality,
    maxBufferedBytes: config.max_buffered_bytes,
  });

  const observeInterruptSignals = () => {
    if (restoreWebSocketSend) return;
    const originalSend = WebSocket.prototype.send;
    const observedSend = function observedCameraMessage(data) {
      try {
        if (typeof data === 'string') {
          const message = JSON.parse(data);
          if (message?.type === 'interrupt-signal') tracker.startWindow();
          if (message?.type === 'set-debug-mode' && typeof message.enabled === 'boolean') {
            display.setDebugMode(message.enabled);
          }
        }
      } catch (_error) {
        // Non-JSON WebSocket payloads are unrelated to the conversation state.
      }
      return originalSend.call(this, data);
    };
    WebSocket.prototype.send = observedSend;
    restoreWebSocketSend = () => {
      if (WebSocket.prototype.send === observedSend) {
        WebSocket.prototype.send = originalSend;
      }
      restoreWebSocketSend = null;
    };
  };

  const stopObservingInterruptSignals = () => {
    restoreWebSocketSend?.();
  };

  // Observe the session debug switch even while the camera itself is closed.
  // This preserves a debug choice made before the user opens the camera.
  observeInterruptSignals();

  return {
    async start(stream) {
      display.clear();
      display.show();
      client.start();
      try {
        await framePump.start(
          stream,
          (blob, maxBufferedBytes) => client.sendFrame(blob, maxBufferedBytes),
        );
        tracker.startCalibration();
      } catch (error) {
        display.clear();
        display.hide();
        client.stop();
        throw error;
      }
    },
    stop() {
      framePump.stop();
      client.stop();
      tracker.reset();
      display.clear();
      display.hide();
    },
    startWindow() {
      display.setFinal(null);
      tracker.startWindow();
    },
    pauseWindow() {
      tracker.pauseWindow();
    },
    // Kept for compatibility with an already-built host frontend. Resuming an
    // assistant-paused window starts the next user turn with an empty summary.
    resumeWindow() {
      display.setFinal(null);
      tracker.startWindow();
    },
    consumeWindow() {
      const aggregate = tracker.consumeWindow();
      display.setFinalSequence(aggregate?.emotion_sequence || null);
      reportDiagnostic(aggregate ? 'aggregate_ready' : 'aggregate_empty', aggregate || {});
      return aggregate;
    },
    destroy() {
      framePump.destroy();
      client.stop();
      tracker.destroy();
      stopObservingInterruptSignals();
      display.destroy();
    },
  };
}
