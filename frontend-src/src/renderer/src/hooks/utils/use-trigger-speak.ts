import { useCallback } from 'react';
import { useWebSocket } from '@/context/websocket-context';
import { optionalFeature, useOptionalMediaCapture } from '@optional-feature';

export function useTriggerSpeak() {
  const { sendMessage, wsState } = useWebSocket();
  const { captureAllMedia } = useOptionalMediaCapture();

  const sendTriggerSignal = useCallback(
    async (actualIdleTime: number) => {
      const images = await captureAllMedia();
      if (wsState !== 'OPEN') return;
      optionalFeature.beginProactiveSpeak();
      sendMessage({
        type: "ai-speak-signal",
        idle_time: actualIdleTime,
        images,
      });
    },
    [sendMessage, captureAllMedia, wsState],
  );

  return {
    sendTriggerSignal,
  };
}
