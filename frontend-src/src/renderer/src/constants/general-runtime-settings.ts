export interface GeneralRuntimeSettings {
  generateAudio: boolean;
  debugMode: boolean;
}

const DEFAULT_GENERAL_RUNTIME_SETTINGS: GeneralRuntimeSettings = {
  generateAudio: false,
  debugMode: false,
};

let currentSettings = { ...DEFAULT_GENERAL_RUNTIME_SETTINGS };

type GeneralRuntimeSettingsListener = (
  settings: GeneralRuntimeSettings,
) => void;

const listeners = new Set<GeneralRuntimeSettingsListener>();

export const getGeneralRuntimeSettings = (): GeneralRuntimeSettings => ({
  ...currentSettings,
});

export const setGeneralRuntimeSettings = (
  settings: GeneralRuntimeSettings,
): void => {
  currentSettings = { ...settings };
  listeners.forEach((listener) => listener(getGeneralRuntimeSettings()));
};

export const subscribeGeneralRuntimeSettings = (
  listener: GeneralRuntimeSettingsListener,
): (() => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
