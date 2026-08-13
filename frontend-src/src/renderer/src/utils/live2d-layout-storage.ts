export interface Live2DModelLayout {
  x?: number;
  y?: number;
  scale?: number;
}

type Live2DModelLayouts = Record<string, Live2DModelLayout>;

const STORAGE_KEY = 'live2d:model-layouts:v1';

const getModelKey = (modelUrl: string): string => {
  try {
    return new URL(modelUrl, window.location.href).pathname;
  } catch (_error) {
    return modelUrl;
  }
};

const readLayouts = (): Live2DModelLayouts => {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return {};

    const parsed = JSON.parse(stored);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    console.warn('[Live2D] Failed to read saved model layout:', error);
    return {};
  }
};

export const getSavedLive2DLayout = (
  modelUrl: string | undefined,
): Live2DModelLayout | undefined => {
  if (!modelUrl) return undefined;
  return readLayouts()[getModelKey(modelUrl)];
};

export const saveLive2DLayout = (
  modelUrl: string | undefined,
  update: Live2DModelLayout,
): void => {
  if (!modelUrl) return;

  try {
    const layouts = readLayouts();
    const modelKey = getModelKey(modelUrl);
    layouts[modelKey] = {
      ...layouts[modelKey],
      ...update,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layouts));
  } catch (error) {
    console.warn('[Live2D] Failed to save model layout:', error);
  }
};
