/* eslint-disable import/order */
/* eslint-disable no-use-before-define */
import { useState, useEffect, useRef } from 'react';
import { BgUrlContextState } from '@/context/bgurl-context';
import { useSubtitle } from '@/context/subtitle-context';
import { useSwitchCharacter } from '@/hooks/utils/use-switch-character';
import { useConfig } from '@/context/character-config-context';
import i18n from 'i18next';
import {
  getStoredTtsInstructionPreset,
  getStoredTtsVoice,
  getTtsInstruction,
  setCurrentQwenTtsSettings,
} from '@/constants/qwen-tts-voices';
import {
  getStoredMaxHistoryTurns,
  setCurrentMaxHistoryTurns,
} from '@/constants/max-history-turns';
import { setStoredCharacterPreset } from '@/constants/character-settings';

interface GeneralSettings {
  language: string[]
  ttsVoice: string[]
  ttsInstructionPreset: string[]
  selectedCharacterPreset: string[]
  showSubtitle: boolean
  maxHistoryTurns: number;
}

interface UseGeneralSettingsProps {
  bgUrlContext: BgUrlContextState | null
  confName: string | undefined
  setConfName: (name: string) => void
  onQwenTtsOptionsChange: (options: {
    voice: string
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
    ttsInstructionPreset: [getStoredTtsInstructionPreset()],
    selectedCharacterPreset: getCurrentCharacterFilename(),
    showSubtitle,
    maxHistoryTurns: getStoredMaxHistoryTurns(),
  };

  const [settings, setSettings] = useState<GeneralSettings>(initialSettings);
  const [originalSettings, setOriginalSettings] = useState<GeneralSettings>(initialSettings);
  const settingsRef = useRef(settings);
  const originalSettingsRef = useRef(originalSettings);
  settingsRef.current = settings;
  originalSettingsRef.current = originalSettings;
  const lastTtsOptionsRef = useRef({
    voice: initialSettings.ttsVoice[0],
    instruction: getTtsInstruction(initialSettings.ttsInstructionPreset[0]),
  });
  const originalConfName = confName;

  useEffect(() => {
    setShowSubtitle(settings.showSubtitle);

    // Apply language change if it differs from current language
    if (settings.language && settings.language[0] && settings.language[0] !== i18n.language) {
      i18n.changeLanguage(settings.language[0]);
    }
    onMaxHistoryTurnsChange(settings.maxHistoryTurns);
  }, [settings, onMaxHistoryTurnsChange, setShowSubtitle]);

  useEffect(() => {
    const selectedTtsVoice = settings.ttsVoice[0];
    const selectedInstructionPreset = settings.ttsInstructionPreset[0];
    if (selectedTtsVoice && selectedInstructionPreset) {
      const nextOptions = {
        voice: selectedTtsVoice,
        instruction: getTtsInstruction(selectedInstructionPreset),
      };
      const previousOptions = lastTtsOptionsRef.current;
      if (
        nextOptions.voice === previousOptions.voice
        && nextOptions.instruction === previousOptions.instruction
      ) {
        return;
      }

      onQwenTtsOptionsChange({
        ...nextOptions,
        notify_ai: nextOptions.voice !== previousOptions.voice,
      });
      lastTtsOptionsRef.current = nextOptions;
    }
  }, [
    settings.ttsVoice,
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
    const latestSettings = settingsRef.current;
    setOriginalSettings(latestSettings);
    originalSettingsRef.current = latestSettings;
    setCurrentQwenTtsSettings(
      latestSettings.ttsVoice[0],
      latestSettings.ttsInstructionPreset[0],
    );
    setCurrentMaxHistoryTurns(latestSettings.maxHistoryTurns);
    setStoredCharacterPreset(latestSettings.selectedCharacterPreset[0]);
  };

  const handleCancel = (): void => {
    const savedSettings = originalSettingsRef.current;
    setSettings(savedSettings);

    // Restore all settings to original values
    setShowSubtitle(savedSettings.showSubtitle);

    // Restore original character preset
    if (originalConfName) {
      setConfName(originalConfName);
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

  return {
    settings,
    handleSettingChange,
    handleSave,
    handleCancel,
    handleCharacterPresetChange,
    showSubtitle,
    setShowSubtitle,
  };
};
