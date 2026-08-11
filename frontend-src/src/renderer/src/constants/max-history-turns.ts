export const MAX_HISTORY_TURNS_STORAGE_KEY = 'maxHistoryTurns';
export const DEFAULT_MAX_HISTORY_TURNS = 8;
export const MIN_MAX_HISTORY_TURNS = 1;
export const MAX_MAX_HISTORY_TURNS = 100;

export const getStoredMaxHistoryTurns = (): number => {
  const storedValue = localStorage.getItem(MAX_HISTORY_TURNS_STORAGE_KEY);
  const parsedValue = Number.parseInt(storedValue ?? '', 10);

  if (
    Number.isNaN(parsedValue)
    || parsedValue < MIN_MAX_HISTORY_TURNS
    || parsedValue > MAX_MAX_HISTORY_TURNS
  ) {
    return DEFAULT_MAX_HISTORY_TURNS;
  }

  return parsedValue;
};
