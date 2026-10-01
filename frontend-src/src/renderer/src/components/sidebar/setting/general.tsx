/* eslint-disable import/no-extraneous-dependencies */
import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Stack, createListCollection } from "@chakra-ui/react";
import { useBgUrl } from "@/context/bgurl-context";
import { settingStyles } from "./setting-styles";
import { useConfig } from "@/context/character-config-context";
import { useGeneralSettings } from "@/hooks/sidebar/setting/use-general-settings";
import { useWebSocket } from "@/context/websocket-context";
import { NumberField, SelectField, SwitchField } from "./common";
import { QWEN_TTS_VOICES } from "@/constants/qwen-tts-voices";
import {
  MAX_MAX_HISTORY_TURNS,
  MIN_MAX_HISTORY_TURNS,
} from "@/constants/max-history-turns";
import { toaster } from "@/components/ui/toaster";
import { ROLLING_SUMMARY_TOAST_ID } from "@/constants/manual-summary";
import { PERSONA_PROFILE_TOAST_ID } from "@/constants/persona-profile";
import {
  getGeneralRuntimeSettings,
  setGeneralRuntimeSettings,
  subscribeGeneralRuntimeSettings,
} from "@/constants/general-runtime-settings";
import { useAccount } from "@/context/account-context";
import { OptionalGeneralSettings } from "@optional-feature";
import CloudRestore from "./cloud-restore";

interface GeneralProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

// Data collection definition
const useCollections = () => {
  const { t } = useTranslation();
  const { configFiles } = useConfig();

  const languages = createListCollection({
    items: [
      { label: "English", value: "en" },
      { label: "中文", value: "zh" },
    ],
  });

  const ttsVoices = createListCollection({
    items: [...QWEN_TTS_VOICES],
  });

  const ttsInstructionPresets = createListCollection({
    items: [
      { label: t("settings.general.ttsInstructionNone"), value: "none" },
      { label: "instruction1", value: "instruction1" },
    ],
  });

  const characterPresets = createListCollection({
    items: configFiles.map((config) => ({
      label: config.name,
      value: config.filename,
    })),
  });

  return {
    languages,
    ttsVoices,
    ttsInstructionPresets,
    characterPresets,
  };
};

function General({ onSave, onCancel }: GeneralProps): JSX.Element {
  const { t, i18n } = useTranslation();
  const { logout } = useAccount();
  const bgUrlContext = useBgUrl();
  const { confName, setConfName } = useConfig();
  const {
    sendMessage,
    wsState,
  } = useWebSocket();
  const collections = useCollections();
  const initialRuntimeSettings = getGeneralRuntimeSettings();
  const [generateAudio, setGenerateAudio] = useState(
    initialRuntimeSettings.generateAudio,
  );
  const [debugMode, setDebugMode] = useState(initialRuntimeSettings.debugMode);
  const runtimeSettingsRef = useRef({ generateAudio, debugMode });
  const savedRuntimeSettingsRef = useRef(initialRuntimeSettings);
  runtimeSettingsRef.current = { generateAudio, debugMode };

  useEffect(() => subscribeGeneralRuntimeSettings((nextSettings) => {
    setGenerateAudio(nextSettings.generateAudio);
    setDebugMode(nextSettings.debugMode);
    savedRuntimeSettingsRef.current = nextSettings;
  }), []);

  const handleGenerateAudioChange = (enabled: boolean): void => {
    setGenerateAudio(enabled);

    if (wsState === "OPEN") {
      sendMessage({ type: "set-generate-audio", enabled });
    }
  };

  const handleDebugModeChange = (enabled: boolean): void => {
    setDebugMode(enabled);

    if (wsState === "OPEN") {
      sendMessage({ type: "set-debug-mode", enabled });
    }
  };

  useEffect(() => {
    if (!onSave || !onCancel) return undefined;

    const removeSave = onSave(() => {
      const nextSettings = runtimeSettingsRef.current;
      savedRuntimeSettingsRef.current = nextSettings;
      setGeneralRuntimeSettings(nextSettings);
    });
    const removeCancel = onCancel(() => {
      const savedSettings = savedRuntimeSettingsRef.current;
      setGenerateAudio(savedSettings.generateAudio);
      setDebugMode(savedSettings.debugMode);
      if (wsState === "OPEN") {
        sendMessage({
          type: "set-generate-audio",
          enabled: savedSettings.generateAudio,
        });
        sendMessage({ type: "set-debug-mode", enabled: savedSettings.debugMode });
      }
    });

    return () => {
      removeSave?.();
      removeCancel?.();
    };
  }, [onSave, onCancel, sendMessage, wsState]);

  const handleRollingSummary = (): void => {
    const loadingToast = {
      title: t("notification.rollingSummaryRunning"),
      type: "loading" as const,
    };
    if (toaster.isVisible(ROLLING_SUMMARY_TOAST_ID)) {
      toaster.update(ROLLING_SUMMARY_TOAST_ID, loadingToast);
    } else {
      toaster.create({ id: ROLLING_SUMMARY_TOAST_ID, ...loadingToast });
    }
    if (wsState !== "OPEN") {
      toaster.update(ROLLING_SUMMARY_TOAST_ID, {
        title: t("error.websocketNotOpen"),
        type: "error",
        duration: 3000,
      });
      return;
    }
    sendMessage({ type: "summarize-rolling-context" });
  };

  const handlePersonaProfile = (): void => {
    const loadingToast = {
      title: t("notification.personaProfileStarting"),
      type: "loading" as const,
    };
    if (toaster.isVisible(PERSONA_PROFILE_TOAST_ID)) {
      toaster.update(PERSONA_PROFILE_TOAST_ID, loadingToast);
    } else {
      toaster.create({ id: PERSONA_PROFILE_TOAST_ID, ...loadingToast });
    }
    if (wsState !== "OPEN") {
      toaster.update(PERSONA_PROFILE_TOAST_ID, {
        title: t("error.websocketNotOpen"),
        type: "error",
        duration: 3000,
      });
      return;
    }
    sendMessage({ type: "generate-persona-profile" });
  };

  const handleQwenTtsOptionsChange = useCallback(
    (options: {
      voice: string;
      instruction: string;
      notify_ai: boolean;
    }) => {
      if (wsState === "OPEN") {
        sendMessage({ type: "set-qwen-tts-options", ...options });
      }
    },
    [sendMessage, wsState],
  );

  const handleMaxHistoryTurnsChange = useCallback(
    (value: number) => {
      if (wsState === "OPEN") {
        sendMessage({
          type: "set-max-history-turns",
          max_history_turns: value,
        });
      }
    },
    [sendMessage, wsState],
  );

  const {
    settings,
    handleSettingChange,
    handleCharacterPresetChange,
  } = useGeneralSettings({
    bgUrlContext,
    confName,
    setConfName,
    onQwenTtsOptionsChange: handleQwenTtsOptionsChange,
    onMaxHistoryTurnsChange: handleMaxHistoryTurnsChange,
    onSave,
    onCancel,
  });

  if (settings.language[0] !== i18n.language) {
    handleSettingChange("language", [i18n.language]);
  }

  return (
    <Stack {...settingStyles.common.container}>
      <SelectField
        label={t("settings.general.language")}
        value={settings.language}
        onChange={(value) => handleSettingChange("language", value)}
        collection={collections.languages}
        placeholder={t("settings.general.language")}
      />

      <SelectField
        label={t("settings.general.ttsVoice")}
        value={settings.ttsVoice}
        onChange={(value) => handleSettingChange("ttsVoice", value)}
        collection={collections.ttsVoices}
        placeholder={t("settings.general.ttsVoice")}
      />

      <SelectField
        label={t("settings.general.ttsInstruction")}
        value={settings.ttsInstructionPreset}
        onChange={(value) => handleSettingChange("ttsInstructionPreset", value)}
        collection={collections.ttsInstructionPresets}
        placeholder={t("settings.general.ttsInstruction")}
      />

      <SwitchField
        label={t("settings.general.generateAudio")}
        checked={generateAudio}
        onChange={handleGenerateAudioChange}
        help={t("settings.general.generateAudioHelp")}
      />

      <SelectField
        label={t("settings.general.characterPreset")}
        value={settings.selectedCharacterPreset}
        onChange={handleCharacterPresetChange}
        collection={collections.characterPresets}
        placeholder={confName || t("settings.general.characterPreset")}
      />

      <NumberField
        label={t("settings.general.maxHistoryTurns")}
        value={settings.maxHistoryTurns}
        onChange={(value) => {
          const turns = Number.parseInt(value, 10);
          if (
            !Number.isNaN(turns)
            && turns >= MIN_MAX_HISTORY_TURNS
            && turns <= MAX_MAX_HISTORY_TURNS
          ) {
            handleSettingChange("maxHistoryTurns", turns);
          }
        }}
        min={MIN_MAX_HISTORY_TURNS}
        max={MAX_MAX_HISTORY_TURNS}
        step={1}
        allowMouseWheel
        help={t("settings.general.maxHistoryTurnsHelp")}
      />

      <SwitchField
        label={t("settings.general.debugMode")}
        checked={debugMode}
        onChange={handleDebugModeChange}
        help={t("settings.general.debugModeHelp")}
      />

      <OptionalGeneralSettings onSave={onSave} onCancel={onCancel} />

      <Button colorPalette="purple" onClick={handlePersonaProfile}>
        {t("settings.general.personaProfile")}
      </Button>

      {debugMode && (
        <Button colorPalette="blue" onClick={handleRollingSummary}>
          {t("settings.general.rollingSummary")}
        </Button>
      )}

      <CloudRestore />

      <Button colorPalette="red" variant="outline" onClick={logout}>
        {t("account.logout")}
      </Button>
    </Stack>
  );
}

export default General;
