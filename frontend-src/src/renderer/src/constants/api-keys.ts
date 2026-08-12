export const DEEPSEEK_API_KEY_STORAGE_KEY = 'deepseekApiKey';
export const QWEN_API_KEY_STORAGE_KEY = 'qwenApiKey';

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
  qwenApiKey: string;
} {
  return {
    deepseekApiKey: getStoredString(DEEPSEEK_API_KEY_STORAGE_KEY),
    qwenApiKey: getStoredString(QWEN_API_KEY_STORAGE_KEY),
  };
}

export function hasStoredDeepSeekApiKey(): boolean {
  return getStoredApiKeys().deepseekApiKey.trim().length > 0;
}
