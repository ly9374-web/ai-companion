/* eslint-disable global-require */
/* eslint-disable @typescript-eslint/no-var-requires */
/* eslint-disable no-use-before-define */
import { Subject } from 'rxjs';
import { ModelInfo } from '@/context/live2d-config-context';
import { HistoryInfo } from '@/context/websocket-context';
import { ConfigFile } from '@/context/character-config-context';
import { toaster } from '@/components/ui/toaster';
import { getStoredQwenTtsOptions } from '@/constants/qwen-tts-voices';
import { getStoredMaxHistoryTurns } from '@/constants/max-history-turns';
import { getStoredRagSettings } from '@/constants/rag-settings';
import {
  getStoredApiKeys,
  isGrokEnabledForPageSession,
} from '@/constants/api-keys';
import { getGeneralRuntimeSettings } from '@/constants/general-runtime-settings';

export interface DisplayText {
  text: string;
  name: string;
  avatar: string;
}

interface BackgroundFile {
  name: string;
  url: string;
}

export interface AudioPayload {
  type: 'audio';
  audio?: string;
  volumes?: number[];
  slice_length?: number;
  display_text?: DisplayText;
  actions?: Actions;
  emotion?: string;
}

export interface Message {
  id: string;
  content: string;
  role: "ai" | "human";
  timestamp: string;
  name?: string;
  avatar?: string;

  // Fields for different message types (make optional)
  type?: 'text' | 'tool_call_status'; // Add possible types, default to 'text' if omitted
  tool_id?: string; // Specific to tool calls
  tool_name?: string; // Specific to tool calls
  status?: 'running' | 'completed' | 'error'; // Specific to tool calls
}

export interface Actions {
  expressions?: string[] | number [];
  pictures?: string[];
  sounds?: string[];
}

export interface MessageEvent {
  tool_id: any;
  tool_name: any;
  name: any;
  status: any;
  content: string;
  timestamp: string;
  type: string;
  audio?: string;
  volumes?: number[];
  slice_length?: number;
  files?: BackgroundFile[];
  actions?: Actions;
  text?: string;
  model_info?: ModelInfo;
  conf_name?: string;
  conf_uid?: string;
  uids?: string[];
  messages?: Message[];
  history_uid?: string;
  success?: boolean;
  histories?: HistoryInfo[];
  configs?: ConfigFile[];
  message?: string;
  display_text?: DisplayText;
  live2d_model?: string;
  voice?: string;
  instruction?: string;
  emotion?: string;
  max_history_turns?: number;
  long_term_memory?: string;
  short_term_relationship?: string;
  provider?: 'deepseek' | 'grok';
  browser_view?: {
    debuggerFullscreenUrl: string;
    debuggerUrl: string;
    pages: {
      id: string;
      url: string;
      faviconUrl: string;
      title: string;
      debuggerUrl: string;
      debuggerFullscreenUrl: string;
    }[];
    wsUrl: string;
    sessionId?: string;
  };
}

// Get translation function for error messages
const getTranslation = () => {
  try {
    const i18next = require('i18next').default;
    return i18next.t.bind(i18next);
  } catch (e) {
    // Fallback if i18next is not available
    return (key: string) => key;
  }
};

class WebSocketService {
  private static instance: WebSocketService;

  private ws: WebSocket | null = null;

  private messageSubject = new Subject<MessageEvent>();

  private stateSubject = new Subject<'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED'>();

  private currentState: 'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED' = 'CLOSED';

  private accountName: string | null = null;

  private sessionToken: string | null = null;

  private connectionGeneration = 0;

  static getInstance() {
    if (!WebSocketService.instance) {
      WebSocketService.instance = new WebSocketService();
    }
    return WebSocketService.instance;
  }

  private initializeConnection() {
    const ragSettings = getStoredRagSettings();
    const apiKeys = getStoredApiKeys();
    const generalSettings = getGeneralRuntimeSettings();
    this.sendMessage({
      type: 'set-api-keys',
      deepseek_api_key: apiKeys.deepseekApiKey,
      grok_api_key: apiKeys.grokApiKey,
      grok_enabled: isGrokEnabledForPageSession(),
      qwen_api_key: apiKeys.qwenApiKey,
    });
    this.sendMessage({
      type: 'set-generate-audio',
      enabled: generalSettings.generateAudio,
    });
    this.sendMessage({
      type: 'set-debug-mode',
      enabled: generalSettings.debugMode,
    });
    this.sendMessage({
      type: 'set-qwen-tts-options',
      ...getStoredQwenTtsOptions(),
      sync_ai_preferences: true,
    });
    this.sendMessage({
      type: 'set-max-history-turns',
      max_history_turns: getStoredMaxHistoryTurns(),
    });
    this.sendMessage({
      type: 'set-rag-options',
      top_k: ragSettings.topK,
      threshold: ragSettings.threshold,
      hybrid_weight: ragSettings.hybridWeight,
    });
    this.sendMessage({
      type: 'fetch-backgrounds',
    });
    this.sendMessage({
      type: 'fetch-configs',
    });
    this.sendMessage({
      type: 'fetch-history-list',
    });
    this.sendMessage({
      type: 'create-new-history',
    });
  }

  connect(url: string) {
    if (!this.accountName || !this.sessionToken) {
      this.disconnect();
      return;
    }
    this.disconnect();

    try {
      const authenticatedUrl = new URL(url);
      authenticatedUrl.searchParams.set('account', this.accountName);
      authenticatedUrl.searchParams.set('session', this.sessionToken);
      const socket = new WebSocket(authenticatedUrl.toString());
      const generation = this.connectionGeneration;
      this.ws = socket;
      this.currentState = 'CONNECTING';
      this.stateSubject.next('CONNECTING');

      socket.onopen = () => {
        if (this.ws !== socket || generation !== this.connectionGeneration) return;
        this.currentState = 'OPEN';
        this.stateSubject.next('OPEN');
        this.initializeConnection();
      };

      socket.onmessage = (event) => {
        if (this.ws !== socket || generation !== this.connectionGeneration) return;
        try {
          const message = JSON.parse(event.data);
          this.messageSubject.next(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
          toaster.create({
            title: `${getTranslation()('error.failedParseWebSocket')}: ${error}`,
            type: "error",
            duration: 2000,
          });
        }
      };

      socket.onclose = () => {
        if (this.ws !== socket || generation !== this.connectionGeneration) return;
        this.ws = null;
        this.currentState = 'CLOSED';
        this.stateSubject.next('CLOSED');
      };

      socket.onerror = () => {
        if (this.ws !== socket || generation !== this.connectionGeneration) return;
        this.currentState = 'CLOSED';
        this.stateSubject.next('CLOSED');
      };
    } catch (error) {
      console.error('Failed to connect to WebSocket:', error);
      this.currentState = 'CLOSED';
      this.stateSubject.next('CLOSED');
    }
  }

  sendMessage(message: object) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket is not open. Unable to send message:', message);
      toaster.create({
        title: getTranslation()('error.websocketNotOpen'),
        type: 'error',
        duration: 2000,
      });
    }
  }

  onMessage(callback: (message: MessageEvent) => void) {
    return this.messageSubject.subscribe(callback);
  }

  onStateChange(callback: (state: 'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED') => void) {
    return this.stateSubject.subscribe(callback);
  }

  disconnect() {
    this.connectionGeneration += 1;
    const socket = this.ws;
    this.ws = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      if (socket.readyState === WebSocket.CONNECTING ||
          socket.readyState === WebSocket.OPEN) {
        socket.close();
      }
    }
    this.currentState = 'CLOSED';
    this.stateSubject.next('CLOSED');
  }

  setAccount(accountName: string | null, sessionToken: string | null = null) {
    if (this.accountName !== accountName || this.sessionToken !== sessionToken) {
      this.disconnect();
    }
    this.accountName = accountName;
    this.sessionToken = sessionToken;
  }

  getCurrentState() {
    return this.currentState;
  }
}

export const wsService = WebSocketService.getInstance();
