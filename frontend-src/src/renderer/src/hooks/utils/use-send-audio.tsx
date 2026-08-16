import { useCallback } from "react";
import { useWebSocket } from "@/context/websocket-context";
import { useMediaCapture } from "@/hooks/utils/use-media-capture";
import { formatBrowserTime } from "@/utils/browser-time";
import { getMissingChatApiKeyProvider } from "@/constants/api-keys";
import { toaster } from "@/components/ui/toaster";
import { useTranslation } from "react-i18next";
import { optionalFeature } from "@optional-feature";

export function useSendAudio() {
  const { t } = useTranslation();
  const { sendMessage } = useWebSocket();
  const { captureAllMedia } = useMediaCapture();

  const sendAudioPartition = useCallback(
    async (audio: Float32Array) => {
      const missingApiKeyProvider = getMissingChatApiKeyProvider();
      if (missingApiKeyProvider) {
        toaster.create({
          title: t(
            missingApiKeyProvider === 'grok'
              ? 'error.grokApiKeyRequired'
              : 'error.deepseekApiKeyRequired',
          ),
          type: 'warning',
          duration: 3000,
        });
        return;
      }

      const optionalContexts = optionalFeature.consumeForUserMessage();

      const chunkSize = 4096;

      // Send the audio data in chunks
      for (let index = 0; index < audio.length; index += chunkSize) {
        const endIndex = Math.min(index + chunkSize, audio.length);
        const chunk = audio.slice(index, endIndex);
        sendMessage({
          type: "mic-audio-data",
          audio: Array.from(chunk),
          // Only send images with first chunk
        });
      }

      const images = await captureAllMedia();
      sendMessage({
        type: "mic-audio-end",
        images,
        browser_time: formatBrowserTime(),
        ...(optionalContexts ? {
          optional_contexts: optionalContexts,
        } : {}),
      });
    },
    [sendMessage, captureAllMedia, t],
  );

  return {
    sendAudioPartition,
  };
}
