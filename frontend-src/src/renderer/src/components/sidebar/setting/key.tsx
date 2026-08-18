import { Stack, createListCollection } from '@chakra-ui/react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWebSocket } from '@/context/websocket-context';
import {
  DEEPSEEK_API_KEY_STORAGE_KEY,
  DEEPSEEK_MODEL_OPTIONS,
  DEEPSEEK_MODEL_STORAGE_KEY,
  getStoredApiKeys,
  GROK_API_KEY_STORAGE_KEY,
  isGrokEnabledForPageSession,
  QWEN_API_KEY_STORAGE_KEY,
  setGrokEnabledForPageSession,
} from '@/constants/api-keys';
import { InputField, SelectField, SwitchField } from './common';
import { settingStyles } from './setting-styles';

interface KeyProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

interface ApiKeySettings {
  deepseekApiKey: string;
  deepseekModel: string;
  grokApiKey: string;
  grokEnabled: boolean;
  qwenApiKey: string;
}

const deepseekModels = createListCollection({
  items: DEEPSEEK_MODEL_OPTIONS.map((option) => ({
    label: option.label,
    value: option.value,
  })),
});

function Key({ onSave, onCancel }: KeyProps): JSX.Element {
  const { t } = useTranslation();
  const { sendMessage } = useWebSocket();
  const initialSettings = {
    ...getStoredApiKeys(),
    grokEnabled: isGrokEnabledForPageSession(),
  };
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
      DEEPSEEK_MODEL_STORAGE_KEY,
      JSON.stringify(settings.deepseekModel),
    );
    localStorage.setItem(
      GROK_API_KEY_STORAGE_KEY,
      JSON.stringify(settings.grokApiKey.trim()),
    );
    localStorage.setItem(
      QWEN_API_KEY_STORAGE_KEY,
      JSON.stringify(settings.qwenApiKey.trim()),
    );
    setGrokEnabledForPageSession(settings.grokEnabled);
    setOriginalSettings(settings);
    sendMessage({
      type: 'set-api-keys',
      deepseek_api_key: settings.deepseekApiKey.trim(),
      deepseek_model: settings.deepseekModel,
      grok_api_key: settings.grokApiKey.trim(),
      grok_enabled: settings.grokEnabled,
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
      <SelectField
        label={t('settings.key.deepseekModel')}
        value={[settings.deepseekModel]}
        onChange={(value) => setSettings((current) => ({
          ...current,
          deepseekModel: value[0] ?? current.deepseekModel,
        }))}
        collection={deepseekModels}
        placeholder={t('settings.key.deepseekModel')}
      />
      <InputField
        label={t('settings.key.grok')}
        value={settings.grokApiKey}
        onChange={(grokApiKey) => setSettings((current) => ({
          ...current,
          grokApiKey,
        }))}
        placeholder={t('settings.key.grokPlaceholder')}
      />
      <SwitchField
        label={t('settings.key.grokEnabled')}
        checked={settings.grokEnabled}
        onChange={(grokEnabled) => setSettings((current) => ({
          ...current,
          grokEnabled,
        }))}
        help={t('settings.key.grokEnabledHelp')}
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
