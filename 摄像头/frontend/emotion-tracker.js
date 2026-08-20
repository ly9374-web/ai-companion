import { RAW_EMOTION_MODEL } from './emotion-model.js';
import { sanitizeRuntimeSettings } from './emotion-profile-store.js';

const BASELINE_COUNTDOWN_MS = 3000;
const BASELINE_WINDOW_MS = 1100;
const SMOOTHING_WINDOW_MS = 550;
const EXPRESSION_STABILITY_MS = 280;
const AMBIGUOUS_STABILITY_MS = 360;
const NEUTRAL_STABILITY_MS = 300;
const MINIMUM_TEMPLATE_SCALE = 0;
const MAXIMUM_RESIDUAL_RATIO = 1;
const MINIMUM_EFFECTIVE_AUS = 1;
const NOISE_MULTIPLIER = 2.5;
const MINIMUM_NOISE_FLOOR = 0.015;

// 个人基线录入：每种表情确认后采集约 1 秒中值。
const CALIBRATION_CAPTURE_MS = 1000;
const CALIBRATION_MINIMUM_FRAMES = 5;
const CALIBRATION_STEPS = Object.freeze([
  { label: '中性', prompt: '保持放松', kind: 'neutral' },
  { label: '悲伤', prompt: '做悲伤表情', kind: 'expression' },
  { label: '愤怒', prompt: '做愤怒表情', kind: 'expression' },
  { label: '惊讶', prompt: '做惊讶表情', kind: 'expression' },
  { label: '开心', prompt: '做开心表情', kind: 'expression' },
]);

// 心率窗口最少有效样本数（云端约 3 次/秒），不足则不参与 prompt 拼接。
const WINDOW_HEART_RATE_MINIMUM_SAMPLES = 3;

const INFERRED_EXPRESSION_LABELS = new Set(['愤怒', '开心', '悲伤', '惊讶']);
const MODEL_LABELS = Object.freeze({
  愤怒: 'angry',
  开心: 'happy',
  悲伤: 'sad',
  惊讶: 'surprise',
});
const EMOTION_THRESHOLD_SETTINGS = Object.freeze({
  愤怒: 'emotionThresholdAnger',
  惊讶: 'emotionThresholdSurprise',
  悲伤: 'emotionThresholdSadness',
  开心: 'emotionThresholdHappiness',
});

// These four additional sadness samples and the class filter mirror the
// currently active v4 mapping in emotion_camera算法支持.
const ADDITIONAL_SADNESS_TEMPLATES = Object.freeze([
  Object.freeze([0.0, 0.012161, 0.0, 0.000076, 0.083633, 0.041779, 0.137466, 0.087478, 0.047866, 0.105361, 0.105178, 0.106338, 0.05519, 0.071533]),
  Object.freeze([0.0, 0.017288, 0.0, 0.0, 0.094131, 0.03653, 0.150894, 0.092727, 0.063491, 0.124648, 0.116897, 0.090591, 0.127822, 0.117249]),
  Object.freeze([0.023071, 0.025833, 0.013351, 0.007888, 0.112197, 0.053253, 0.125503, 0.11824, 0.035903, 0.116836, 0.098342, 0.094497, 0.078872, 0.06665]),
  Object.freeze([0.057495, 0.045852, 0.0, 0.0, 0.093276, 0.024323, 0.136245, 0.094131, 0.050796, 0.108047, 0.109634, 0.108169, 0.061721, 0.05722]),
]);

const EMOTION_MODEL = Object.freeze({
  ...RAW_EMOTION_MODEL,
  version: 'personal-au-templates-v4',
  sourceRecordCount: Number(RAW_EMOTION_MODEL.sourceRecordCount || 0)
    + ADDITIONAL_SADNESS_TEMPLATES.length,
  classes: Object.freeze(Object.fromEntries(
    Object.entries(RAW_EMOTION_MODEL.classes || {})
      .filter(([label]) => INFERRED_EXPRESSION_LABELS.has(label))
      .map(([label, profile]) => [
        label,
        label === '悲伤'
          ? Object.freeze({
            ...profile,
            templates: Object.freeze([
              ...profile.templates,
              ...ADDITIONAL_SADNESS_TEMPLATES,
            ]),
          })
          : profile,
      ]),
  )),
});

function objectValue(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function resultSnapshot(message) {
  const root = objectValue(message) || {};
  return objectValue(root.data) || objectValue(root.result) || root;
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

function medianAuFrames(frames) {
  const names = [...new Set(frames.flatMap((frame) => Object.keys(frame.units)))];
  return Object.fromEntries(names.map((name) => [
    name,
    median(frames.map((frame) => Number(frame.units[name])).filter(Number.isFinite)),
  ]));
}

function normalizedAuUnits(snapshot) {
  const units = objectValue(snapshot.au)?.au_units;
  if (!objectValue(units)) return null;
  const entries = Object.entries(units)
    .map(([name, value]) => [String(name).toUpperCase(), Number(value)])
    .filter(([name, value]) => /^AU\d+$/.test(name) && Number.isFinite(value));
  return entries.length ? Object.fromEntries(entries) : null;
}

function vectorNorm(vector) {
  return Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
}

function templateMatchMetrics(vector, template, queryNorm) {
  let dot = 0;
  let templateSquared = 0;
  const length = Math.max(vector.length, template.length);
  for (let index = 0; index < length; index += 1) {
    const queryValue = Number(vector[index] || 0);
    const templateValue = Number(template[index] || 0);
    dot += queryValue * templateValue;
    templateSquared += templateValue * templateValue;
  }
  const templateNorm = Math.sqrt(templateSquared);
  const similarity = queryNorm && templateNorm ? dot / (queryNorm * templateNorm) : 0;
  const scale = templateSquared ? Math.max(0, dot / templateSquared) : 0;
  let residualSquared = 0;
  for (let index = 0; index < length; index += 1) {
    const delta = Number(vector[index] || 0) - scale * Number(template[index] || 0);
    residualSquared += delta * delta;
  }
  return {
    similarity,
    scale,
    residualRatio: queryNorm ? Math.sqrt(residualSquared) / queryNorm : 1,
  };
}

function inferenceLabels(result) {
  return Array.isArray(result?.emotions) ? result.emotions : [];
}

function stabilityDuration(result) {
  if (result?.state === 'neutral') return NEUTRAL_STABILITY_MS;
  if (result?.state === 'ambiguous') return AMBIGUOUS_STABILITY_MS;
  return EXPRESSION_STABILITY_MS;
}

function auNumber(label) {
  const match = String(label).match(/\d+/);
  return match ? Number(match[0]) : Number.MAX_SAFE_INTEGER;
}

// ===== 心率提取（移植自 emotion_camera算法支持/static/index.html） =====

const HEART_RATE_KEYS = [
  'heart_rate', 'heartRate', 'heart_rate_value', 'heartRateValue',
  'bpm', 'BPM', 'pulse', 'pulse_rate', 'pulseRate', 'hr', 'HR',
];

// 合理心率范围，用于排除 9999 这类“无数据”占位值。
const HEART_RATE_MIN = 30;
const HEART_RATE_MAX = 220;

// 返回 { bpm, valid }；找不到心率字段返回 null。
function extractHeartRate(data) {
  if (!data || typeof data !== 'object') return null;

  // 优先处理云端实际的嵌套结构：data.heart_rate.heart_rate
  const hrGroup = data.heart_rate;
  if (hrGroup && typeof hrGroup === 'object') {
    const bpm = Number(hrGroup.heart_rate);
    if (Number.isFinite(bpm)) {
      let valid = true;
      if (typeof hrGroup.heart_rate_is_valid === 'boolean') {
        valid = hrGroup.heart_rate_is_valid;
      } else if (typeof hrGroup.heart_rate_status === 'string') {
        const status = hrGroup.heart_rate_status.toLowerCase();
        valid = status === 'valid' || status === 'ok';
      } else {
        // 没有显式有效性字段时，用合理范围排除占位值
        valid = bpm >= HEART_RATE_MIN && bpm <= HEART_RATE_MAX;
      }
      return { bpm, valid };
    }
  }

  // 兼容其他可能的命名 / 嵌套位置
  const direct = HEART_RATE_KEYS
    .map((key) => data[key])
    .find((value) => Number.isFinite(Number(value)));
  if (direct !== undefined) {
    const n = Number(direct);
    if (Number.isFinite(n)) {
      return { bpm: n, valid: n >= HEART_RATE_MIN && n <= HEART_RATE_MAX };
    }
  }

  const nested = ['vitals', 'vital', 'hr', 'heart', 'health', 'physio'];
  for (const group of nested) {
    const obj = data[group];
    if (!obj || typeof obj !== 'object') continue;
    const inner = HEART_RATE_KEYS
      .map((key) => obj[key])
      .find((value) => Number.isFinite(Number(value)));
    if (inner !== undefined) {
      const n = Number(inner);
      if (Number.isFinite(n)) {
        return { bpm: n, valid: n >= HEART_RATE_MIN && n <= HEART_RATE_MAX };
      }
    }
  }
  return null;
}

export class EmotionTracker {
  constructor({
    maxResultAgeMs = 2500,
    settings = null,
    onLiveEmotion = () => {},
    onAuDeltas = () => {},
    onCalibrationState = () => {},
    onHeartRate = () => {},
    onCalibrationProgress = () => {},
    onCalibrationComplete = () => {},
    onSegmentState = () => {},
  } = {}) {
    this.maxResultAgeMs = maxResultAgeMs;
    this.settings = sanitizeRuntimeSettings(settings);
    this.onLiveEmotion = onLiveEmotion;
    this.onAuDeltas = onAuDeltas;
    this.onCalibrationState = onCalibrationState;
    this.onHeartRate = onHeartRate;
    this.onCalibrationProgress = onCalibrationProgress;
    this.onCalibrationComplete = onCalibrationComplete;
    this.onSegmentState = onSegmentState;
    this.started = false;
    this.baselineStartedAt = 0;
    this.baselineAu = null;
    this.auNoise = {};
    this.recentAuFrames = [];
    this.personalClasses = null;
    this.calibrationSession = null;
    this.calibrationCaptureTimer = null;
    this.inferenceCandidateKey = '';
    this.inferenceCandidateSince = 0;
    this.stableInference = null;
    this.faceAvailable = false;
    this.connectionError = false;
    this.latestReceivedAt = 0;
    this.staleTimer = null;
    this.calibrationTimer = null;
    this.latestHeartRate = null;
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastUpdatedAt = 0;
    this.windowLastLabels = [];
    this.windowLastInferenceKey = '';
    this.windowSegments = [];
    this.windowCurrentSegment = null;
    this.windowHeartRateSamples = [];
  }

  applySettings(settings) {
    this.settings = sanitizeRuntimeSettings(settings);
  }

  startCalibration() {
    this.reset();
    this.started = true;
    this.baselineStartedAt = performance.now();
    this.onCalibrationState({ state: 'countdown', remaining: 3 });
    this.calibrationTimer = window.setInterval(() => {
      if (this.baselineAu) return;
      if (this.calibrationSession) return;
      if (this.connectionError) return;
      const elapsed = performance.now() - this.baselineStartedAt;
      if (elapsed < BASELINE_COUNTDOWN_MS) {
        this.onCalibrationState({
          state: 'countdown',
          remaining: Math.max(1, Math.ceil((BASELINE_COUNTDOWN_MS - elapsed) / 1000)),
        });
      } else {
        this.onCalibrationState({ state: 'waiting_for_face' });
        this.captureBaselineIfReady();
      }
    }, 100);
  }

  // 直接以个人档案启动：跳过通用倒计时，使用档案的中性基线与模板。
  startWithProfile(profile) {
    this.reset();
    this.started = true;
    this.personalClasses = profile.classes;
    this.baselineAu = { ...profile.neutralAu };
    this.auNoise = { ...profile.neutralNoiseMad };
    this.onCalibrationState({ state: 'ready' });
    if (this.windowRequested) this.startWindow();
  }

  // 摄像头运行中切换到个人档案。
  activateProfile(profile) {
    this.personalClasses = profile.classes;
    this.baselineAu = { ...profile.neutralAu };
    this.auNoise = { ...profile.neutralNoiseMad };
    window.clearInterval(this.calibrationTimer);
    this.calibrationTimer = null;
    this.onCalibrationState({ state: 'ready' });
    if (this.windowRequested && !this.windowActive) this.startWindow();
    if (this.recentAuFrames.length) {
      this.updateStableInference(this.smoothedAuUnits(performance.now()), performance.now());
    }
  }

  // 摄像头运行中退回通用模板：重新采集通用中性基线。
  useGenericProfile() {
    this.personalClasses = null;
    this.startCalibration();
  }

  // ===== 个人基线录入状态机 =====

  beginPersonalCalibration(profileKey, displayName) {
    if (!this.started) return '请先开启摄像头后再录制个人基线。';
    if (this.calibrationSession) {
      this.emitCalibrationStep('');
      return '';
    }
    this.calibrationSession = {
      profileKey,
      displayName,
      stepIndex: 0,
      phase: 'ready',
      frames: [],
      neutralAu: null,
      neutralNoise: {},
      templates: {},
    };
    this.emitCalibrationStep('');
    return '';
  }

  hasCalibrationSession() {
    return this.calibrationSession !== null;
  }

  // 当前校准步骤的快照；无会话时返回 null（用于订阅方挂载时恢复 UI）。
  currentCalibrationStep() {
    if (!this.calibrationSession) return null;
    const step = CALIBRATION_STEPS[this.calibrationSession.stepIndex];
    return {
      state: 'step',
      stepIndex: this.calibrationSession.stepIndex,
      total: CALIBRATION_STEPS.length,
      label: step.label,
      prompt: step.prompt,
      kind: step.kind,
      phase: this.calibrationSession.phase,
      message: '',
      displayName: this.calibrationSession.displayName,
    };
  }

  emitCalibrationStep(message = '') {
    const payload = this.currentCalibrationStep();
    if (!payload) return;
    this.onCalibrationProgress({ ...payload, message });
  }

  captureCalibrationStep() {
    if (!this.calibrationSession || this.calibrationSession.phase !== 'ready') return;
    if (!this.started) {
      this.emitCalibrationStep('摄像头未开启，无法录制。');
      return;
    }
    if (!this.faceAvailable) {
      this.emitCalibrationStep('当前未检测到人脸，请面对摄像头后重试。');
      return;
    }
    if (!this.recentAuFrames.length) {
      this.emitCalibrationStep('正在等待识别数据，请稍候…');
      return;
    }
    this.calibrationSession.phase = 'capturing';
    this.calibrationSession.frames = [];
    this.emitCalibrationStep('请保持当前状态，正在采集 AU…');
    window.clearTimeout(this.calibrationCaptureTimer);
    this.calibrationCaptureTimer = window.setTimeout(
      () => this.finishCalibrationCapture(),
      CALIBRATION_CAPTURE_MS + 80,
    );
  }

  cancelPersonalCalibration() {
    window.clearTimeout(this.calibrationCaptureTimer);
    this.calibrationCaptureTimer = null;
    if (!this.calibrationSession) return;
    this.calibrationSession = null;
    this.onCalibrationProgress({ state: 'cancelled' });
  }

  calibrationVector(units, neutralAu, neutralNoise) {
    return EMOTION_MODEL.features.map((name) => {
      const change = Number(units[name] ?? neutralAu[name] ?? 0)
        - Number(neutralAu[name] ?? units[name] ?? 0);
      const noiseFloor = Math.max(
        MINIMUM_NOISE_FLOOR,
        Number(neutralNoise[name] || 0) * NOISE_MULTIPLIER,
      );
      return Number((Math.sign(change) * Math.max(0, Math.abs(change) - noiseFloor)).toFixed(6));
    });
  }

  finishCalibrationCapture() {
    this.calibrationCaptureTimer = null;
    const session = this.calibrationSession;
    if (!session || session.phase !== 'capturing') return;
    session.phase = 'ready';
    const frames = session.frames;
    if (frames.length < CALIBRATION_MINIMUM_FRAMES) {
      this.emitCalibrationStep(
        `只采到 ${frames.length} 帧有效人脸，请保持正对摄像头后重录。`,
      );
      return;
    }

    const step = CALIBRATION_STEPS[session.stepIndex];
    const capturedAu = medianAuFrames(frames);
    if (step.kind === 'neutral') {
      const noise = Object.fromEntries(Object.keys(capturedAu).map((name) => {
        const deviations = frames
          .map((frame) => Number(frame.units[name]))
          .filter(Number.isFinite)
          .map((value) => Math.abs(value - capturedAu[name]));
        return [name, median(deviations)];
      }));
      session.neutralAu = capturedAu;
      session.neutralNoise = noise;
      this.baselineAu = { ...capturedAu };
      this.auNoise = { ...noise };
      this.recentAuFrames = [];
      this.onCalibrationState({ state: 'ready' });
    } else {
      const vector = this.calibrationVector(
        capturedAu,
        session.neutralAu,
        session.neutralNoise,
      );
      const activity = vectorNorm(vector);
      const minimumActivity = Math.max(0.04, this.settings.minimumSignalNorm * 0.6);
      if (activity < minimumActivity) {
        this.emitCalibrationStep(
          `变化量 ${activity.toFixed(3)} 太小，请把${step.label}表情做明显一点后重录。`,
        );
        return;
      }
      session.templates[step.label] = vector;
    }

    session.stepIndex += 1;
    if (session.stepIndex >= CALIBRATION_STEPS.length) {
      this.finishPersonalCalibration();
      return;
    }
    this.emitCalibrationStep('');
  }

  finishPersonalCalibration() {
    const session = this.calibrationSession;
    if (!session) return;
    window.clearTimeout(this.calibrationCaptureTimer);
    this.calibrationCaptureTimer = null;
    this.calibrationSession = null;
    const profile = {
      key: session.profileKey,
      displayName: session.displayName,
      modelVersion: EMOTION_MODEL.version,
      features: [...EMOTION_MODEL.features],
      neutralAu: session.neutralAu,
      neutralNoiseMad: session.neutralNoise,
      classes: Object.fromEntries(CALIBRATION_STEPS
        .filter((step) => step.kind === 'expression')
        .map((step) => [step.label, { templates: [[...session.templates[step.label]]] }])),
    };
    this.onCalibrationComplete(profile);
    this.onCalibrationProgress({ state: 'done', displayName: session.displayName });
  }

  recordAuFrame(units, now) {
    this.recentAuFrames.push({ at: now, units: { ...units } });
    this.recentAuFrames = this.recentAuFrames
      .filter((frame) => now - frame.at <= 2200);
    if (this.calibrationSession && this.calibrationSession.phase === 'capturing'
      && this.faceAvailable) {
      this.calibrationSession.frames.push({ at: now, units: { ...units } });
    }
  }

  smoothedAuUnits(now) {
    const frames = this.recentAuFrames
      .filter((frame) => now - frame.at <= SMOOTHING_WINDOW_MS);
    return medianAuFrames(frames.length ? frames : this.recentAuFrames.slice(-1));
  }

  captureBaselineIfReady() {
    if (this.calibrationSession) return false;
    if (this.baselineAu || !this.faceAvailable) return false;
    const now = performance.now();
    if (now - this.baselineStartedAt < BASELINE_COUNTDOWN_MS) return false;
    const frames = this.recentAuFrames
      .filter((frame) => now - frame.at <= BASELINE_WINDOW_MS);
    if (frames.length < 3 || frames.at(-1).at - frames[0].at < 450) return false;

    this.baselineAu = medianAuFrames(frames);
    this.auNoise = Object.fromEntries(Object.keys(this.baselineAu).map((name) => {
      const deviations = frames
        .map((frame) => Number(frame.units[name]))
        .filter(Number.isFinite)
        .map((value) => Math.abs(value - this.baselineAu[name]));
      return [name, median(deviations)];
    }));
    window.clearInterval(this.calibrationTimer);
    this.calibrationTimer = null;
    this.onCalibrationState({ state: 'ready' });
    if (this.windowRequested) this.startWindow();
    return true;
  }

  inferExpression(currentUnits) {
    if (!this.baselineAu || !currentUnits) {
      return { state: 'neutral', emotions: ['neutral'] };
    }
    const classes = this.personalClasses || EMOTION_MODEL.classes;
    const vector = EMOTION_MODEL.features.map((name) => {
      const rawChange = Number(currentUnits[name] ?? this.baselineAu[name])
        - Number(this.baselineAu[name] ?? currentUnits[name]);
      const noiseFloor = Math.max(
        MINIMUM_NOISE_FLOOR,
        Number(this.auNoise[name] || 0) * NOISE_MULTIPLIER,
      );
      return Math.sign(rawChange) * Math.max(0, Math.abs(rawChange) - noiseFloor);
    });
    const queryNorm = vectorNorm(vector);
    const signalL1 = vector.reduce((sum, value) => sum + Math.abs(value), 0);
    const effectiveAuCount = queryNorm
      ? signalL1 * signalL1 / (queryNorm * queryNorm)
      : 0;

    const ranked = Object.entries(classes)
      .map(([label, profile]) => {
        const match = profile.templates
          .map((template) => templateMatchMetrics(vector, template, queryNorm))
          .sort((left, right) => right.similarity - left.similarity)[0];
        const threshold = this.settings[EMOTION_THRESHOLD_SETTINGS[label]] ?? 0.75;
        return { label, threshold, ...match };
      })
      .sort((left, right) => right.similarity - left.similarity);
    const winner = ranked.find((candidate) => (
      candidate.similarity >= candidate.threshold
      && candidate.scale >= MINIMUM_TEMPLATE_SCALE
      && candidate.residualRatio <= MAXIMUM_RESIDUAL_RATIO
    ));
    if (
      !winner
      || queryNorm < this.settings.minimumSignalNorm
      || effectiveAuCount < MINIMUM_EFFECTIVE_AUS
    ) {
      return { state: 'neutral', emotions: ['neutral'] };
    }
    return { state: 'expression', emotions: [MODEL_LABELS[winner.label]] };
  }

  publishAuDeltas(currentUnits) {
    if (!this.baselineAu || !currentUnits) {
      this.onAuDeltas(null);
      return;
    }
    const names = [...new Set([
      ...Object.keys(this.baselineAu),
      ...Object.keys(currentUnits),
    ])].sort((left, right) => (
      auNumber(left) - auNumber(right) || left.localeCompare(right)
    ));
    this.onAuDeltas(names.map((name) => {
      const current = Number(currentUnits[name] ?? this.baselineAu[name] ?? 0);
      const baseline = Number(this.baselineAu[name] ?? current);
      return {
        au: name,
        delta: Math.max(-1, Math.min(1, current - baseline)),
      };
    }));
  }

  updateStableInference(currentUnits, now) {
    const next = this.inferExpression(currentUnits);
    const nextKey = `${next.state}:${next.emotions.join('|')}`;
    if (nextKey !== this.inferenceCandidateKey) {
      this.inferenceCandidateKey = nextKey;
      this.inferenceCandidateSince = now;
    }
    const stableKey = this.stableInference
      ? `${this.stableInference.state}:${this.stableInference.emotions.join('|')}`
      : '';
    if (
      !this.stableInference
      || nextKey === stableKey
      || now - this.inferenceCandidateSince >= stabilityDuration(next)
    ) {
      this.accumulateWindowUntil(now);
      this.stableInference = next;
      if (this.windowActive) {
        this.windowLastLabels = inferenceLabels(next);
        this.recordWindowInference(next);
      }
      this.onLiveEmotion(next.emotions);
    }
  }

  updateResult(message) {
    const snapshot = resultSnapshot(message);
    const face = objectValue(snapshot.face) || {};
    const detected = face.face_detected ?? face.detected
      ?? snapshot.face_detected ?? snapshot.faceDetected;
    if (detected === false || detected === 0) {
      this.handleUnavailableFace();
      return false;
    }

    const now = performance.now();
    const heartRate = extractHeartRate(snapshot);
    if (heartRate && heartRate.valid) {
      this.latestHeartRate = { bpm: heartRate.bpm, at: now };
      this.onHeartRate(heartRate.bpm);
      if (this.windowActive) {
        this.windowHeartRateSamples.push({ t: now, bpm: heartRate.bpm });
      }
    }

    const units = normalizedAuUnits(snapshot);
    if (!units) return false;

    this.faceAvailable = true;
    this.latestReceivedAt = now;
    window.clearTimeout(this.staleTimer);
    this.staleTimer = window.setTimeout(() => {
      if (performance.now() - this.latestReceivedAt >= this.maxResultAgeMs) {
        this.handleUnavailableFace();
      }
    }, this.maxResultAgeMs);
    this.recordAuFrame(units, now);
    if (!this.baselineAu) {
      this.captureBaselineIfReady();
      return true;
    }
    const smoothedUnits = this.smoothedAuUnits(now);
    this.publishAuDeltas(smoothedUnits);
    this.updateStableInference(smoothedUnits, now);
    return true;
  }

  handleUnavailableFace() {
    const now = performance.now();
    this.accumulateWindowUntil(now);
    if (this.windowCurrentSegment && this.windowCurrentSegment.durationMs > 0) {
      this.windowSegments.push(this.windowCurrentSegment);
    }
    this.windowCurrentSegment = null;
    this.windowLastInferenceKey = '';
    this.faceAvailable = false;
    this.latestReceivedAt = 0;
    this.windowLastLabels = [];
    this.onLiveEmotion(null);
    this.onAuDeltas(null);
  }

  clearLatestResult() {
    window.clearTimeout(this.staleTimer);
    this.staleTimer = null;
    this.handleUnavailableFace();
  }

  connectionFailed(message) {
    this.clearLatestResult();
    this.connectionError = true;
    this.onCalibrationState({ state: 'connection_error', message });
  }

  connectionRestored() {
    this.connectionError = false;
    if (this.baselineAu) {
      this.onCalibrationState({ state: 'ready' });
      if (this.stableInference) {
        this.onLiveEmotion(this.stableInference.emotions);
      }
      return;
    }
    const elapsed = performance.now() - this.baselineStartedAt;
    this.onCalibrationState(
      elapsed < BASELINE_COUNTDOWN_MS
        ? {
          state: 'countdown',
          remaining: Math.max(1, Math.ceil((BASELINE_COUNTDOWN_MS - elapsed) / 1000)),
        }
        : { state: 'waiting_for_face' },
    );
  }

  accumulateWindowUntil(now = performance.now()) {
    if (!this.windowActive || !this.windowLastLabels.length) {
      this.windowLastUpdatedAt = now;
      this.onSegmentState(null);
      return;
    }
    const elapsed = Math.max(0, now - this.windowLastUpdatedAt);
    if (this.windowCurrentSegment) {
      this.windowCurrentSegment.durationMs += elapsed;
      this.onSegmentState({
        durationMs: this.windowCurrentSegment.durationMs,
        thresholdMs: this.settings.emotionSegmentMinMs,
      });
    }
    this.windowLastUpdatedAt = now;
  }

  recordWindowInference(inference) {
    if (!this.windowActive) return;
    const labels = inferenceLabels(inference);
    if (!labels.length) return;
    const key = `${inference?.state || ''}:${labels.join('|')}`;
    if (key === this.windowLastInferenceKey) return;
    this.windowLastInferenceKey = key;
    if (this.windowCurrentSegment && this.windowCurrentSegment.durationMs > 0) {
      this.windowSegments.push(this.windowCurrentSegment);
    }
    this.windowCurrentSegment = { labels: [...labels], durationMs: 0 };
  }

  startWindow() {
    this.windowRequested = true;
    this.windowLastUpdatedAt = performance.now();
    this.windowLastLabels = [];
    this.windowSegments = [];
    this.windowCurrentSegment = null;
    this.windowLastInferenceKey = '';
    this.windowHeartRateSamples = [];
    this.windowActive = Boolean(this.baselineAu);
    if (this.windowActive && this.faceAvailable && this.stableInference) {
      this.windowLastLabels = inferenceLabels(this.stableInference);
      this.recordWindowInference(this.stableInference);
    }
  }

  pauseWindow() {
    this.accumulateWindowUntil();
    if (this.windowCurrentSegment && this.windowCurrentSegment.durationMs > 0) {
      this.windowSegments.push(this.windowCurrentSegment);
    }
    this.windowCurrentSegment = null;
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastLabels = [];
    this.windowLastInferenceKey = '';
    // Live AU inference intentionally stays active while the assistant speaks.
    // Only the request-scoped duration accumulator is paused here.
  }

  resumeWindow() {
    this.startWindow();
  }

  aggregateWindow() {
    const segments = this.windowCurrentSegment
      ? [...this.windowSegments, this.windowCurrentSegment]
      : [...this.windowSegments];

    // 非中性段连续时长低于阈值则丢弃；中性段不受阈值限制，始终保留。
    const thresholdMs = this.settings.emotionSegmentMinMs;
    const kept = segments.filter((segment) => (
      segment.labels.includes('neutral')
      || segment.durationMs >= thresholdMs
    ));

    const totalDuration = kept.reduce((sum, segment) => sum + segment.durationMs, 0);
    const firstSeen = new Map();
    kept.forEach((segment, sequenceIndex) => {
      segment.labels.forEach((emotion, emotionIndex) => {
        if (!firstSeen.has(emotion)) {
          firstSeen.set(emotion, sequenceIndex * 2 + emotionIndex);
        }
      });
    });

    const durations = {};
    for (const segment of kept) {
      const share = segment.labels.length
        ? segment.durationMs / segment.labels.length
        : 0;
      for (const emotion of segment.labels) {
        durations[emotion] = (durations[emotion] || 0) + share;
      }
    }

    const durationEntries = Object.entries(durations)
      .filter(([, duration]) => duration > 0);
    const nonNeutralEntries = durationEntries
      .filter(([emotion]) => emotion !== 'neutral');
    const candidates = nonNeutralEntries.length
      ? nonNeutralEntries
      : durationEntries.filter(([emotion]) => emotion === 'neutral');
    const emotions = candidates
      .sort((left, right) => (
        right[1] - left[1]
        || (firstSeen.get(left[0]) ?? Number.MAX_SAFE_INTEGER)
          - (firstSeen.get(right[0]) ?? Number.MAX_SAFE_INTEGER)
      ))
      .slice(0, 2)
      .sort((left, right) => (
        (firstSeen.get(left[0]) ?? Number.MAX_SAFE_INTEGER)
          - (firstSeen.get(right[0]) ?? Number.MAX_SAFE_INTEGER)
      ))
      .map(([emotion]) => emotion);

    return {
      emotions: emotions.length ? emotions : ['neutral'],
      valid_duration_ms: Math.round(totalDuration),
    };
  }

  // 窗口内心率均值；样本不足时返回 null（不参与 prompt 拼接）。
  windowHeartRateAggregate() {
    const samples = this.windowHeartRateSamples;
    if (samples.length < WINDOW_HEART_RATE_MINIMUM_SAMPLES) return null;
    const total = samples.reduce((sum, sample) => sum + sample.bpm, 0);
    const average = total / samples.length;
    if (!Number.isFinite(average)) return null;
    return {
      avg_bpm: Math.round(average),
      sample_count: samples.length,
    };
  }

  consumeWindow() {
    if (!this.baselineAu) {
      this.windowRequested = false;
      this.windowActive = false;
      this.windowLastLabels = [];
      this.windowSegments = [];
      this.windowCurrentSegment = null;
      this.windowLastInferenceKey = '';
      this.windowHeartRateSamples = [];
      return null;
    }
    this.accumulateWindowUntil();
    this.windowActive = false;
    this.windowRequested = false;
    this.windowLastLabels = [];
    this.windowLastInferenceKey = '';
    const aggregate = this.aggregateWindow();
    const heartRate = this.windowHeartRateAggregate();
    this.windowHeartRateSamples = [];
    return heartRate ? { ...aggregate, heart_rate: heartRate } : aggregate;
  }

  reset() {
    window.clearTimeout(this.staleTimer);
    window.clearInterval(this.calibrationTimer);
    window.clearTimeout(this.calibrationCaptureTimer);
    this.staleTimer = null;
    this.calibrationTimer = null;
    this.calibrationCaptureTimer = null;
    this.started = false;
    this.baselineStartedAt = 0;
    this.baselineAu = null;
    this.auNoise = {};
    this.recentAuFrames = [];
    this.personalClasses = null;
    this.calibrationSession = null;
    this.inferenceCandidateKey = '';
    this.inferenceCandidateSince = 0;
    this.stableInference = null;
    this.faceAvailable = false;
    this.connectionError = false;
    this.latestReceivedAt = 0;
    this.latestHeartRate = null;
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastUpdatedAt = 0;
    this.windowLastLabels = [];
    this.windowSegments = [];
    this.windowCurrentSegment = null;
    this.windowLastInferenceKey = '';
    this.windowHeartRateSamples = [];
    this.onLiveEmotion(null);
    this.onAuDeltas(null);
    this.onSegmentState(null);
    this.onCalibrationState({ state: 'idle' });
  }

  destroy() {
    this.reset();
  }
}
