import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toaster } from '@/components/ui/toaster';
import { useWebSocket } from '@/context/websocket-context';
import { formatBrowserTime } from '@/utils/browser-time';
import { MANUAL_SUMMARY_TOAST_ID } from '@/constants/manual-summary';

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]')
    || target.closest('[contenteditable]:not([contenteditable="false"]), [role="textbox"]') !== null;
}

export function useManualSummary() {
  const { t } = useTranslation();
  const { sendMessage, wsState } = useWebSocket();
  const handledKeyRef = useRef(false);

  useEffect(() => {
    if (wsState === 'OPEN' || !toaster.isVisible(MANUAL_SUMMARY_TOAST_ID)) return;
    toaster.update(MANUAL_SUMMARY_TOAST_ID, {
      title: t('error.websocketNotOpen'),
      type: 'error',
      duration: 3000,
    });
  }, [t, wsState]);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (
        event.code !== 'KeyS'
        || event.isComposing
        || event.ctrlKey
        || event.altKey
        || event.metaKey
        || event.shiftKey
        || isTypingTarget(event.target)
        || isTypingTarget(document.activeElement)
      ) return;

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      if (event.repeat) return;
      handledKeyRef.current = true;

      if (wsState !== 'OPEN') {
        toaster.create({
          title: t('error.websocketNotOpen'),
          type: 'error',
          duration: 2000,
        });
        return;
      }

      const loadingToast = {
        title: t('notification.manualSummaryRunning'),
        type: 'loading' as const,
      };
      if (toaster.isVisible(MANUAL_SUMMARY_TOAST_ID)) {
        toaster.update(MANUAL_SUMMARY_TOAST_ID, loadingToast);
      } else {
        toaster.create({ id: MANUAL_SUMMARY_TOAST_ID, ...loadingToast });
      }
      sendMessage({
        type: 'summarize-pending-memory',
        browser_time: formatBrowserTime(),
      });
    };

    const handleKeyUp = (event: globalThis.KeyboardEvent) => {
      if (event.code !== 'KeyS' || !handledKeyRef.current) return;
      handledKeyRef.current = false;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    };

    window.addEventListener('keydown', handleKeyDown, true);
    window.addEventListener('keyup', handleKeyUp, true);
    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
      window.removeEventListener('keyup', handleKeyUp, true);
      handledKeyRef.current = false;
    };
  }, [sendMessage, t, wsState]);
}
