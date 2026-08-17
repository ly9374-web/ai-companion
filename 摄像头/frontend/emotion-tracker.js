import { RAW_EMOTION_MODEL } from './emotion-model.js';

const BASELINE_COUNTDOWN_MS = 3000;
const BASELINE_WINDOW_MS = 1100;
const SMOOTHING_WINDOW_MS = 550;
const EXPRESSION_STABILITY_MS = 280;
const AMBIGUOUS_STABILITY_MS = 360;
const NEUTRAL_STABILITY_MS = 450;
const MINIMUM_SIGNAL_NORM = 0.12;
const MINIMUM_EMOTION_SIMILARITY = 0.75;
const MINIMUM_TEMPLATE_SCALE = 0;
const MAXIMUM_RESIDUAL_RATIO = 1;
const MINIMUM_EFFECTIVE_AUS = 1;
const NOISE_MULTIPLIER = 2.5;
const MINIMUM_NOISE_FLOOR = 0.015;

const INFERRED_EXPRESSION_LABELS = new Set(['愤怒', '开心', '悲伤', '惊讶']);
const MODEL_LABELS = Object.freeze({
  愤怒: 'angry',
  开心: 'happy',
  悲伤: 'sad',
  惊讶: 'surprise',
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

export class EmotionTracker {
  constructor({
    maxResultAgeMs = 2500,
    onLiveEmotion = () => {},
    onAuDeltas = () => {},
    onCalibrationState = () => {},
  } = {}) {
    this.maxResultAgeMs = maxResultAgeMs;
    this.onLiveEmotion = onLiveEmotion;
    this.onAuDeltas = onAuDeltas;
    this.onCalibrationState = onCalibrationState;
    this.baselineStartedAt = 0;
    this.baselineAu = null;
    this.auNoise = {};
    this.recentAuFrames = [];
    this.inferenceCandidateKey = '';
    this.inferenceCandidateSince = 0;
    this.stableInference = null;
    this.faceAvailable = false;
    this.latestReceivedAt = 0;
    this.staleTimer = null;
    this.calibrationTimer = null;
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastUpdatedAt = 0;
    this.windowLastLabels = [];
    this.windowDurations = {};
    this.windowEmotionSequence = [];
    this.windowLastInferenceKey = '';
  }

  startCalibration() {
    this.reset();
    this.baselineStartedAt = performance.now();
    this.onCalibrationState({ state: 'countdown', remaining: 3 });
    this.calibrationTimer = window.setInterval(() => {
      if (this.baselineAu) return;
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

  recordAuFrame(units, now) {
    this.recentAuFrames.push({ at: now, units: { ...units } });
    this.recentAuFrames = this.recentAuFrames
      .filter((frame) => now - frame.at <= 2200);
  }

  smoothedAuUnits(now) {
    const frames = this.recentAuFrames
      .filter((frame) => now - frame.at <= SMOOTHING_WINDOW_MS);
    return medianAuFrames(frames.length ? frames : this.recentAuFrames.slice(-1));
  }

  captureBaselineIfReady() {
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

    const ranked = Object.entries(EMOTION_MODEL.classes)
      .map(([label, profile]) => {
        const match = profile.templates
          .map((template) => templateMatchMetrics(vector, template, queryNorm))
          .sort((left, right) => right.similarity - left.similarity)[0];
        return { label, ...match };
      })
      .sort((left, right) => right.similarity - left.similarity);
    const winner = ranked.find((candidate) => (
      candidate.similarity >= MINIMUM_EMOTION_SIMILARITY
      && candidate.scale >= MINIMUM_TEMPLATE_SCALE
      && candidate.residualRatio <= MAXIMUM_RESIDUAL_RATIO
    ));
    if (
      !winner
      || queryNorm < MINIMUM_SIGNAL_NORM
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
    const units = normalizedAuUnits(snapshot);
    if (!units) return false;

    const now = performance.now();
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
    this.onCalibrationState({ state: 'connection_error', message });
  }

  connectionRestored() {
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
      return;
    }
    const elapsed = Math.max(0, now - this.windowLastUpdatedAt);
    const share = elapsed / this.windowLastLabels.length;
    for (const emotion of this.windowLastLabels) {
      this.windowDurations[emotion] = (this.windowDurations[emotion] || 0) + share;
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
    this.windowEmotionSequence.push([...labels]);
  }

  startWindow() {
    this.windowRequested = true;
    this.windowDurations = {};
    this.windowLastUpdatedAt = performance.now();
    this.windowLastLabels = [];
    this.windowEmotionSequence = [];
    this.windowLastInferenceKey = '';
    this.windowActive = Boolean(this.baselineAu);
    if (this.windowActive && this.faceAvailable && this.stableInference) {
      this.windowLastLabels = inferenceLabels(this.stableInference);
      this.recordWindowInference(this.stableInference);
    }
  }

  pauseWindow() {
    this.accumulateWindowUntil();
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
    const totalDuration = Object.values(this.windowDurations)
      .reduce((sum, duration) => sum + duration, 0);
    const firstSeen = new Map();
    this.windowEmotionSequence.forEach((emotions, sequenceIndex) => {
      emotions.forEach((emotion, emotionIndex) => {
        if (!firstSeen.has(emotion)) {
          firstSeen.set(emotion, sequenceIndex * 2 + emotionIndex);
        }
      });
    });

    const durationEntries = Object.entries(this.windowDurations)
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

  consumeWindow() {
    if (!this.baselineAu) {
      this.windowRequested = false;
      this.windowActive = false;
      this.windowLastLabels = [];
      this.windowDurations = {};
      this.windowEmotionSequence = [];
      this.windowLastInferenceKey = '';
      return null;
    }
    this.accumulateWindowUntil();
    this.windowActive = false;
    this.windowRequested = false;
    this.windowLastLabels = [];
    this.windowLastInferenceKey = '';
    return this.aggregateWindow();
  }

  reset() {
    window.clearTimeout(this.staleTimer);
    window.clearInterval(this.calibrationTimer);
    this.staleTimer = null;
    this.calibrationTimer = null;
    this.baselineStartedAt = 0;
    this.baselineAu = null;
    this.auNoise = {};
    this.recentAuFrames = [];
    this.inferenceCandidateKey = '';
    this.inferenceCandidateSince = 0;
    this.stableInference = null;
    this.faceAvailable = false;
    this.latestReceivedAt = 0;
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastUpdatedAt = 0;
    this.windowLastLabels = [];
    this.windowDurations = {};
    this.windowEmotionSequence = [];
    this.windowLastInferenceKey = '';
    this.onLiveEmotion(null);
    this.onAuDeltas(null);
    this.onCalibrationState({ state: 'idle' });
  }

  destroy() {
    this.reset();
  }
}
