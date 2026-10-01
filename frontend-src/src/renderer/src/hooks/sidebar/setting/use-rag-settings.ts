import { useCallback, useEffect, useRef, useState } from 'react';
import { wsService } from '@/services/websocket-service';
import {
  getStoredRagSettings,
  RagSettings,
  RAG_SETTINGS_KEY,
  sanitizeRagSettings,
} from '@/constants/rag-settings';

interface UseRagSettingsProps {
  onSave?: (callback: () => Promise<void>) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

let nextRagRequestId = 0;

function sendSettings(settings: RagSettings): Promise<void> {
  return new Promise((resolve, reject) => {
    const requestId = `rag-${Date.now()}-${++nextRagRequestId}`;
    const subscription = wsService.onMessage((message) => {
      if (message.request_id !== requestId) return;
      if (message.type === 'rag-options-updated') {
        clearTimeout(timeoutId);
        subscription.unsubscribe();
        resolve();
      } else if (message.type === 'error') {
        clearTimeout(timeoutId);
        subscription.unsubscribe();
        reject(new Error(message.message || 'RAG settings could not be saved'));
      }
    });
    const timeoutId = setTimeout(() => {
      subscription.unsubscribe();
      reject(new Error('RAG settings save timed out'));
    }, 10000);
    if (!wsService.sendMessage({
      type: 'set-rag-options',
      request_id: requestId,
      top_k: settings.topK,
      threshold: settings.threshold,
      hybrid_weight: settings.hybridWeight,
    })) {
      clearTimeout(timeoutId);
      subscription.unsubscribe();
      reject(new Error('WebSocket is disconnected'));
    }
  });
}

export function useRagSettings({ onSave, onCancel }: UseRagSettingsProps = {}) {
  const [settings, setSettings] = useState<RagSettings>(getStoredRagSettings);
  const [originalSettings, setOriginalSettings] = useState<RagSettings>(settings);
  const editingRef = useRef(false);

  const update = useCallback(<K extends keyof RagSettings>(key: K, value: RagSettings[K]) => {
    editingRef.current = true;
    setSettings((previous) => sanitizeRagSettings({ ...previous, [key]: value }));
  }, []);

  const handleSave = useCallback(async () => {
    const normalized = sanitizeRagSettings(settings);
    await sendSettings(normalized);
    window.localStorage.setItem(RAG_SETTINGS_KEY, JSON.stringify(normalized));
    editingRef.current = false;
    setSettings(normalized);
    setOriginalSettings(normalized);
  }, [settings]);

  const handleCancel = useCallback(() => {
    editingRef.current = false;
    setSettings(originalSettings);
  }, [originalSettings]);

  useEffect(() => {
    const subscription = wsService.onMessage((message) => {
      if (message.type !== 'rag-options-updated' ||
          typeof message.top_k !== 'number' ||
          typeof message.threshold !== 'number' ||
          typeof message.hybrid_weight !== 'number') return;
      if (message.persisted === false && window.localStorage.getItem(RAG_SETTINGS_KEY)) return;
      const updated = sanitizeRagSettings({
        topK: message.top_k,
        threshold: message.threshold,
        hybridWeight: message.hybrid_weight,
      });
      setOriginalSettings(updated);
      if (!editingRef.current) setSettings(updated);
    });
    return () => subscription.unsubscribe();
  }, []);

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
