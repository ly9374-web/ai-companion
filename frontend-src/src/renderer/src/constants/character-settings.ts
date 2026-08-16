export const CHARACTER_PRESET_STORAGE_KEY = 'selectedCharacterPreset';

export const getStoredCharacterPreset = (): string => {
  try {
    const value = localStorage.getItem(CHARACTER_PRESET_STORAGE_KEY);
    return value ? JSON.parse(value) : '';
  } catch (_error) {
    return '';
  }
};

export const setStoredCharacterPreset = (filename: string): void => {
  if (filename) {
    localStorage.setItem(
      CHARACTER_PRESET_STORAGE_KEY,
      JSON.stringify(filename),
    );
  }
};
