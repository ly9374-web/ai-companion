import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { getCurrentBaseUrl } from '@/constants/connection-settings';
import {
  getAccountSessionToken,
  getLastAccountName,
  isAccountSessionActive,
  rememberAuthenticatedAccount,
  rememberLoggedOut,
} from '@/constants/account-settings';
import { wsService } from '@/services/websocket-service';
import { audioManager } from '@/utils/audio-manager';
import { audioTaskQueue } from '@/utils/task-queue';

interface AccountResult {
  ok: boolean;
  error?: string;
  connectionError?: boolean;
}

interface AccountFeatures {
  conversationStarters: boolean;
}

type AccountFailure = 'authentication' | 'network' | 'server' | 'request';

interface AccountContextValue {
  account: string | null;
  features: AccountFeatures;
  loading: boolean;
  login: (name: string, password: string) => Promise<AccountResult>;
  register: (name: string, password: string) => Promise<AccountResult>;
  logout: () => void;
}

const AccountContext = createContext<AccountContextValue | null>(null);

const requestAccount = async (
  action: 'login' | 'register' | 'session',
  name: string,
  credentials: { password?: string; sessionToken?: string },
): Promise<{
  account?: string;
  sessionToken?: string;
  features?: Partial<AccountFeatures>;
  error?: string;
  failure?: AccountFailure;
}> => {
  try {
    const response = await fetch(`${getCurrentBaseUrl()}/api/accounts/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: name, ...credentials }),
    });
    const responseText = await response.text();
    let payload: {
      account?: string;
      sessionToken?: string;
      features?: Partial<AccountFeatures>;
      error?: string;
    } = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) {
      const failure: AccountFailure = response.status === 401
        ? 'authentication'
        : response.status >= 500
          ? 'server'
          : 'request';
      return {
        error: payload.error || `账号操作失败（HTTP ${response.status}）`,
        failure,
      };
    }
    return {
      account: payload.account,
      sessionToken: payload.sessionToken,
      features: payload.features,
    };
  } catch (_error) {
    return { error: '无法连接到服务器', failure: 'network' };
  }
};

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [account, setAccount] = useState<string | null>(null);
  const [features, setFeatures] = useState<AccountFeatures>({
    conversationStarters: false,
  });
  const [loading, setLoading] = useState(isAccountSessionActive());

  const finishAuthentication = useCallback((
    canonicalAccount: string,
    sessionToken: string,
    nextFeatures: Partial<AccountFeatures> = {},
  ) => {
    rememberAuthenticatedAccount(canonicalAccount, sessionToken);
    wsService.setAccount(canonicalAccount, sessionToken);
    setAccount(canonicalAccount);
    setFeatures({
      conversationStarters: nextFeatures.conversationStarters === true,
    });
  }, []);

  const login = useCallback(async (
    name: string,
    password: string,
  ): Promise<AccountResult> => {
    const result = await requestAccount('login', name, { password });
    if (!result.account || !result.sessionToken) {
      return {
        ok: false,
        error: result.error || '账号错误',
        connectionError: result.failure === 'network',
      };
    }
    finishAuthentication(result.account, result.sessionToken, result.features);
    return { ok: true };
  }, [finishAuthentication]);

  const register = useCallback(async (
    name: string,
    password: string,
  ): Promise<AccountResult> => {
    const result = await requestAccount('register', name, { password });
    if (!result.account || !result.sessionToken) {
      return {
        ok: false,
        error: result.error || '注册失败',
        connectionError: result.failure === 'network',
      };
    }
    finishAuthentication(result.account, result.sessionToken, result.features);
    return { ok: true };
  }, [finishAuthentication]);

  const logout = useCallback(() => {
    const previousAccount = getLastAccountName();
    const sessionToken = getAccountSessionToken();
    if (previousAccount && sessionToken) {
      void fetch(`${getCurrentBaseUrl()}/api/accounts/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: previousAccount, sessionToken }),
      }).catch(() => undefined);
    }
    audioTaskQueue.clearQueue();
    audioManager.stopCurrentAudioAndLipSync();
    wsService.setAccount(null);
    rememberLoggedOut();
    setAccount(null);
    setFeatures({ conversationStarters: false });
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!isAccountSessionActive()) {
      setLoading(false);
      return;
    }
    const previousAccount = getLastAccountName();
    const sessionToken = getAccountSessionToken();
    if (!previousAccount || !sessionToken) {
      rememberLoggedOut();
      setLoading(false);
      return;
    }
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const authenticatePreviousAccount = async (attempt: number): Promise<void> => {
      const result = await requestAccount('session', previousAccount, { sessionToken });
      if (disposed) return;
      if (result.account) {
        finishAuthentication(result.account, sessionToken, result.features);
        setLoading(false);
        return;
      }
      if (result.failure === 'authentication') {
        rememberLoggedOut();
        setLoading(false);
        return;
      }
      if (result.failure === 'network' && attempt < 5) {
        retryTimer = setTimeout(() => {
          void authenticatePreviousAccount(attempt + 1);
        }, 1500);
        return;
      }
      // A transient network/server failure is not the same as an explicit
      // logout. Preserve the session flag so reopening can retry automatically.
      setLoading(false);
    };

    void authenticatePreviousAccount(0);
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [finishAuthentication]);

  const value = useMemo(() => ({
    account,
    features,
    loading,
    login,
    register,
    logout,
  }), [account, features, loading, login, register, logout]);

  return (
    <AccountContext.Provider value={value}>
      {children}
    </AccountContext.Provider>
  );
}

export function useAccount(): AccountContextValue {
  const context = useContext(AccountContext);
  if (!context) throw new Error('useAccount must be used within AccountProvider');
  return context;
}
