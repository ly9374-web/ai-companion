export interface GeneralRuntimeSettings {
  generateAudio: boolean;
  debugMode: boolean;
}

const DEFAULT_GENERAL_RUNTIME_SETTINGS: GeneralRuntimeSettings = {
  generateAudio: false,
  debugMode: false,
};

let currentSettings = { ...DEFAULT_GENERAL_RUNTIME_SETTINGS };

export const getGeneralRuntimeSettings = (): GeneralRuntimeSettings => ({
  ...currentSettings,
});

export const setGeneralRuntimeSettings = (
  settings: GeneralRuntimeSettings,
): void => {
  currentSettings = { ...settings };
};
