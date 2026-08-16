/* eslint-disable react/jsx-no-constructed-context-values */
import React, { useContext, useCallback } from 'react';
import { wsService } from '@/services/websocket-service';
import {
  DEFAULT_BASE_URL,
  DEFAULT_WS_URL,
  getCurrentBaseUrl,
  getCurrentWsUrl,
  setCurrentBaseUrl,
  setCurrentWsUrl,
} from '@/constants/connection-settings';


export interface HistoryInfo {
  uid: string;
  latest_message: {
    role: 'human' | 'ai';
    timestamp: string;
    content: string;
  } | null;
  timestamp: string | null;
}

interface WebSocketContextProps {
  sendMessage: (message: object) => void;
  wsState: string;
  reconnect: () => void;
  wsUrl: string;
  setWsUrl: (url: string) => void;
  baseUrl: string;
  setBaseUrl: (url: string) => void;
}

export const WebSocketContext = React.createContext<WebSocketContextProps>({
  sendMessage: wsService.sendMessage.bind(wsService),
  wsState: 'CLOSED',
  reconnect: () => wsService.connect(DEFAULT_WS_URL),
  wsUrl: DEFAULT_WS_URL,
  setWsUrl: () => {},
  baseUrl: DEFAULT_BASE_URL,
  setBaseUrl: () => {},
});

export function useWebSocket() {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
}

export const defaultWsUrl = DEFAULT_WS_URL;
export const defaultBaseUrl = DEFAULT_BASE_URL;

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [wsUrl, setWsUrlState] = React.useState(getCurrentWsUrl);
  const [baseUrl, setBaseUrlState] = React.useState(getCurrentBaseUrl);
  const handleSetWsUrl = useCallback((url: string) => {
    setCurrentWsUrl(url);
    setWsUrlState(url);
    wsService.connect(url);
  }, []);
  const handleSetBaseUrl = useCallback((url: string) => {
    setCurrentBaseUrl(url);
    setBaseUrlState(url);
  }, []);

  const value = {
    sendMessage: wsService.sendMessage.bind(wsService),
    wsState: 'CLOSED',
    reconnect: () => wsService.connect(wsUrl),
    wsUrl,
    setWsUrl: handleSetWsUrl,
    baseUrl,
    setBaseUrl: handleSetBaseUrl,
  };

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
}
