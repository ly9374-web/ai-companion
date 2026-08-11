export const GENERATE_AUDIO_STORAGE_KEY = 'generateAudio';

export const getStoredGenerateAudio = (): boolean => {
  try {
    const stored = localStorage.getItem(GENERATE_AUDIO_STORAGE_KEY);
    return stored === null ? true : JSON.parse(stored) !== false;
  } catch (error) {
    console.warn('Failed to read generate-audio setting:', error);
    return true;
  }
};
