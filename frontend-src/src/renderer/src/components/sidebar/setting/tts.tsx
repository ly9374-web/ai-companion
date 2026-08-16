import { Stack, createListCollection } from '@chakra-ui/react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useBgUrl } from '@/context/bgurl-context';
import {
  defaultBaseUrl,
  defaultWsUrl,
  useWebSocket,
} from '@/context/websocket-context';
import {
  getStoredImageCompressionQuality,
  getStoredImageMaxWidth,
  setCurrentImageSettings,
} from '@/constants/image-settings';
import { InputField, SelectField } from './common';
import { settingStyles } from './setting-styles';

interface TTSProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

interface SynthesisSettings {
  customBgUrl: string;
  selectedBgUrl: string[];
  backgroundUrl: string;
  wsUrl: string;
  baseUrl: string;
  imageCompressionQuality: number;
  imageMaxWidth: number;
}

function TTS({ onSave, onCancel }: TTSProps): JSX.Element {
  const { t } = useTranslation();
  const bgUrlContext = useBgUrl();
  const {
    wsUrl,
    setWsUrl,
    baseUrl,
    setBaseUrl,
  } = useWebSocket();

  const currentBackgroundUrl = bgUrlContext?.backgroundUrl || '';
  const currentBackgroundPath = currentBackgroundUrl.replace(baseUrl, '');
  const initialSettings: SynthesisSettings = {
    customBgUrl: !currentBackgroundUrl.includes('/bg/')
      ? currentBackgroundUrl
      : '',
    selectedBgUrl: currentBackgroundPath.startsWith('/bg/')
      ? [currentBackgroundPath]
      : [],
    backgroundUrl: currentBackgroundUrl,
    wsUrl: wsUrl || defaultWsUrl,
    baseUrl: baseUrl || defaultBaseUrl,
    imageCompressionQuality: getStoredImageCompressionQuality(),
    imageMaxWidth: getStoredImageMaxWidth(),
  };

  const [settings, setSettings] = useState<SynthesisSettings>(initialSettings);
  const [originalSettings, setOriginalSettings] = useState<SynthesisSettings>(initialSettings);

  const backgrounds = createListCollection({
    items:
      bgUrlContext?.backgroundFiles?.map((filename) => ({
        label: String(filename),
        value: `/bg/${filename}`,
      })) || [],
  });

  useEffect(() => {
    const newBgUrl = settings.customBgUrl || settings.selectedBgUrl[0];
    if (newBgUrl && bgUrlContext) {
      const fullUrl = newBgUrl.startsWith('http')
        ? newBgUrl
        : `${settings.baseUrl}${newBgUrl}`;
      bgUrlContext.setBackgroundUrl(fullUrl);
    }

    setWsUrl(settings.wsUrl);
    setBaseUrl(settings.baseUrl);
  }, [settings, bgUrlContext, setWsUrl, setBaseUrl]);

  const handleSave = useCallback((): void => {
    setOriginalSettings(settings);
    setCurrentImageSettings(
      settings.imageCompressionQuality,
      settings.imageMaxWidth,
    );
  }, [settings]);

  const handleCancel = useCallback((): void => {
    setSettings(originalSettings);
    bgUrlContext?.setBackgroundUrl(originalSettings.backgroundUrl);
    setWsUrl(originalSettings.wsUrl);
    setBaseUrl(originalSettings.baseUrl);
  }, [originalSettings, bgUrlContext, setWsUrl, setBaseUrl]);

  useEffect(() => {
    if (!onSave || !onCancel) return undefined;

    const cleanupSave = onSave(handleSave);
    const cleanupCancel = onCancel(handleCancel);

    return () => {
      cleanupSave?.();
      cleanupCancel?.();
    };
  }, [onSave, onCancel, handleSave, handleCancel]);

  const handleSettingChange = <K extends keyof SynthesisSettings,>(
    key: K,
    value: SynthesisSettings[K],
  ): void => {
    setSettings((previous) => ({ ...previous, [key]: value }));
  };

  return (
    <Stack {...settingStyles.common.container}>
      <SelectField
        label={t('settings.tts.backgroundImage')}
        value={settings.selectedBgUrl}
        onChange={(value) => {
          setSettings((previous) => ({
            ...previous,
            selectedBgUrl: value,
            customBgUrl: '',
          }));
        }}
        collection={backgrounds}
        placeholder={t('settings.tts.backgroundImage')}
      />

      <InputField
        label={t('settings.tts.customBgUrl')}
        value={settings.customBgUrl}
        onChange={(value) => {
          setSettings((previous) => ({
            ...previous,
            customBgUrl: value,
            selectedBgUrl: value ? [] : previous.selectedBgUrl,
          }));
        }}
        placeholder={t('settings.tts.customBgUrlPlaceholder')}
      />

      <InputField
        label={t('settings.tts.wsUrl')}
        value={settings.wsUrl}
        onChange={(value) => handleSettingChange('wsUrl', value)}
        placeholder="Enter WebSocket URL"
      />

      <InputField
        label={t('settings.tts.baseUrl')}
        value={settings.baseUrl}
        onChange={(value) => handleSettingChange('baseUrl', value)}
        placeholder="Enter Base URL"
      />

      <InputField
        label={t('settings.tts.imageCompressionQuality')}
        value={settings.imageCompressionQuality.toString()}
        onChange={(value) => {
          const quality = Number.parseFloat(value);
          if (!Number.isNaN(quality) && quality >= 0.1 && quality <= 1.0) {
            handleSettingChange('imageCompressionQuality', quality);
          }
        }}
        help={t('settings.tts.imageCompressionQualityHelp')}
      />

      <InputField
        label={t('settings.tts.imageMaxWidth')}
        value={settings.imageMaxWidth.toString()}
        onChange={(value) => {
          const maxWidth = Number.parseInt(value, 10);
          if (!Number.isNaN(maxWidth) && maxWidth >= 0) {
            handleSettingChange('imageMaxWidth', maxWidth);
          }
        }}
        help={t('settings.tts.imageMaxWidthHelp')}
      />
    </Stack>
  );
}

export default TTS;
