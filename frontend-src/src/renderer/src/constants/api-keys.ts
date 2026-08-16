export const DEEPSEEK_API_KEY_STORAGE_KEY = 'deepseekApiKey';
export const GROK_API_KEY_STORAGE_KEY = 'grokApiKey';
export const QWEN_API_KEY_STORAGE_KEY = 'qwenApiKey';

let grokEnabledForPageSession = false;

function getStoredString(key: string): string {
  try {
    const value = localStorage.getItem(key);
    if (!value) return '';
    const parsed = JSON.parse(value);
    return typeof parsed === 'string' ? parsed : '';
  } catch {
    return '';
  }
}

export function getStoredApiKeys(): {
  deepseekApiKey: string;
  grokApiKey: string;
  qwenApiKey: string;
} {
  return {
    deepseekApiKey: getStoredString(DEEPSEEK_API_KEY_STORAGE_KEY),
    grokApiKey: getStoredString(GROK_API_KEY_STORAGE_KEY),
    qwenApiKey: getStoredString(QWEN_API_KEY_STORAGE_KEY),
  };
}

export function isGrokEnabledForPageSession(): boolean {
  return grokEnabledForPageSession;
}

export function setGrokEnabledForPageSession(enabled: boolean): void {
  grokEnabledForPageSession = enabled;
}

export function hasStoredDeepSeekApiKey(): boolean {
  return getStoredApiKeys().deepseekApiKey.trim().length > 0;
}

export function getMissingChatApiKeyProvider(): 'deepseek' | 'grok' | null {
  const apiKeys = getStoredApiKeys();
  if (!apiKeys.deepseekApiKey.trim()) return 'deepseek';
  if (grokEnabledForPageSession && !apiKeys.grokApiKey.trim()) return 'grok';
  return null;
}
