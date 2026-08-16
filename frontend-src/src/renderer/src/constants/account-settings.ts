const LAST_ACCOUNT_KEY = 'lastAccountName';
const ACCOUNT_SESSION_ACTIVE_KEY = 'accountSessionActive';
const LEGACY_BACKGROUND_KEY = 'backgroundUrl';

const readStoredString = (key: string): string => {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : '';
  } catch (_error) {
    return '';
  }
};

const accountBackgroundKey = (account: string): string => (
  `backgroundUrl:${encodeURIComponent(account)}`
);

export const getLastAccountName = (): string => (
  readStoredString(LAST_ACCOUNT_KEY) || 'Jason'
);

export const isAccountSessionActive = (): boolean => {
  try {
    const stored = localStorage.getItem(ACCOUNT_SESSION_ACTIVE_KEY);
    return stored === null || stored === 'true';
  } catch (_error) {
    return false;
  }
};

export const rememberAuthenticatedAccount = (account: string): void => {
  localStorage.setItem(LAST_ACCOUNT_KEY, JSON.stringify(account));
  localStorage.setItem(ACCOUNT_SESSION_ACTIVE_KEY, 'true');
};

export const rememberLoggedOut = (): void => {
  localStorage.setItem(ACCOUNT_SESSION_ACTIVE_KEY, 'false');
};

export const getStoredAccountBackground = (
  account: string,
  fallback: string,
): string => {
  const scoped = account ? readStoredString(accountBackgroundKey(account)) : '';
  const legacy = account.toLocaleLowerCase() === 'jason'
    ? readStoredString(LEGACY_BACKGROUND_KEY)
    : '';
  return scoped || legacy || fallback;
};

export const setStoredAccountBackground = (
  account: string,
  backgroundUrl: string,
): void => {
  if (!account) return;
  localStorage.setItem(
    accountBackgroundKey(account),
    JSON.stringify(backgroundUrl),
  );
};

export const getLastAccountBackground = (fallback: string): string => (
  getStoredAccountBackground(getLastAccountName(), fallback)
);
