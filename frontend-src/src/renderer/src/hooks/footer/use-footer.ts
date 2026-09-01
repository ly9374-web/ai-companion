import { ChangeEvent, KeyboardEvent } from 'react';
import { useTextInput } from '@/hooks/footer/use-text-input';
import { useInterrupt } from '@/hooks/utils/use-interrupt';
import { useMicToggle } from '@/hooks/utils/use-mic-toggle';
import { useAiState, AiStateEnum } from '@/context/ai-state-context';
import { useChatHistory } from '@/context/chat-history-context';
import { useWebSocket } from '@/context/websocket-context';
import { formatBrowserTime } from '@/utils/browser-time';

export const useFooter = () => {
  const {
    inputText: inputValue,
    setInputText: handleChange,
    handleKeyPress: handleKey,
    handleCompositionStart,
    handleCompositionEnd,
  } = useTextInput();

  const { interrupt } = useInterrupt();
  const { handleMicToggle, micOn } = useMicToggle();
  const { setAiState, aiState } = useAiState();
  const { sendMessage } = useWebSocket();
  const {
    currentHistoryUid,
    messages,
    undoPending,
    setUndoPending,
  } = useChatHistory();

  const handleInputChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    handleChange({ target: { value: e.target.value } } as ChangeEvent<HTMLInputElement>);
    setAiState(AiStateEnum.WAITING);
  };

  const handleKeyPress = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    handleKey(e as any);
  };

  const canUndo = Boolean(
    currentHistoryUid
    && !undoPending
    && messages.some((message) => message.role === 'human' || message.role === 'ai'),
  );

  const handleUndo = () => {
    if (!canUndo) return;
    if (aiState === AiStateEnum.THINKING_SPEAKING) {
      interrupt(false);
    }
    setAiState(AiStateEnum.INTERRUPTED);
    setUndoPending(true);
    sendMessage({
      type: 'undo-last-message',
      history_uid: currentHistoryUid,
      browser_time: formatBrowserTime(),
    });
  };

  return {
    inputValue,
    handleInputChange,
    handleKeyPress,
    handleCompositionStart,
    handleCompositionEnd,
    handleUndo,
    canUndo,
    undoPending,
    handleMicToggle,
    micOn,
  };
};
