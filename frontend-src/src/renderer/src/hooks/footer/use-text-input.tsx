import { useCallback, useState } from 'react';
import { useWebSocket } from '@/context/websocket-context';
import { useAiState } from '@/context/ai-state-context';
import { useInterrupt } from '@/components/canvas/live2d';
import { useChatHistory } from '@/context/chat-history-context';
import { useVAD } from '@/context/vad-context';
import { useMediaCapture } from '@/hooks/utils/use-media-capture';
import { formatBrowserTime } from '@/utils/browser-time';
import { optionalFeature } from '@optional-feature';
import { getMissingChatApiKeyProvider } from '@/constants/api-keys';
import { toaster } from '@/components/ui/toaster';
import { useTranslation } from 'react-i18next';

export type QuickStartTopic = 'english' | 'work' | 'relationships' | 'school' | 'psychology' | 'story';

interface SendTextMessageOptions {
  quickStartTopic?: QuickStartTopic;
}

export function useSendTextMessage() {
  const { t } = useTranslation();
  const wsContext = useWebSocket();
  const { aiState } = useAiState();
  const { interrupt } = useInterrupt();
  const { appendHumanMessage } = useChatHistory();
  const { stopMic, autoStopMic } = useVAD();
  const { captureAllMedia } = useMediaCapture();

  const sendTextMessage = useCallback(async (
    displayText: string,
    options: SendTextMessageOptions = {},
  ): Promise<boolean> => {
    const text = displayText.trim();
    if (!text || !wsContext) return false;
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
      return false;
    }
    if (aiState === 'thinking-speaking') {
      interrupt();
    }

    const optionalContexts = optionalFeature.consumeForUserMessage();
    const images = await captureAllMedia();

    appendHumanMessage(text);
    wsContext.sendMessage({
      type: 'text-input',
      text,
      images,
      browser_time: formatBrowserTime(),
      ...(options.quickStartTopic ? {
        quick_start_topic: options.quickStartTopic,
      } : {}),
      ...(optionalContexts ? {
        optional_contexts: optionalContexts,
      } : {}),
    });

    if (autoStopMic) stopMic();
    return true;
  }, [
    aiState,
    appendHumanMessage,
    autoStopMic,
    captureAllMedia,
    interrupt,
    stopMic,
    t,
    wsContext,
  ]);

  return { sendTextMessage };
}

export function useTextInput() {
  const [inputText, setInputText] = useState('');
  const [isComposing, setIsComposing] = useState(false);
  const { sendTextMessage } = useSendTextMessage();

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputText(e.target.value);
  };

  const handleSend = async () => {
    if (await sendTextMessage(inputText)) setInputText('');
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
