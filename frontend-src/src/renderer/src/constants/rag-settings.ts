export interface RagSettings {
  topK: number;
  threshold: number;
  hybridWeight: number;
}

export const DEFAULT_RAG_SETTINGS: RagSettings = {
  topK: 5,
  threshold: 0.5,
  hybridWeight: 0.5,
};

export const RAG_SETTINGS_KEY = 'ragSettings';

export function sanitizeRagSettings(value: Partial<RagSettings> | null): RagSettings {
  const topK = Number(value?.topK);
  const threshold = Number(value?.threshold);
  const hybridWeight = Number(value?.hybridWeight);
  return {
    topK: Number.isFinite(topK) ? Math.max(1, Math.min(20, Math.round(topK))) : 5,
    threshold: Number.isFinite(threshold) ? Math.max(0, Math.min(1, threshold)) : 0.5,
    hybridWeight: Number.isFinite(hybridWeight)
      ? Math.max(0, Math.min(1, hybridWeight))
      : 0.5,
  };
}

export function getStoredRagSettings(): RagSettings {
  try {
    const raw = window.localStorage.getItem(RAG_SETTINGS_KEY);
    return sanitizeRagSettings(raw ? JSON.parse(raw) : null);
  } catch {
    return { ...DEFAULT_RAG_SETTINGS };
  }
}
