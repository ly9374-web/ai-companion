export const DEFAULT_MAX_HISTORY_TURNS = 8;
export const MIN_MAX_HISTORY_TURNS = 1;
export const MAX_MAX_HISTORY_TURNS = 100;
export const MAX_HISTORY_TURNS_STORAGE_KEY = 'maxHistoryTurns';

export const getStoredMaxHistoryTurns = (): number => {
  const value = Number.parseInt(
    localStorage.getItem(MAX_HISTORY_TURNS_STORAGE_KEY) ?? '',
    10,
  );
  return Number.isNaN(value)
    || value < MIN_MAX_HISTORY_TURNS
    || value > MAX_MAX_HISTORY_TURNS
    ? DEFAULT_MAX_HISTORY_TURNS
    : value;
};

export const setCurrentMaxHistoryTurns = (value: number): void => {
  localStorage.setItem(MAX_HISTORY_TURNS_STORAGE_KEY, value.toString());
};
