export const DEEPSEEK_API_KEY_STORAGE_KEY = 'deepseekApiKey';
export const GROK_API_KEY_STORAGE_KEY = 'grokApiKey';
export const QWEN_API_KEY_STORAGE_KEY = 'qwenApiKey';
export const DEEPSEEK_MODEL_STORAGE_KEY = 'deepseekModel';

export const DEEPSEEK_MODEL_PRO = 'deepseek-v4-pro';
export const DEEPSEEK_MODEL_FLASH = 'deepseek-v4-flash';
export const DEEPSEEK_MODEL_OPTIONS = [
  { label: 'pro', value: DEEPSEEK_MODEL_PRO },
  { label: 'flash', value: DEEPSEEK_MODEL_FLASH },
];

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

export function getStoredDeepseekModel(): string {
  const value = getStoredString(DEEPSEEK_MODEL_STORAGE_KEY);
  return value === DEEPSEEK_MODEL_FLASH ? value : DEEPSEEK_MODEL_PRO;
}

export function getStoredApiKeys(): {
  deepseekApiKey: string;
  grokApiKey: string;
  qwenApiKey: string;
  deepseekModel: string;
} {
  return {
    deepseekApiKey: getStoredString(DEEPSEEK_API_KEY_STORAGE_KEY),
    grokApiKey: getStoredString(GROK_API_KEY_STORAGE_KEY),
    qwenApiKey: getStoredString(QWEN_API_KEY_STORAGE_KEY),
    deepseekModel: getStoredDeepseekModel(),
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
