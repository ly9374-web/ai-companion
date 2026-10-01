const browserOrigin = typeof window !== 'undefined'
  && (window.location.protocol === 'http:' || window.location.protocol === 'https:')
  ? window.location.origin : null;

export const DEFAULT_WS_URL = browserOrigin
  ? `${browserOrigin.replace(/^http/, 'ws')}/client-ws`
  : 'ws://127.0.0.1:12393/client-ws';
export const DEFAULT_BASE_URL = browserOrigin || 'http://127.0.0.1:12393';

const readStoredString = (key: string, fallback: string): string => {
  try {
    const stored = localStorage.getItem(key);
    return stored ? JSON.parse(stored) : fallback;
  } catch (_error) {
    return fallback;
  }
};

export const getCurrentWsUrl = (): string => readStoredString('wsUrl', DEFAULT_WS_URL);
export const getCurrentBaseUrl = (): string => readStoredString('baseUrl', DEFAULT_BASE_URL);

export const setCurrentWsUrl = (url: string): void => {
  localStorage.setItem('wsUrl', JSON.stringify(url));
};

export const setCurrentBaseUrl = (url: string): void => {
  localStorage.setItem('baseUrl', JSON.stringify(url));
};
