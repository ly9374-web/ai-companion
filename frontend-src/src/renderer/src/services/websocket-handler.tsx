/* eslint-disable no-sparse-arrays */
/* eslint-disable react-hooks/exhaustive-deps */
// eslint-disable-next-line object-curly-newline
import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { wsService, MessageEvent } from '@/services/websocket-service';
import {
  WebSocketContext, HistoryInfo, defaultWsUrl, defaultBaseUrl,
} from '@/context/websocket-context';
import { ModelInfo, useLive2DConfig } from '@/context/live2d-config-context';
import { useSubtitle } from '@/context/subtitle-context';
import { audioTaskQueue } from '@/utils/task-queue';
import { useAudioTask } from '@/components/canvas/live2d';
import { useBgUrl } from '@/context/bgurl-context';
import { useConfig } from '@/context/character-config-context';
import { useChatHistory } from '@/context/chat-history-context';
import { toaster } from '@/components/ui/toaster';
import { useVAD } from '@/context/vad-context';
import { AiState, useAiState } from "@/context/ai-state-context";
import {
  getStoredQwenTtsOptions,
  getStoredTtsInstructionPreset,
  setCurrentQwenTtsSettings,
  isValidQwenTtsVoice,
} from '@/constants/qwen-tts-voices';
import { getStoredMaxHistoryTurns } from '@/constants/max-history-turns';
import { optionalFeature } from '@optional-feature';
import { optionalExpressionFeature } from '@/services/optional-expression-feature';
import {
  MANUAL_SUMMARY_TOAST_ID,
  ROLLING_SUMMARY_TOAST_ID,
} from '@/constants/manual-summary';
import { PERSONA_PROFILE_TOAST_ID } from '@/constants/persona-profile';
import {
  getCurrentBaseUrl,
  getCurrentWsUrl,
  setCurrentBaseUrl,
  setCurrentWsUrl,
} from '@/constants/connection-settings';
import { getStoredCharacterPreset } from '@/constants/character-settings';

function WebSocketHandler({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const [wsState, setWsState] = useState<string>('CLOSED');
  const [wsUrl, setWsUrlState] = useState<string>(getCurrentWsUrl);
  const [baseUrl, setBaseUrlState] = useState<string>(getCurrentBaseUrl);
  const { aiState, setAiState, backendSynthComplete, setBackendSynthComplete } = useAiState();
  const { setModelInfo } = useLive2DConfig();
  const { setSubtitleText } = useSubtitle();
  const {
    clearResponse,
    setForceNewMessage,
    appendHumanMessage,
    appendOrUpdateToolCallMessage,
    setUndoPending,
  } = useChatHistory();
  const { addAudioTask } = useAudioTask();
  const bgUrlContext = useBgUrl();
  const {
    confName,
    confUid,
    setConfName,
    setConfUid,
    setConfigFiles,
  } = useConfig();
  const [pendingModelInfo, setPendingModelInfo] = useState<ModelInfo | undefined>(undefined);
  const { startMic, stopMic, autoStartMicOnConvEnd } = useVAD();
  const autoStartMicOnConvEndRef = useRef(autoStartMicOnConvEnd);
  const profilerFinalPendingRef = useRef(false);
  const profilerFinalRoundRef = useRef<number | null>(null);

  const setWsUrl = useCallback((url: string) => {
    setCurrentWsUrl(url);
    setWsUrlState(url);
  }, []);
  const setBaseUrl = useCallback((url: string) => {
    setCurrentBaseUrl(url);
    setBaseUrlState(url);
  }, []);

  useEffect(() => {
    autoStartMicOnConvEndRef.current = autoStartMicOnConvEnd;
  }, [autoStartMicOnConvEnd]);

  useEffect(() => {
    if (pendingModelInfo && confUid) {
      setModelInfo(pendingModelInfo);
      setPendingModelInfo(undefined);
    }
  }, [pendingModelInfo, setModelInfo, confUid]);

  const {
    setCurrentHistoryUid, setMessages, setHistoryList,
  } = useChatHistory();

  const handleControlMessage = useCallback((controlText: string) => {
    switch (controlText) {
      case 'start-mic':
        console.log('Starting microphone...');
        startMic();
        break;
      case 'stop-mic':
        console.log('Stopping microphone...');
        stopMic();
        break;
      case 'conversation-chain-start':
        setAiState('thinking-speaking');
        audioTaskQueue.clearQueue();
        clearResponse();
        optionalFeature.onConversationStart();
        break;
      case 'conversation-chain-end':
        audioTaskQueue.addTask(() => new Promise<void>((resolve) => {
          optionalFeature.onConversationEnd();
          if (profilerFinalPendingRef.current) {
            profilerFinalPendingRef.current = false;
            const finalizeRound = profilerFinalRoundRef.current;
            profilerFinalRoundRef.current = null;
            setAiState('loading');
            wsService.sendMessage({
              type: 'profiler-finalize',
              ...(finalizeRound !== null ? { round: finalizeRound } : {}),
              optional_contexts: optionalFeature.consumeAssistantResponse(),
            });
            resolve();
            return;
          }
          setAiState((currentState: AiState) => {
            if (currentState === 'thinking-speaking') {
              // Auto start mic if enabled
              if (autoStartMicOnConvEndRef.current) {
                startMic();
              }
              return 'idle';
            }
            return currentState;
          });
          resolve();
        }));
        break;
      default:
        console.warn('Unknown control command:', controlText);
    }
  }, [setAiState, clearResponse, setForceNewMessage, startMic, stopMic]);

  const handleWebSocketMessage = useCallback((message: MessageEvent) => {
    console.log('Received message from server:', message);
    switch (message.type) {
      case 'control':
        if (message.text) {
          handleControlMessage(message.text);
        }
        break;
      case 'set-model-and-conf':
        setAiState('loading');
        if (message.conf_name) {
          setConfName(message.conf_name);
        }
        if (isValidQwenTtsVoice(message.tts_voice)) {
          const storedOptions = getStoredQwenTtsOptions();
          if (storedOptions.voice !== message.tts_voice) {
            setCurrentQwenTtsSettings(
              message.tts_voice,
              getStoredTtsInstructionPreset(),
            );
            wsService.sendMessage({
              type: 'set-qwen-tts-options',
              voice: message.tts_voice,
              instruction: storedOptions.instruction,
              sync_ai_preferences: true,
            });
          }
        }
        if (message.conf_uid) {
          setConfUid(message.conf_uid);
          console.log('confUid', message.conf_uid);
        }
        if (message.expression_dir) {
          optionalExpressionFeature.reload(message.expression_dir);
        }
        setPendingModelInfo(message.model_info);
        // setModelInfo(message.model_info);
        // We don't know when the confRef in live2d-config-context will be updated, so we set a delay here for convenience
        if (message.model_info && !message.model_info.url.startsWith("http")) {
          const modelUrl = baseUrl + message.model_info.url;
          // eslint-disable-next-line no-param-reassign
          message.model_info.url = modelUrl;
        }

        setAiState('idle');
        break;
      case 'full-text':
        if (message.text) {
          setSubtitleText(message.text);
        }
        break;
      case 'expression-update':
        if (message.emotion) {
          void optionalExpressionFeature.setEmotion(message.emotion);
        }
        break;
      case 'config-files':
        if (message.configs) {
          setConfigFiles(message.configs);
          const storedCharacter = getStoredCharacterPreset();
          const storedConfig = message.configs.find(
            (config) => config.filename === storedCharacter,
          );
          if (storedConfig && storedConfig.name !== confName) {
            wsService.sendMessage({
              type: 'switch-config',
              file: storedCharacter,
            });
          }
        }
        break;
      case 'config-switched':
        setAiState('idle');
        setSubtitleText(t('notification.characterLoaded'));

        toaster.create({
          title: t('notification.characterSwitched'),
          type: 'success',
          duration: 2000,
        });

        // setModelInfo(undefined);

        wsService.sendMessage({ type: 'fetch-history-list' });
        wsService.sendMessage({ type: 'create-new-history' });
        wsService.sendMessage({
          type: 'set-qwen-tts-options',
          ...getStoredQwenTtsOptions(),
          sync_ai_preferences: true,
        });
        wsService.sendMessage({
          type: 'set-max-history-turns',
          max_history_turns: getStoredMaxHistoryTurns(),
        });
        break;
      case 'tts-voice-updated':
      case 'qwen-tts-options-updated':
      case 'debug-mode-updated':
      case 'max-history-turns-updated':
      case 'rag-options-updated':
        break;
      case 'background-files':
        if (message.files) {
          bgUrlContext?.setBackgroundFiles(message.files);
        }
        break;
      case 'audio':
        if (aiState === 'interrupted' || aiState === 'listening') {
          console.log('Audio playback intercepted. Sentence:', message.display_text?.text);
        } else {
          console.log("actions", message.actions);
          addAudioTask({
            audioBase64: message.audio || '',
            volumes: message.volumes || [],
            sliceLength: message.slice_length || 0,
            displayText: message.display_text || null,
            expressions: message.actions?.expressions || null,
            emotion: message.emotion || null,
            speechSegments: message.speech_segments || [],
          });
        }
        break;
      case 'history-data':
        if (message.messages) {
          setMessages(message.messages);
        }
        toaster.create({
          title: t('notification.historyLoaded'),
          type: 'success',
          duration: 2000,
        });
        break;
      case 'new-history-created':
        profilerFinalPendingRef.current = false;
        profilerFinalRoundRef.current = null;
        setAiState('idle');
        setSubtitleText(t('notification.newConversation'));
        // No need to open mic here
        if (message.history_uid) {
          setCurrentHistoryUid(message.history_uid);
          setMessages(message.messages || []);
          const latestMessage = message.messages?.length
            ? message.messages[message.messages.length - 1]
            : null;
          const newHistory: HistoryInfo = {
            uid: message.history_uid,
            latest_message: latestMessage ? {
              content: latestMessage.content,
              role: latestMessage.role,
              timestamp: latestMessage.timestamp,
            } : null,
            timestamp: latestMessage?.timestamp || new Date().toISOString(),
          };
          setHistoryList((prev: HistoryInfo[]) => [newHistory, ...prev]);
          toaster.create({
            title: t('notification.newChatHistory'),
            type: 'success',
            duration: 2000,
          });
        }
        break;
      case 'profiler-analysis-ready':
        profilerFinalPendingRef.current = true;
        profilerFinalRoundRef.current = typeof message.round === 'number' ? message.round : null;
        break;
      case 'profiler-analysis-status': {
        const status = message.status;
        const interimRound = typeof message.round === 'number' ? message.round : null;
        if (status === 'accepted' || status === 'running') {
          setAiState('loading');
          const toastOptions = {
            title: status === 'accepted'
              ? (interimRound !== null
                ? `已进入第${interimRound}轮，正在整理中期分析材料`
                : '危机已经解决，正在整理分析材料')
              : (interimRound !== null
                ? `DeepSeek 正在生成第${interimRound}轮中期心理侧写`
                : 'DeepSeek 正在生成心理侧写'),
            description: typeof message.progress === 'number'
              ? `进度 ${message.progress}%`
              : undefined,
            type: 'loading' as const,
          };
          if (toaster.isVisible('profiler-analysis')) {
            toaster.update('profiler-analysis', toastOptions);
          } else {
            toaster.create({ id: 'profiler-analysis', ...toastOptions });
          }
          break;
        }
        setAiState('idle');
        if (status === 'success') {
          const toastOptions = {
            title: interimRound !== null
              ? `第${interimRound}轮中期心理侧写报告已生成`
              : '心理侧写报告已生成',
            description: message.path,
            type: 'success' as const,
            duration: 6000,
          };
          if (toaster.isVisible('profiler-analysis')) {
            toaster.update('profiler-analysis', toastOptions);
          } else {
            toaster.create({ id: 'profiler-analysis', ...toastOptions });
          }
          window.dispatchEvent(new CustomEvent('profiler-analysis-complete', { detail: {
            content: message.content || '',
            path: message.path || '',
            round: interimRound,
          }}));
          break;
        }
        const toastOptions = {
          title: status === 'duplicate' ? '心理侧写报告正在生成中' : '心理侧写报告生成失败',
          description: message.error,
          type: status === 'duplicate' ? 'info' as const : 'error' as const,
          duration: 6000,
        };
        if (toaster.isVisible('profiler-analysis')) {
          toaster.update('profiler-analysis', toastOptions);
        } else {
          toaster.create({ id: 'profiler-analysis', ...toastOptions });
        }
        break;
      }
      case 'history-deleted':
        toaster.create({
          title: message.success
            ? t('notification.historyDeleteSuccess')
            : t('notification.historyDeleteFail'),
          type: message.success ? 'success' : 'error',
          duration: 2000,
        });
        break;
      case 'undo-last-message-result':
        setUndoPending(false);
        setAiState('idle');
        if (message.success) {
          setMessages(message.messages || []);
          if (message.histories) {
            setHistoryList(message.histories);
          }
          clearResponse();
          setForceNewMessage(true);
          setSubtitleText('');
          toaster.create({
            title: t('notification.undoMessageSuccess'),
            type: 'success',
            duration: 1800,
          });
        } else {
          toaster.create({
            title: t('notification.undoMessageEmpty'),
            type: 'info',
            duration: 1800,
          });
        }
        break;
      case 'history-list':
        if (message.histories) {
          setHistoryList(message.histories);
          if (message.histories.length > 0) {
            setCurrentHistoryUid(message.histories[0].uid);
          }
        }
        break;
      case 'user-input-transcription':
        console.log('user-input-transcription: ', message.text);
        if (message.text) {
          appendHumanMessage(message.text);
        }
        break;
      case 'error':
        setUndoPending(false);
        toaster.create({
          title: message.message,
          type: 'error',
          duration: 2000,
        });
        break;
      case 'api-key-required': {
        const apiKeyRequiredTitle = t(
          message.provider === 'grok'
            ? 'error.grokApiKeyRequired'
            : 'error.deepseekApiKeyRequired',
        );
        if (message.request_type === 'profiler-finalize') {
          setAiState('idle');
          if (toaster.isVisible('profiler-analysis')) {
            toaster.update('profiler-analysis', {
              title: apiKeyRequiredTitle,
              type: 'error',
              duration: 3000,
            });
          } else {
            toaster.create({
              id: 'profiler-analysis',
              title: apiKeyRequiredTitle,
              type: 'error',
              duration: 3000,
            });
          }
          break;
        }
        if (message.request_type === 'generate-persona-profile') {
          if (toaster.isVisible(PERSONA_PROFILE_TOAST_ID)) {
            toaster.update(PERSONA_PROFILE_TOAST_ID, {
              title: apiKeyRequiredTitle,
              type: 'error',
              duration: 3000,
            });
          } else {
            toaster.create({
              id: PERSONA_PROFILE_TOAST_ID,
              title: apiKeyRequiredTitle,
              type: 'error',
              duration: 3000,
            });
          }
          break;
        }
        if (toaster.isVisible(ROLLING_SUMMARY_TOAST_ID)) {
          toaster.update(ROLLING_SUMMARY_TOAST_ID, {
            title: apiKeyRequiredTitle,
            type: 'error',
            duration: 3000,
          });
          break;
        }
        if (toaster.isVisible(MANUAL_SUMMARY_TOAST_ID)) {
          toaster.update(MANUAL_SUMMARY_TOAST_ID, {
            title: apiKeyRequiredTitle,
            type: 'error',
            duration: 3000,
          });
          break;
        }
        if (toaster.isVisible(PERSONA_PROFILE_TOAST_ID)) {
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: apiKeyRequiredTitle,
            type: 'error',
            duration: 3000,
          });
          break;
        }
        toaster.create({
          title: apiKeyRequiredTitle,
          type: 'warning',
          duration: 3000,
        });
        break;
      }
      case 'manual-summary-result': {
        const results = [
          message.long_term_memory,
          message.short_term_relationship,
        ];
        const hasSuccess = results.includes('success');
        const hasFailure = results.some((result) => (
          result === 'error' || result === 'unsupported'
        ));
        const isDisabled = results.every((result) => result === 'disabled');

        let title = t('notification.manualSummaryComplete');
        let type: 'success' | 'error' | 'info' = 'success';
        if (isDisabled) {
          title = t('notification.manualSummaryDisabled');
          type = 'info';
        } else if (hasFailure) {
          title = hasSuccess
            ? t('notification.manualSummaryPartialFailure')
            : t('notification.manualSummaryFailed');
          type = 'error';
        } else if (!hasSuccess) {
          title = t('notification.manualSummaryEmpty');
          type = 'info';
        }

        toaster.update(MANUAL_SUMMARY_TOAST_ID, {
          title,
          type,
          duration: 3000,
        });
        break;
      }
      case 'manual-summary-duplicate':
        toaster.update(MANUAL_SUMMARY_TOAST_ID, {
          title: t('notification.manualSummaryDuplicate'),
          type: 'info',
          duration: 3000,
        });
        break;
      case 'debug-rolling-summary-result': {
        const status = message.status;
        let title = t('notification.rollingSummaryFailed');
        let type: 'success' | 'error' | 'info' = 'error';
        if (status === 'success') {
          title = t('notification.rollingSummaryComplete');
          type = 'success';
        } else if (status === 'empty') {
          title = t('notification.rollingSummaryEmpty');
          type = 'info';
        } else if (status === 'duplicate') {
          title = t('notification.rollingSummaryDuplicate');
          type = 'info';
        } else if (status === 'disabled') {
          title = t('notification.rollingSummaryDebugOnly');
          type = 'info';
        }
        toaster.update(ROLLING_SUMMARY_TOAST_ID, {
          title,
          type,
          duration: 3000,
        });
        break;
      }
      case 'persona-profile-status': {
        const status = message.status;
        if (status === 'accepted') {
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: t('notification.personaProfileAccepted'),
            type: 'loading',
          });
          break;
        }
        if (status === 'running') {
          const stepKey = [
            'snapshot',
            'relationship',
            'memory',
            'merge',
            'consolidating',
            'persona',
            'thinslice',
            'complete',
          ].includes(message.step)
            ? message.step
            : 'running';
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: t(`notification.personaProfileStep.${stepKey}`, {
              current: message.current,
              total: message.total,
            }),
            description: typeof message.progress === 'number'
              ? t('notification.personaProfileProgress', { progress: message.progress })
              : undefined,
            type: 'loading',
          });
          break;
        }
        if (status === 'success') {
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: t('notification.personaProfileComplete', {
              number: message.profile_number,
            }),
            description: message.path,
            type: 'success',
            duration: 6000,
          });
          break;
        }
        if (status === 'empty') {
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: t('notification.personaProfileEmpty'),
            type: 'info',
            duration: 3000,
          });
          break;
        }
        if (status === 'duplicate') {
          toaster.update(PERSONA_PROFILE_TOAST_ID, {
            title: t('notification.personaProfileDuplicate'),
            type: 'info',
            duration: 3000,
          });
          break;
        }
        toaster.update(PERSONA_PROFILE_TOAST_ID, {
          title: t('notification.personaProfileFailed'),
          description: message.error,
          type: 'error',
          duration: 6000,
        });
        break;
      }
      case 'system-prompt':
        // Backend returned the editable system prompt section (or an error).
        window.dispatchEvent(new CustomEvent('system-prompt', { detail: {
          content: message.content,
          error: message.error,
        }}));
        break;
      case 'system-prompt-updated':
        window.dispatchEvent(new CustomEvent('system-prompt-updated', { detail: {
          success: message.success,
          error: message.error,
        }}));
        break;
      case 'system-prompt-reset':
        window.dispatchEvent(new CustomEvent('system-prompt-reset', { detail: {
          success: message.success,
          content: message.content,
          error: message.error,
        }}));
        break;
      case 'backend-synth-complete':
        setBackendSynthComplete(message.request_id || null);
        break;
      case 'conversation-chain-end':
        if (!audioTaskQueue.hasTask()) {
          setAiState((currentState: AiState) => {
            if (currentState === 'thinking-speaking') {
              return 'idle';
            }
            return currentState;
          });
        }
        break;
      case 'force-new-message':
        setForceNewMessage(true);
        break;
      case 'tool_call_status':
        optionalFeature.handleWebSocketMessage(message);
        if (message.tool_id && message.tool_name && message.status) {
          appendOrUpdateToolCallMessage({
            id: message.tool_id,
            type: 'tool_call_status',
            role: 'ai',
            tool_id: message.tool_id,
            tool_name: message.tool_name,
            name: message.name,
            status: message.status as ('running' | 'completed' | 'error'),
            content: message.content || '',
            timestamp: message.timestamp || new Date().toISOString(),
            media_urls: message.media_urls,
          });
        } else {
          console.warn('Received incomplete tool_call_status message:', message);
        }
        break;
      default:
        console.warn('Unknown message type:', message.type);
    }
  }, [aiState, addAudioTask, appendHumanMessage, baseUrl, bgUrlContext, confName, setAiState, setConfName, setConfUid, setConfigFiles, setCurrentHistoryUid, setHistoryList, setMessages, setModelInfo, setSubtitleText, startMic, stopMic, backendSynthComplete, setBackendSynthComplete, clearResponse, handleControlMessage, appendOrUpdateToolCallMessage, t]);

  useEffect(() => {
    wsService.connect(wsUrl);
  }, [wsUrl]);

  useEffect(() => {
    const stateSubscription = wsService.onStateChange(setWsState);
    const messageSubscription = wsService.onMessage(handleWebSocketMessage);
    return () => {
      stateSubscription.unsubscribe();
      messageSubscription.unsubscribe();
    };
  }, [wsUrl, handleWebSocketMessage]);

  const webSocketContextValue = useMemo(() => ({
    sendMessage: wsService.sendMessage.bind(wsService),
    wsState,
    reconnect: () => wsService.connect(wsUrl),
    wsUrl,
    setWsUrl,
    baseUrl,
    setBaseUrl,
  }), [wsState, wsUrl, baseUrl]);

  return (
    <WebSocketContext.Provider value={webSocketContextValue}>
      {children}
    </WebSocketContext.Provider>
  );
}

export default WebSocketHandler;
