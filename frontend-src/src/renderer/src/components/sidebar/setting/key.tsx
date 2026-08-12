import { Stack } from '@chakra-ui/react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWebSocket } from '@/context/websocket-context';
import {
  DEEPSEEK_API_KEY_STORAGE_KEY,
  getStoredApiKeys,
  QWEN_API_KEY_STORAGE_KEY,
} from '@/constants/api-keys';
import { InputField } from './common';
import { settingStyles } from './setting-styles';

interface KeyProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

interface ApiKeySettings {
  deepseekApiKey: string;
  qwenApiKey: string;
}

function Key({ onSave, onCancel }: KeyProps): JSX.Element {
  const { t } = useTranslation();
  const { sendMessage } = useWebSocket();
  const initialSettings = getStoredApiKeys();
  const [settings, setSettings] = useState<ApiKeySettings>(initialSettings);
  const [originalSettings, setOriginalSettings] = useState<ApiKeySettings>(
    initialSettings,
  );

  const handleSave = useCallback((): void => {
    localStorage.setItem(
      DEEPSEEK_API_KEY_STORAGE_KEY,
      JSON.stringify(settings.deepseekApiKey.trim()),
    );
    localStorage.setItem(
      QWEN_API_KEY_STORAGE_KEY,
      JSON.stringify(settings.qwenApiKey.trim()),
    );
    setOriginalSettings(settings);
    sendMessage({
      type: 'set-api-keys',
      deepseek_api_key: settings.deepseekApiKey.trim(),
      qwen_api_key: settings.qwenApiKey.trim(),
    });
  }, [sendMessage, settings]);

  const handleCancel = useCallback((): void => {
    setSettings(originalSettings);
  }, [originalSettings]);

  useEffect(() => {
    if (!onSave || !onCancel) return undefined;
    const cleanupSave = onSave(handleSave);
    const cleanupCancel = onCancel(handleCancel);
    return () => {
      cleanupSave?.();
      cleanupCancel?.();
    };
  }, [handleCancel, handleSave, onCancel, onSave]);

  return (
    <Stack {...settingStyles.common.container}>
      <InputField
        label={t('settings.key.deepseek')}
        value={settings.deepseekApiKey}
        onChange={(deepseekApiKey) => setSettings((current) => ({
          ...current,
          deepseekApiKey,
        }))}
        placeholder={t('settings.key.deepseekPlaceholder')}
      />
      <InputField
        label={t('settings.key.qwen')}
        value={settings.qwenApiKey}
        onChange={(qwenApiKey) => setSettings((current) => ({
          ...current,
          qwenApiKey,
        }))}
        placeholder={t('settings.key.qwenPlaceholder')}
      />
    </Stack>
  );
}

export default Key;
