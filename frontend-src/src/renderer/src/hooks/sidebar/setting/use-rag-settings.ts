import { useCallback, useEffect, useState } from 'react';
import { wsService } from '@/services/websocket-service';
import {
  getStoredRagSettings,
  RagSettings,
  RAG_SETTINGS_KEY,
  sanitizeRagSettings,
} from '@/constants/rag-settings';

interface UseRagSettingsProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

function sendSettings(settings: RagSettings): void {
  wsService.sendMessage({
    type: 'set-rag-options',
    top_k: settings.topK,
    threshold: settings.threshold,
    hybrid_weight: settings.hybridWeight,
  });
}

export function useRagSettings({ onSave, onCancel }: UseRagSettingsProps = {}) {
  const [settings, setSettings] = useState<RagSettings>(getStoredRagSettings);
  const [originalSettings, setOriginalSettings] = useState<RagSettings>(settings);

  const update = useCallback(<K extends keyof RagSettings>(key: K, value: RagSettings[K]) => {
    setSettings((previous) => sanitizeRagSettings({ ...previous, [key]: value }));
  }, []);

  const handleSave = useCallback(() => {
    const normalized = sanitizeRagSettings(settings);
    window.localStorage.setItem(RAG_SETTINGS_KEY, JSON.stringify(normalized));
    setSettings(normalized);
    setOriginalSettings(normalized);
    sendSettings(normalized);
  }, [settings]);

  const handleCancel = useCallback(() => {
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
  }, [onSave, onCancel, handleSave, handleCancel]);

  return { settings, update };
}
