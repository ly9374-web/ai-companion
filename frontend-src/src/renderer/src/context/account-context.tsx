import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { getCurrentBaseUrl } from '@/constants/connection-settings';
import {
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

type AccountFailure = 'invalid-account' | 'network' | 'server' | 'request';

interface AccountContextValue {
  account: string | null;
  loading: boolean;
  login: (name: string) => Promise<AccountResult>;
  register: (name: string) => Promise<AccountResult>;
  logout: () => void;
}

const AccountContext = createContext<AccountContextValue | null>(null);

const requestAccount = async (
  action: 'login' | 'register',
  name: string,
): Promise<{ account?: string; error?: string; failure?: AccountFailure }> => {
  try {
    const response = await fetch(`${getCurrentBaseUrl()}/api/accounts/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: name }),
    });
    const responseText = await response.text();
    let payload: { account?: string; error?: string } = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) {
      const failure: AccountFailure = response.status === 404 && action === 'login'
        ? 'invalid-account'
        : response.status >= 500
          ? 'server'
          : 'request';
      return {
        error: payload.error || `账号操作失败（HTTP ${response.status}）`,
        failure,
      };
    }
    return { account: payload.account };
  } catch (_error) {
    return { error: '无法连接到服务器', failure: 'network' };
  }
};

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [account, setAccount] = useState<string | null>(null);
  const [loading, setLoading] = useState(isAccountSessionActive());

  const finishAuthentication = useCallback((canonicalAccount: string) => {
    rememberAuthenticatedAccount(canonicalAccount);
    wsService.setAccount(canonicalAccount);
    setAccount(canonicalAccount);
  }, []);

  const login = useCallback(async (name: string): Promise<AccountResult> => {
    const result = await requestAccount('login', name);
    if (!result.account) {
      return {
        ok: false,
        error: result.error || '账号错误',
        connectionError: result.failure === 'network',
      };
    }
    finishAuthentication(result.account);
    return { ok: true };
  }, [finishAuthentication]);

  const register = useCallback(async (name: string): Promise<AccountResult> => {
    const result = await requestAccount('register', name);
    if (!result.account) {
      return {
        ok: false,
        error: result.error || '注册失败',
        connectionError: result.failure === 'network',
      };
    }
    finishAuthentication(result.account);
    return { ok: true };
  }, [finishAuthentication]);

  const logout = useCallback(() => {
    audioTaskQueue.clearQueue();
    audioManager.stopCurrentAudioAndLipSync();
    wsService.setAccount(null);
    rememberLoggedOut();
    setAccount(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!isAccountSessionActive()) {
      setLoading(false);
      return;
    }
    const previousAccount = getLastAccountName();
    if (!previousAccount) {
      rememberLoggedOut();
      setLoading(false);
      return;
    }
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const authenticatePreviousAccount = async (attempt: number): Promise<void> => {
      const result = await requestAccount('login', previousAccount);
      if (disposed) return;
      if (result.account) {
        finishAuthentication(result.account);
        setLoading(false);
        return;
      }
      if (result.failure === 'invalid-account') {
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
    loading,
    login,
    register,
    logout,
  }), [account, loading, login, register, logout]);

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
