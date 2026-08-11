/* eslint-disable import/order */
/* eslint-disable no-use-before-define */
import { useState, useEffect, useRef } from 'react';
import { BgUrlContextState } from '@/context/bgurl-context';
import { useSubtitle } from '@/context/subtitle-context';
import { useCamera } from '@/context/camera-context';
import { useSwitchCharacter } from '@/hooks/utils/use-switch-character';
import { useConfig } from '@/context/character-config-context';
import i18n from 'i18next';
import {
  getStoredTtsInstructionPreset,
  getStoredTtsLanguageHint,
  getStoredTtsVoice,
  getTtsInstruction,
  TTS_INSTRUCTION_PRESET_STORAGE_KEY,
  TTS_LANGUAGE_HINT_STORAGE_KEY,
  TTS_VOICE_STORAGE_KEY,
} from '@/constants/qwen-tts-voices';
import {
  getStoredMaxHistoryTurns,
  MAX_HISTORY_TURNS_STORAGE_KEY,
} from '@/constants/max-history-turns';

interface GeneralSettings {
  language: string[]
  ttsVoice: string[]
  ttsLanguageHint: string[]
  ttsInstructionPreset: string[]
  selectedCharacterPreset: string[]
  useCameraBackground: boolean
  showSubtitle: boolean
  maxHistoryTurns: number;
}

interface UseGeneralSettingsProps {
  bgUrlContext: BgUrlContextState | null
  confName: string | undefined
  setConfName: (name: string) => void
  onQwenTtsOptionsChange: (options: {
    voice: string
    language_hint: string
    instruction: string
    notify_ai: boolean
  }) => void
  onMaxHistoryTurnsChange: (value: number) => void
  onSave?: (callback: () => void) => () => void
  onCancel?: (callback: () => void) => () => void
}

export const useGeneralSettings = ({
  bgUrlContext,
  confName,
  setConfName,
  onQwenTtsOptionsChange,
  onMaxHistoryTurnsChange,
  onSave,
  onCancel,
}: UseGeneralSettingsProps) => {
  const { showSubtitle, setShowSubtitle } = useSubtitle();
  const { setUseCameraBackground } = bgUrlContext || {};
  const { startBackgroundCamera, stopBackgroundCamera } = useCamera();
  const { configFiles, getFilenameByName } = useConfig();
  const { switchCharacter } = useSwitchCharacter();

  const getCurrentCharacterFilename = (): string[] => {
    if (!confName) return [];
    const filename = getFilenameByName(confName);
    return filename ? [filename] : [];
  };

  const initialSettings: GeneralSettings = {
    language: [i18n.language || 'en'],
    ttsVoice: [getStoredTtsVoice()],
    ttsLanguageHint: [getStoredTtsLanguageHint()],
    ttsInstructionPreset: [getStoredTtsInstructionPreset()],
    selectedCharacterPreset: getCurrentCharacterFilename(),
    useCameraBackground: bgUrlContext?.useCameraBackground || false,
    showSubtitle,
    maxHistoryTurns: getStoredMaxHistoryTurns(),
  };

  const [settings, setSettings] = useState<GeneralSettings>(initialSettings);
  const [originalSettings, setOriginalSettings] = useState<GeneralSettings>(initialSettings);
  const lastTtsOptionsRef = useRef({
    voice: initialSettings.ttsVoice[0],
    language_hint: initialSettings.ttsLanguageHint[0],
    instruction: getTtsInstruction(initialSettings.ttsInstructionPreset[0]),
  });
  const originalConfName = confName;

  useEffect(() => {
    setShowSubtitle(settings.showSubtitle);

    // Apply language change if it differs from current language
    if (settings.language && settings.language[0] && settings.language[0] !== i18n.language) {
      i18n.changeLanguage(settings.language[0]);
    }
    localStorage.setItem(
      MAX_HISTORY_TURNS_STORAGE_KEY,
      settings.maxHistoryTurns.toString(),
    );
    onMaxHistoryTurnsChange(settings.maxHistoryTurns);
  }, [settings, onMaxHistoryTurnsChange, setShowSubtitle]);

  useEffect(() => {
    const selectedTtsVoice = settings.ttsVoice[0];
    const selectedLanguageHint = settings.ttsLanguageHint[0];
    const selectedInstructionPreset = settings.ttsInstructionPreset[0];
    if (selectedTtsVoice && selectedLanguageHint && selectedInstructionPreset) {
      const nextOptions = {
        voice: selectedTtsVoice,
        language_hint: selectedLanguageHint,
        instruction: getTtsInstruction(selectedInstructionPreset),
      };
      const previousOptions = lastTtsOptionsRef.current;
      if (
        nextOptions.voice === previousOptions.voice
        && nextOptions.language_hint === previousOptions.language_hint
        && nextOptions.instruction === previousOptions.instruction
      ) {
        return;
      }

      localStorage.setItem(TTS_VOICE_STORAGE_KEY, JSON.stringify(selectedTtsVoice));
      localStorage.setItem(
        TTS_LANGUAGE_HINT_STORAGE_KEY,
        JSON.stringify(selectedLanguageHint),
      );
      localStorage.setItem(
        TTS_INSTRUCTION_PRESET_STORAGE_KEY,
        JSON.stringify(selectedInstructionPreset),
      );
      onQwenTtsOptionsChange({
        ...nextOptions,
        notify_ai: nextOptions.voice !== previousOptions.voice
          || nextOptions.language_hint !== previousOptions.language_hint,
      });
      lastTtsOptionsRef.current = nextOptions;
    }
  }, [
    settings.ttsVoice,
    settings.ttsLanguageHint,
    settings.ttsInstructionPreset,
    onQwenTtsOptionsChange,
  ]);

  useEffect(() => {
    if (confName) {
      const filename = getFilenameByName(confName);
      if (filename) {
        const newSettings = {
          ...settings,
          selectedCharacterPreset: [filename],
        };
        setSettings(newSettings);
        setOriginalSettings(newSettings);
      }
    }
  }, [confName]);

  // Add save/cancel effect
  useEffect(() => {
    if (!onSave || !onCancel) return;

    const cleanupSave = onSave(() => {
      handleSave();
    });

    const cleanupCancel = onCancel(() => {
      handleCancel();
    });

    return () => {
      cleanupSave?.();
      cleanupCancel?.();
    };
  }, [onSave, onCancel]);

  const handleSettingChange = (
    key: keyof GeneralSettings,
    value: GeneralSettings[keyof GeneralSettings],
  ): void => {
    setSettings((prev) => ({ ...prev, [key]: value }));

    // Immediately change language when it's updated
    if (key === 'language' && Array.isArray(value) && value.length > 0) {
      i18n.changeLanguage(value[0]);
    }
  };

  const handleSave = (): void => {
    setOriginalSettings(settings);
  };

  const handleCancel = (): void => {
    setSettings(originalSettings);

    // Restore all settings to original values
    setShowSubtitle(originalSettings.showSubtitle);
    if (bgUrlContext) {
      bgUrlContext.setUseCameraBackground(originalSettings.useCameraBackground);
    }

    // Restore original character preset
    if (originalConfName) {
      setConfName(originalConfName);
    }

    // Handle camera state
    if (originalSettings.useCameraBackground) {
      startBackgroundCamera();
    } else {
      stopBackgroundCamera();
    }
  };

  const handleCharacterPresetChange = (value: string[]): void => {
    const selectedFilename = value[0];
    const selectedConfig = configFiles.find((config) => config.filename === selectedFilename);
    const currentFilename = confName ? getFilenameByName(confName) : '';

    handleSettingChange('selectedCharacterPreset', value);

    if (currentFilename === selectedFilename) {
      return;
    }

    if (selectedConfig) {
      switchCharacter(selectedFilename);
    }
  };

  const handleCameraToggle = async (checked: boolean) => {
    if (!setUseCameraBackground) return;

    if (checked) {
      try {
        await startBackgroundCamera();
        handleSettingChange('useCameraBackground', true);
        setUseCameraBackground(true);
      } catch (error) {
        console.error('Failed to start camera:', error);
        handleSettingChange('useCameraBackground', false);
        setUseCameraBackground(false);
      }
    } else {
      stopBackgroundCamera();
      handleSettingChange('useCameraBackground', false);
      setUseCameraBackground(false);
    }
  };

  return {
    settings,
    handleSettingChange,
    handleSave,
    handleCancel,
    handleCameraToggle,
    handleCharacterPresetChange,
    showSubtitle,
    setShowSubtitle,
  };
};
