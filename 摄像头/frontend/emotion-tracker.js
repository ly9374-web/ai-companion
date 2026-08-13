const BASELINE_COUNTDOWN_MS = 3000;
const BASELINE_WINDOW_MS = 1100;
const SMOOTHING_WINDOW_MS = 550;
const INFERENCE_STABILITY_MS = 650;
const NEUTRAL_ACTIVITY_THRESHOLD = 0.055;
const MIN_EXPRESSION_SCORE = 0.55;
const MIN_SCORE_MARGIN = 0.10;
const EXPRESSION_AU_TRIGGER_THRESHOLD = 0.13;
const SUMMARY_NON_NEUTRAL_THRESHOLD = 0.10;
const SUMMARY_CLOSE_MARGIN = 0.01;
const IGNORED_INFERENCE_AUS = new Set(['AU43']);

const EMOTION_TRIGGER_AUS = {
  surprise: ['AU1', 'AU2', 'AU5', 'AU26'],
  sad: ['AU1', 'AU4', 'AU15'],
  angry: ['AU4', 'AU5', 'AU10', 'AU17', 'AU20', 'AU25'],
  disgust: ['AU9', 'AU10', 'AU17'],
  happy: ['AU6', 'AU12'],
};

// The calibrated variants come from the reference v3 frozen samples. They
// intentionally score AU proportions rather than cloud emotion confidence.
const EMOTION_PROTOTYPES = {
  surprise: [
    { AU1: 0.28, AU2: 0.28, AU5: 0.22, AU26: 0.22 },
    { AU1: 0.38, AU2: 0.38, AU5: 0.24 },
    { AU1: 0.42, AU2: 0.38, AU26: 0.20 },
  ],
  sad: [
    { AU1: 0.35, AU4: 0.38, AU15: 0.27 },
    { AU1: 0.42, AU4: 0.38, AU17: 0.20 },
    { AU1: 0.42, AU15: 0.36, AU17: 0.22 },
    { AU15: 0.18, AU20: 0.16, AU1: 0.14, AU17: 0.14, AU2: 0.11, AU9: 0.10, AU6: 0.08, AU26: 0.05, AU12: 0.04 },
  ],
  angry: [
    { AU4: 0.45, AU5: 0.18, AU10: 0.16, AU17: 0.11, AU25: 0.10 },
    { AU4: 0.52, AU5: 0.22, AU20: 0.14, AU25: 0.12 },
    { AU4: 0.58, AU10: 0.22, AU17: 0.20 },
    { AU4: 0.56, AU9: 0.20, AU10: 0.12, AU6: 0.12 },
    { AU4: 0.34, AU9: 0.20, AU6: 0.10, AU20: 0.09, AU12: 0.08, AU15: 0.06, AU10: 0.05, AU14: 0.04, AU17: 0.04 },
  ],
  disgust: [
    { AU9: 0.55, AU17: 0.25, AU4: 0.20 },
    { AU10: 0.55, AU17: 0.25, AU4: 0.20 },
    { AU9: 0.62, AU10: 0.38 },
    { AU9: 0.40, AU4: 0.28, AU17: 0.17, AU6: 0.15 },
    { AU9: 0.18, AU4: 0.16, AU6: 0.15, AU25: 0.13, AU12: 0.09, AU15: 0.08, AU10: 0.08, AU14: 0.06, AU26: 0.03, AU1: 0.02, AU20: 0.02 },
  ],
  happy: [
    { AU12: 0.58, AU6: 0.42 },
    { AU12: 0.74, AU6: 0.26 },
    { AU12: 1.00 },
    { AU12: 0.24, AU6: 0.19, AU25: 0.18, AU10: 0.13, AU14: 0.13, AU20: 0.07, AU17: 0.04, AU2: 0.02 },
  ],
};

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

function cosineSimilarity(left, right) {
  const names = new Set([...Object.keys(left), ...Object.keys(right)]);
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (const name of names) {
    const a = Number(left[name] || 0);
    const b = Number(right[name] || 0);
    dot += a * b;
    leftNorm += a * a;
    rightNorm += b * b;
  }
  return leftNorm && rightNorm ? dot / Math.sqrt(leftNorm * rightNorm) : 0;
}

function scoreExpressionProfile(shares, variants) {
  let best = 0;
  for (const prototype of variants) {
    const similarity = cosineSimilarity(shares, prototype);
    const coverage = Object.keys(prototype)
      .reduce((sum, au) => sum + (shares[au] || 0), 0);
    best = Math.max(
      best,
      similarity * 0.72 + Math.min(1, coverage * 1.35) * 0.28,
    );
  }
  return Math.min(0.96, best);
}

function inferenceLabels(result) {
  return Array.isArray(result?.emotions) ? result.emotions : [];
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
    const signals = {};
    const rawPositiveChanges = {};
    let activitySquared = 0;
    const names = new Set([...Object.keys(this.baselineAu), ...Object.keys(currentUnits)]);
    for (const name of names) {
      if (IGNORED_INFERENCE_AUS.has(name)) continue;
      const rawChange = Number(currentUnits[name] ?? this.baselineAu[name])
        - Number(this.baselineAu[name] ?? currentUnits[name]);
      rawPositiveChanges[name] = Math.max(0, rawChange);
      const noiseFloor = Math.max(0.015, Number(this.auNoise[name] || 0) * 2.5);
      const signal = Math.max(0, rawChange - noiseFloor);
      if (signal > 0) signals[name] = signal;
      activitySquared += signal * signal;
    }

    const activity = Math.sqrt(activitySquared);
    const total = Object.values(signals).reduce((sum, value) => sum + value, 0);
    if (activity < NEUTRAL_ACTIVITY_THRESHOLD || total <= 0) {
      return { state: 'neutral', emotions: ['neutral'] };
    }

    const shares = Object.fromEntries(
      Object.entries(signals).map(([name, value]) => [name, value / total]),
    );
    const ranked = Object.entries(EMOTION_PROTOTYPES)
      .map(([emotion, variants]) => {
        const trigger = EMOTION_TRIGGER_AUS[emotion]
          .map((au) => ({ au, change: rawPositiveChanges[au] || 0 }))
          .sort((left, right) => right.change - left.change)[0];
        return { emotion, score: scoreExpressionProfile(shares, variants), trigger };
      })
      .filter((item) => item.trigger.change >= EXPRESSION_AU_TRIGGER_THRESHOLD)
      .sort((left, right) => right.score - left.score);

    const winner = ranked[0];
    const runnerUp = ranked[1];
    if (!winner || winner.score < MIN_EXPRESSION_SCORE) {
      return { state: 'neutral', emotions: ['neutral'] };
    }
    if (runnerUp && winner.score - runnerUp.score < MIN_SCORE_MARGIN) {
      return {
        state: 'ambiguous',
        emotions: [winner.emotion, runnerUp.emotion],
      };
    }
    return { state: 'expression', emotions: [winner.emotion] };
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
      || now - this.inferenceCandidateSince >= INFERENCE_STABILITY_MS
    ) {
      this.accumulateWindowUntil(now);
      this.stableInference = next;
      if (this.windowActive) this.windowLastLabels = inferenceLabels(next);
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

  startWindow() {
    this.windowRequested = true;
    this.windowDurations = {};
    this.windowLastUpdatedAt = performance.now();
    this.windowLastLabels = [];
    this.windowActive = Boolean(this.baselineAu);
    if (this.windowActive && this.faceAvailable && this.stableInference) {
      this.windowLastLabels = inferenceLabels(this.stableInference);
    }
  }

  pauseWindow() {
    this.accumulateWindowUntil();
    this.windowRequested = false;
    this.windowActive = false;
    this.windowLastLabels = [];
    // Live AU inference intentionally stays active while the assistant speaks.
    // Only the request-scoped duration accumulator is paused here.
  }

  resumeWindow() {
    this.startWindow();
  }

  aggregateWindow() {
    const totalDuration = Object.values(this.windowDurations)
      .reduce((sum, duration) => sum + duration, 0);
    const nonNeutral = Object.entries(this.windowDurations)
      .filter(([emotion, duration]) => (
        emotion !== 'neutral'
        && totalDuration > 0
        && duration / totalDuration > SUMMARY_NON_NEUTRAL_THRESHOLD
      ))
      .sort((left, right) => right[1] - left[1]);

    let emotions = ['neutral'];
    if (nonNeutral.length) {
      emotions = [nonNeutral[0][0]];
      if (
        nonNeutral[1]
        && (nonNeutral[0][1] - nonNeutral[1][1]) / totalDuration
          <= SUMMARY_CLOSE_MARGIN
      ) {
        emotions.push(nonNeutral[1][0]);
      }
    }
    return {
      emotions,
      valid_duration_ms: Math.round(totalDuration),
    };
  }

  consumeWindow() {
    if (!this.baselineAu) {
      this.windowRequested = false;
      this.windowActive = false;
      this.windowLastLabels = [];
      this.windowDurations = {};
      return null;
    }
    this.accumulateWindowUntil();
    this.windowActive = false;
    this.windowRequested = false;
    this.windowLastLabels = [];
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
    this.onLiveEmotion(null);
    this.onAuDeltas(null);
    this.onCalibrationState({ state: 'idle' });
  }

  destroy() {
    this.reset();
  }
}
