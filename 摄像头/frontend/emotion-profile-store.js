// 个人表情基线档案与识别参数的 localStorage 持久化。
// 结构参考 emotion_camera算法支持/static/index.html 的个人档案实现，
// 额外为每个档案保存一份独立的运行参数（5 项）。

import { RAW_EMOTION_MODEL } from './emotion-model.js';

const PROFILE_STORAGE_KEY = 'emotion-camera-personal-profiles-v2';
const CALIBRATION_EXPRESSION_LABELS = ['悲伤', '愤怒', '惊讶', '开心'];

export const DEFAULT_RUNTIME_SETTINGS = Object.freeze({
  minimumSignalNorm: 0.12,
  emotionThresholdSadness: 0.75,
  emotionThresholdAnger: 0.75,
  emotionThresholdSurprise: 0.75,
  emotionThresholdHappiness: 0.75,
  emotionSegmentMinMs: 1500,
});

const SETTING_LIMITS = Object.freeze({
  minimumSignalNorm: [0, 1],
  emotionThresholdSadness: [0, 1],
  emotionThresholdAnger: [0, 1],
  emotionThresholdSurprise: [0, 1],
  emotionThresholdHappiness: [0, 1],
  emotionSegmentMinMs: [0, 10000],
});

export function normalizeProfileDisplayName(value) {
  return String(value || '').normalize('NFKC').trim().replace(/\s+/g, ' ').slice(0, 40);
}

export function normalizeProfileKey(value) {
  return normalizeProfileDisplayName(value).toLocaleLowerCase('zh-CN');
}

function sanitizeNumber(value, [minimum, maximum], fallback) {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

export function sanitizeRuntimeSettings(settings) {
  const source = settings && typeof settings === 'object' ? settings : {};
  const result = {};
  for (const key of Object.keys(DEFAULT_RUNTIME_SETTINGS)) {
    result[key] = sanitizeNumber(
      source[key],
      SETTING_LIMITS[key],
      DEFAULT_RUNTIME_SETTINGS[key],
    );
  }
  return result;
}

function expectedFeatureSignature() {
  return (RAW_EMOTION_MODEL.features || []).join('|');
}

function validAuMap(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value)
    .map(([name, number]) => [String(name), Number(number)])
    .filter(([name, number]) => /^AU\d+$/.test(name) && Number.isFinite(number));
  return entries.length ? Object.fromEntries(entries) : null;
}

export function validPersonalProfile(profile) {
  if (!profile || typeof profile !== 'object') return false;
  if (!Array.isArray(profile.features)) return false;
  if (profile.features.join('|') !== expectedFeatureSignature()) return false;
  if (!validAuMap(profile.neutralAu) || !validAuMap(profile.neutralNoiseMad)) return false;
  if (!profile.classes || typeof profile.classes !== 'object') return false;
  return CALIBRATION_EXPRESSION_LABELS.every((label) => {
    const templates = profile.classes[label]?.templates;
    return Array.isArray(templates) && templates.length >= 1
      && templates.every((template) => Array.isArray(template)
        && template.length === profile.features.length
        && template.every((number) => Number.isFinite(Number(number))));
  });
}

function sanitizeProfile(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (!validPersonalProfile(raw)) return null;
  return {
    version: 2,
    key: String(raw.key || normalizeProfileKey(raw.displayName || '')),
    displayName: String(raw.displayName || raw.key || '').slice(0, 40),
    createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : '',
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : '',
    modelVersion: String(raw.modelVersion || ''),
    features: [...raw.features],
    neutralAu: { ...raw.neutralAu },
    neutralNoiseMad: { ...raw.neutralNoiseMad },
    classes: Object.fromEntries(CALIBRATION_EXPRESSION_LABELS.map((label) => [
      label,
      { templates: raw.classes[label].templates.map((template) => [...template]) },
    ])),
    settings: sanitizeRuntimeSettings(raw.settings),
  };
}

export function loadProfileStore() {
  const empty = {
    version: 2,
    profiles: Object.create(null),
    genericSettings: { ...DEFAULT_RUNTIME_SETTINGS },
    lastUsedKey: '',
  };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PROFILE_STORAGE_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object' || typeof parsed.profiles !== 'object') {
      return empty;
    }
    const profiles = Object.create(null);
    for (const [key, raw] of Object.entries(parsed.profiles)) {
      const profile = sanitizeProfile(raw);
      if (profile && profile.key) profiles[profile.key] = profile;
    }
    return {
      version: 2,
      profiles,
      genericSettings: sanitizeRuntimeSettings(parsed.genericSettings),
      lastUsedKey: typeof parsed.lastUsedKey === 'string' ? parsed.lastUsedKey : '',
    };
  } catch (_error) {
    return empty;
  }
}

export function persistProfileStore(store) {
  try {
    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(store));
    return true;
  } catch (_error) {
    return false;
  }
}

export function findProfileByName(store, displayName) {
  return store.profiles[normalizeProfileKey(displayName)] || null;
}

export { CALIBRATION_EXPRESSION_LABELS };
