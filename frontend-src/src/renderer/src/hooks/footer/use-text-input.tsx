import { useState } from 'react';
import { useWebSocket } from '@/context/websocket-context';
import { useAiState } from '@/context/ai-state-context';
import { useInterrupt } from '@/components/canvas/live2d';
import { useChatHistory } from '@/context/chat-history-context';
import { useVAD } from '@/context/vad-context';
import { useMediaCapture } from '@/hooks/utils/use-media-capture';
import { formatBrowserTime } from '@/utils/browser-time';
import { optionalFeature } from '@optional-feature';
import { hasStoredDeepSeekApiKey } from '@/constants/api-keys';
import { toaster } from '@/components/ui/toaster';
import { useTranslation } from 'react-i18next';

export function useTextInput() {
  const { t } = useTranslation();
  const [inputText, setInputText] = useState('');
  const [isComposing, setIsComposing] = useState(false);
  const wsContext = useWebSocket();
  const { aiState } = useAiState();
  const { interrupt } = useInterrupt();
  const { appendHumanMessage } = useChatHistory();
  const { stopMic, autoStopMic } = useVAD();
  const { captureAllMedia } = useMediaCapture();

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputText(e.target.value);
  };

  const handleSend = async () => {
    if (!inputText.trim() || !wsContext) return;
    if (!hasStoredDeepSeekApiKey()) {
      toaster.create({
        title: t('error.deepseekApiKeyRequired'),
        type: 'warning',
        duration: 3000,
      });
      return;
    }
    if (aiState === 'thinking-speaking') {
      interrupt();
    }

    const optionalContexts = optionalFeature.consumeForUserMessage();

    const images = await captureAllMedia();

    appendHumanMessage(inputText.trim());
    wsContext.sendMessage({
      type: 'text-input',
      text: inputText.trim(),
      images,
      browser_time: formatBrowserTime(),
      ...(optionalContexts ? {
        optional_contexts: optionalContexts,
      } : {}),
    });

    if (autoStopMic) stopMic();
    setInputText('');
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (isComposing) return;

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleCompositionStart = () => setIsComposing(true);
  const handleCompositionEnd = () => setIsComposing(false);

  return {
    inputText,
    setInputText: handleInputChange,
    handleSend,
    handleKeyPress,
    handleCompositionStart,
    handleCompositionEnd,
  };
}
