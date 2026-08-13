import { useCallback } from 'react';
import { useWebSocket } from '@/context/websocket-context';
import { useMediaCapture } from './use-media-capture';
import { optionalFeature } from '@optional-feature';

export function useTriggerSpeak() {
  const { sendMessage, wsState } = useWebSocket();
  const { captureAllMedia } = useMediaCapture();

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
