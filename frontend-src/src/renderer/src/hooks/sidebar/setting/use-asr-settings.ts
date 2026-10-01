import React, { useRef, useState, useEffect } from 'react';
import { useVAD, VADSettings } from '@/context/vad-context';

export const useASRSettings = () => {
  const {
    settings,
    updateSettings,
    autoStopMic,
    setAutoStopMic,
    autoStartMicOn,
    setAutoStartMicOn,
    autoStartMicOnConvEnd,
    setAutoStartMicOnConvEnd,
    autoGenerateAudioOnMic,
    setAutoGenerateAudioOnMic,
  } = useVAD();

  const localSettingsRef = useRef<VADSettings>(settings);
  const originalSettingsRef = useRef(settings);
  const originalAutoStopMicRef = useRef(autoStopMic);
  const originalAutoStartMicOnRef = useRef(autoStartMicOn);
  const originalAutoStartMicOnConvEndRef = useRef(autoStartMicOnConvEnd);
  const originalAutoGenerateAudioOnMicRef = useRef(autoGenerateAudioOnMic);
  const [localVoiceInterruption, setLocalVoiceInterruption] = useState(autoStopMic);
  const [localAutoStartMic, setLocalAutoStartMic] = useState(autoStartMicOn);
  const [localAutoStartMicOnConvEnd, setLocalAutoStartMicOnConvEnd] = useState(autoStartMicOnConvEnd);
  const [localAutoGenerateAudioOnMic, setLocalAutoGenerateAudioOnMic] = useState(autoGenerateAudioOnMic);
  const [, forceUpdate] = React.useReducer((x) => x + 1, 0);

  useEffect(() => {
    setLocalVoiceInterruption(autoStopMic);
    setLocalAutoStartMic(autoStartMicOn);
    setLocalAutoStartMicOnConvEnd(autoStartMicOnConvEnd);
    setLocalAutoGenerateAudioOnMic(autoGenerateAudioOnMic);
  }, [autoStopMic, autoStartMicOn, autoStartMicOnConvEnd, autoGenerateAudioOnMic]);

  const handleInputChange = (key: keyof VADSettings, value: number | string): void => {
    if (value === '' || value === '-') {
      localSettingsRef.current = { ...localSettingsRef.current, [key]: value };
    } else {
      const parsed = Number(value);
      // eslint-disable-next-line no-restricted-globals
      if (!isNaN(parsed)) {
        localSettingsRef.current = { ...localSettingsRef.current, [key]: parsed };
      }
    }
    forceUpdate();
  };

  const handleVoiceInterruptionChange = (value: boolean) => {
    setLocalVoiceInterruption(value);
    setAutoStopMic(value);
  };

  const handleAutoStartMicChange = (value: boolean) => {
    setLocalAutoStartMic(value);
    setAutoStartMicOn(value);
  };

  const handleAutoStartMicOnConvEndChange = (value: boolean) => {
    setLocalAutoStartMicOnConvEnd(value);
    setAutoStartMicOnConvEnd(value);
  };

  const handleAutoGenerateAudioOnMicChange = (value: boolean) => {
    setLocalAutoGenerateAudioOnMic(value);
    setAutoGenerateAudioOnMic(value);
  };

  const handleSave = (): void => {
    updateSettings(localSettingsRef.current);
    originalSettingsRef.current = localSettingsRef.current;
    originalAutoStopMicRef.current = localVoiceInterruption;
    originalAutoStartMicOnRef.current = localAutoStartMic;
    originalAutoStartMicOnConvEndRef.current = localAutoStartMicOnConvEnd;
    originalAutoGenerateAudioOnMicRef.current = localAutoGenerateAudioOnMic;
  };

  const handleCancel = (): void => {
    localSettingsRef.current = originalSettingsRef.current;
    setLocalVoiceInterruption(originalAutoStopMicRef.current);
    setLocalAutoStartMic(originalAutoStartMicOnRef.current);
    setAutoStopMic(originalAutoStopMicRef.current);
    setAutoStartMicOn(originalAutoStartMicOnRef.current);
    setLocalAutoStartMicOnConvEnd(originalAutoStartMicOnConvEndRef.current);
    setAutoStartMicOnConvEnd(originalAutoStartMicOnConvEndRef.current);
    setLocalAutoGenerateAudioOnMic(originalAutoGenerateAudioOnMicRef.current);
    setAutoGenerateAudioOnMic(originalAutoGenerateAudioOnMicRef.current);
    forceUpdate();
  };

  return {
    localSettings: localSettingsRef.current,
    autoStopMic: localVoiceInterruption,
    autoStartMicOn: localAutoStartMic,
    autoStartMicOnConvEnd: localAutoStartMicOnConvEnd,
    autoGenerateAudioOnMic: localAutoGenerateAudioOnMic,
    setAutoStopMic: handleVoiceInterruptionChange,
    setAutoStartMicOn: handleAutoStartMicChange,
    setAutoStartMicOnConvEnd: handleAutoStartMicOnConvEndChange,
    setAutoGenerateAudioOnMic: handleAutoGenerateAudioOnMicChange,
    handleInputChange,
    handleSave,
    handleCancel,
  };
};
