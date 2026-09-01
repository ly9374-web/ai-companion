import { AieWebSocketClient } from './aie-websocket-client.js';
import { EmotionDisplay } from './emotion-display.js';
import { EmotionTracker } from './emotion-tracker.js';
import { JpegFramePump } from './jpeg-frame-pump.js';
import {
  DEFAULT_RUNTIME_SETTINGS,
  loadProfileStore,
  normalizeProfileDisplayName,
  normalizeProfileKey,
  persistProfileStore,
  sanitizeRuntimeSettings,
  validPersonalProfile,
} from './emotion-profile-store.js';

export function createCameraEmotionFeature(config = {}) {
  const display = new EmotionDisplay();
  let restoreWebSocketSend = null;
  let reportedFirstResult = false;
  let reportedFirstRejectedResult = false;
  let reportedHeartRateRaw = false;
  let reportedFaceHeartRate = false;
  let reportedStageResult = false;
  let lastReportedHeartRateValue = { bpm: null, at: 0 };
  const reportDiagnostic = (event, details = {}) => {
    void fetch('/optional-feature/diagnostics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, details }),
    }).catch(() => {});
  };

  // ===== 个人基线档案与识别参数（localStorage 持久化） =====
  let running = false;
  let activeProfile = null;
  let currentSettings = { ...DEFAULT_RUNTIME_SETTINGS };
  let activeAssistantSegment = null;
  let assistantResponseActive = false;
  let assistantSegments = [];
  let assistantHeartRateTotal = 0;
  let assistantHeartRateSamples = 0;
  let completedAssistantResponse = null;
  const profileStateListeners = new Set();
  const calibrationListeners = new Set();

  // 创建时预载上次使用的档案及其参数：下次开启摄像头默认使用上次设置。
  {
    const initialStore = loadProfileStore();
    currentSettings = { ...initialStore.genericSettings };
    if (initialStore.lastUsedKey) {
      const profile = initialStore.profiles[initialStore.lastUsedKey];
      if (validPersonalProfile(profile)) {
        activeProfile = profile;
        currentSettings = { ...profile.settings };
      }
    }
  }

  const getProfileState = () => {
    const store = loadProfileStore();
    return {
      activeName: activeProfile?.displayName || '',
      lastUsedName: store.lastUsedKey
        ? store.profiles[store.lastUsedKey]?.displayName || ''
        : '',
      profileNames: Object.values(store.profiles).map((profile) => profile.displayName),
      calibrationActive: tracker.hasCalibrationSession(),
      settings: { ...currentSettings },
    };
  };

  const emitProfileState = () => {
    const state = getProfileState();
    for (const listener of profileStateListeners) {
      try {
        listener(state);
      } catch (_error) {
        // 订阅方异常不能影响运行时。
      }
    }
  };

  const emitCalibration = (progress) => {
    for (const listener of calibrationListeners) {
      try {
        listener(progress);
      } catch (_error) {
        // 订阅方异常不能影响运行时。
      }
    }
  };

  const persistSettings = () => {
    const store = loadProfileStore();
    if (activeProfile) {
      activeProfile.settings = { ...currentSettings };
      const stored = store.profiles[activeProfile.key];
      if (stored) stored.settings = { ...currentSettings };
    } else {
      store.genericSettings = { ...currentSettings };
    }
    persistProfileStore(store);
  };

  // 摄像头运行中时把当前档案/通用模式应用到 tracker。
  const applyTrackerProfile = () => {
    if (!running) return;
    if (activeProfile) tracker.activateProfile(activeProfile);
    else tracker.useGenericProfile();
  };

  const activateProfileObject = (profile) => {
    activeProfile = profile;
    currentSettings = { ...profile.settings };
    tracker.applySettings(currentSettings);
    applyTrackerProfile();
    emitProfileState();
  };

  const tracker = new EmotionTracker({
    maxResultAgeMs: config.max_result_age_ms,
    settings: currentSettings,
    onLiveEmotion: (emotions) => display.setLive(emotions),
    onAuDeltas: (items) => display.setAuDeltas(items),
    onCalibrationState: (detail) => display.setCalibrationState(detail),
    onHeartRate: (bpm) => display.setHeartRate(bpm),
    onSegmentState: (state) => display.setSegmentState(state),
    onCalibrationProgress: emitCalibration,
    onCalibrationComplete: (draft) => {
      // 录制完成：补全元数据后入库并立即启用。
      const store = loadProfileStore();
      const now = new Date().toISOString();
      const existing = store.profiles[draft.key];
      const profile = {
        ...draft,
        createdAt: existing?.createdAt || now,
        updatedAt: now,
        settings: { ...currentSettings },
      };
      store.profiles[profile.key] = profile;
      store.lastUsedKey = profile.key;
      persistProfileStore(store);
      activeProfile = profile;
      currentSettings = { ...profile.settings };
      tracker.applySettings(currentSettings);
      applyTrackerProfile();
      emitProfileState();
    },
  });
  const client = new AieWebSocketClient({
    serverUrl: config.server_url || 'ws://127.0.0.1:8765/ws',
    onResult: (result) => {
      // 临时诊断：追踪心率值变化与云端 60 秒阶段录制的进度。
      const hrGroup = result?.heart_rate;
      if (hrGroup && typeof hrGroup === 'object' && !reportedHeartRateRaw) {
        reportedHeartRateRaw = true;
        reportDiagnostic('heart_rate_raw_first', {
          heart_rate: Number(hrGroup.heart_rate) || 0,
          hrv: Number(hrGroup.hrv) || 0,
          heart_rate_is_valid: hrGroup.heart_rate_is_valid === undefined ? 'absent' : hrGroup.heart_rate_is_valid,
          face_detected: result?.face?.face_detected === undefined ? 'absent' : result?.face?.face_detected,
          stage_status: String(result?.stageStatus || ''),
        });
      }
      if (hrGroup && typeof hrGroup === 'object' && result?.face?.face_detected === true && !reportedFaceHeartRate) {
        reportedFaceHeartRate = true;
        reportDiagnostic('heart_rate_with_face_first', {
          heart_rate: Number(hrGroup.heart_rate) || 0,
          hrv: Number(hrGroup.hrv) || 0,
          stage_status: String(result?.stageStatus || ''),
          recording_active: result?.recordingActive === true,
        });
      }
      if (hrGroup && typeof hrGroup === 'object') {
        const bpmValue = Number(hrGroup.heart_rate);
        const nowMs = Date.now();
        if (
          Number.isFinite(bpmValue)
          && bpmValue !== lastReportedHeartRateValue.bpm
          && nowMs - lastReportedHeartRateValue.at >= 5000
        ) {
          lastReportedHeartRateValue = { bpm: bpmValue, at: nowMs };
          reportDiagnostic('heart_rate_value', {
            heart_rate: bpmValue,
            stage_status: String(result?.stageStatus || ''),
            stage_elapsed_seconds: Math.round(Number(result?.stageElapsedSeconds) || 0),
            next_stage_in_seconds: result?.nextStageInSeconds == null
              ? -1
              : Math.round(Number(result?.nextStageInSeconds)),
            stage_completed: Number(result?.stageCompletedCount) || 0,
          });
        }
      }
      if (
        !reportedStageResult
        && ((Number(result?.stageCompletedCount) || 0) > 0 || result?.latestStageResult)
      ) {
        reportedStageResult = true;
        reportDiagnostic('stage_result_first', {
          stage_completed: Number(result?.stageCompletedCount) || 0,
          stage_status: String(result?.stageStatus || ''),
          heart_rate: Number(result?.heart_rate?.heart_rate) || 0,
          latest_stage_result: JSON.stringify(result?.latestStageResult || {}).slice(0, 400),
        });
      }
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
          message.includes('502') || message.includes('503') || message.includes('504')
            ? '云端表情识别服务暂时不可用，正在自动重试…'
            : message.includes('token') || message.includes('鉴权')
              ? 'AIe 鉴权失败，请检查 emotion_camera/.env'
            : `AIe 服务错误：${message}`,
        );
      } else if (
        event === 'websocket_closed'
        && details?.reconnecting
        && !details?.service_error
      ) {
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
          if (message?.type === 'interrupt-signal' && !assistantResponseActive) {
            tracker.startWindow();
          }
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

  const resetAssistantResponse = () => {
    activeAssistantSegment = null;
    assistantSegments = [];
    assistantHeartRateTotal = 0;
    assistantHeartRateSamples = 0;
  };

  const shortenInterruptedText = (text, playbackRatio) => {
    const normalized = String(text || '').trim();
    const ratio = Number(playbackRatio);
    if (!normalized || !Number.isFinite(ratio) || ratio >= 0.995) return normalized;
    const length = Math.max(1, Math.round(normalized.length * Math.max(0, ratio)));
    const prefix = normalized.slice(0, length).trim();
    return prefix && prefix.length < normalized.length ? `${prefix}…` : prefix;
  };

  const finishActiveAssistantSegment = ({ interrupted = false, playbackRatio = 1 } = {}) => {
    if (!activeAssistantSegment) return;
    const segment = activeAssistantSegment;
    activeAssistantSegment = null;
    const aggregate = tracker.consumeWindow({
      thresholdMs: 300,
      maxEmotions: 1,
      heartRateMinimumSamples: 1,
    });
    const heartRate = aggregate?.heart_rate;
    if (heartRate && Number.isFinite(heartRate.avg_bpm) && Number.isFinite(heartRate.sample_count)) {
      assistantHeartRateTotal += heartRate.avg_bpm * heartRate.sample_count;
      assistantHeartRateSamples += heartRate.sample_count;
    }
    const group = aggregate?.emotion_durations?.[0];
    const emotion = group?.emotions?.find((value) => value !== 'neutral');
    if (emotion && Number(group?.duration_ms) >= 300) {
      assistantSegments.push({
        text: interrupted
          ? shortenInterruptedText(segment.text, playbackRatio)
          : String(segment.text || '').trim(),
        emotion,
        duration_ms: group.duration_ms,
        interrupted: interrupted === true,
      });
    }
  };

  const assistantResponseAggregate = () => {
    const result = {
      listening_segments: [...assistantSegments],
    };
    if (assistantHeartRateSamples >= 3) {
      result.assistant_heart_rate = {
        avg_bpm: Math.round(assistantHeartRateTotal / assistantHeartRateSamples),
        sample_count: assistantHeartRateSamples,
      };
    }
    return result.listening_segments.length || result.assistant_heart_rate ? result : null;
  };

  // Observe the session debug switch even while the camera itself is closed.
  // This preserves a debug choice made before the user opens the camera.
  observeInterruptSignals();

  return {
    async start(stream) {
      display.clear();
      display.show();
      client.start();
      running = true;
      try {
        await framePump.start(
          stream,
          (blob, maxBufferedBytes) => client.sendFrame(blob, maxBufferedBytes),
        );
        tracker.applySettings(currentSettings);
        if (activeProfile) tracker.startWithProfile(activeProfile);
        else tracker.startCalibration();
      } catch (error) {
        running = false;
        display.clear();
        display.hide();
        client.stop();
        throw error;
      }
    },
    stop() {
      running = false;
      framePump.stop();
      client.stop();
      tracker.reset();
      display.clear();
      display.hide();
      resetAssistantResponse();
      assistantResponseActive = false;
      completedAssistantResponse = null;
    },
    startWindow() {
      display.setFinalAggregate(null);
      tracker.startWindow();
    },
    pauseWindow() {
      tracker.pauseWindow();
    },
    // Kept for compatibility with an already-built host frontend. Resuming an
    // assistant-paused window starts the next user turn with an empty summary.
    resumeWindow() {
      display.setFinalAggregate(null);
      tracker.startWindow();
    },
    consumeWindow() {
      const aggregate = tracker.consumeWindow();
      const result = aggregate || completedAssistantResponse
        ? { ...(aggregate || {}), ...(completedAssistantResponse || {}) }
        : null;
      completedAssistantResponse = null;
      display.setFinalAggregate(result);
      reportDiagnostic(result ? 'aggregate_ready' : 'aggregate_empty', result || {});
      return result;
    },
    beginAssistantResponse() {
      tracker.pauseWindow();
      resetAssistantResponse();
      assistantResponseActive = true;
      completedAssistantResponse = null;
    },
    startAssistantSegment(text) {
      if (!assistantResponseActive) return;
      finishActiveAssistantSegment({ interrupted: true, playbackRatio: 1 });
      activeAssistantSegment = { text: String(text || '') };
      tracker.startWindow();
    },
    finishAssistantSegment(details = {}) {
      if (!assistantResponseActive) return;
      finishActiveAssistantSegment(details);
    },
    finishAssistantResponse() {
      if (!assistantResponseActive) {
        tracker.startWindow();
        return;
      }
      finishActiveAssistantSegment({ interrupted: true, playbackRatio: 1 });
      completedAssistantResponse = assistantResponseAggregate();
      display.setFinalAggregate(completedAssistantResponse);
      reportDiagnostic(
        completedAssistantResponse ? 'assistant_aggregate_ready' : 'assistant_aggregate_empty',
        completedAssistantResponse || {},
      );
      resetAssistantResponse();
      assistantResponseActive = false;
      tracker.startWindow();
    },
    consumeAssistantResponse() {
      const result = completedAssistantResponse;
      completedAssistantResponse = null;
      return result;
    },
    // 最近一次有效心率（5 秒内），供“按 j 带入心率”在发消息时读取。
    getLatestHeartRate() {
      return tracker.getLatestHeartRate();
    },
    destroy() {
      running = false;
      profileStateListeners.clear();
      calibrationListeners.clear();
      framePump.destroy();
      client.stop();
      tracker.destroy();
      stopObservingInterruptSignals();
      display.destroy();
    },

    // ===== 一眸：个人基线档案与参数 =====

    getProfileState,

    getSettings() {
      return { ...currentSettings };
    },

    subscribeProfileState(listener) {
      profileStateListeners.add(listener);
      return () => profileStateListeners.delete(listener);
    },

    subscribeCalibration(listener) {
      calibrationListeners.add(listener);
      // 立即回放当前步骤，便于订阅方（如设置抽屉重新打开）恢复校准 UI。
      const current = tracker.currentCalibrationStep();
      if (current) {
        try {
          listener(current);
        } catch (_error) {
          // 订阅方异常不能影响运行时。
        }
      }
      return () => calibrationListeners.delete(listener);
    },

    applySettings(partial) {
      currentSettings = sanitizeRuntimeSettings({
        ...currentSettings,
        ...(partial || {}),
      });
      tracker.applySettings(currentSettings);
      persistSettings();
      emitProfileState();
    },

    activateProfileByName(name) {
      const key = normalizeProfileKey(name);
      if (!key) return { ok: false, error: '请输入有效的名称。' };
      const store = loadProfileStore();
      const profile = store.profiles[key];
      if (!validPersonalProfile(profile)) return { ok: false, error: 'not_found' };
      store.lastUsedKey = key;
      persistProfileStore(store);
      activateProfileObject(profile);
      return { ok: true, profile };
    },

    beginPersonalCalibration(name) {
      const displayName = normalizeProfileDisplayName(name);
      const key = normalizeProfileKey(displayName);
      if (!key) return { ok: false, error: '请输入有效的名称。' };
      if (!running) return { ok: false, error: 'camera_off' };
      const error = tracker.beginPersonalCalibration(key, displayName);
      if (error) return { ok: false, error };
      return { ok: true };
    },

    captureCalibrationStep() {
      tracker.captureCalibrationStep();
    },

    cancelCalibration() {
      tracker.cancelPersonalCalibration();
    },

    useGenericProfile() {
      const store = loadProfileStore();
      store.lastUsedKey = '';
      persistProfileStore(store);
      activeProfile = null;
      currentSettings = { ...store.genericSettings };
      tracker.applySettings(currentSettings);
      applyTrackerProfile();
      emitProfileState();
    },

    deleteActiveProfile() {
      if (!activeProfile) return { ok: false, error: 'no_active_profile' };
      const store = loadProfileStore();
      delete store.profiles[activeProfile.key];
      if (store.lastUsedKey === activeProfile.key) store.lastUsedKey = '';
      persistProfileStore(store);
      activeProfile = null;
      currentSettings = { ...store.genericSettings };
      tracker.applySettings(currentSettings);
      applyTrackerProfile();
      emitProfileState();
      return { ok: true };
    },
  };
}
