import { ReactNode } from 'react';

export const optionalFeature = {
  consumeForUserMessage: (): Record<string, unknown> | null => null,
  requestHeartRateForNextMessage: (): void => {},
  clearHeartRateRequest: (): void => {},
  beginProactiveSpeak: (): void => {},
  onConversationStart: (): void => {},
  onConversationEnd: (): void => {},
  onAssistantAudioStart: (_detail: { text: string }): void => {},
  onAssistantAudioEnd: (_detail: {
    text: string;
    interrupted: boolean;
    playbackRatio: number;
  }): void => {},
  consumeAssistantResponse: (): Record<string, unknown> | null => null,
  handleWebSocketMessage: (_message: unknown): void => {},
  getEmotionSegmentMinMs: (): number => 1500,
  setEmotionSegmentMinMs: (_ms: number): void => {},
};

export function useOptionalFeatureAvailability(): boolean {
  return false;
}

export function useCamera() {
  return {
    available: false,
    isStreaming: false,
    stream: null as MediaStream | null,
    startedAt: null as number | null,
    startCamera: async (): Promise<void> => {
      throw new Error('Camera feature is unavailable');
    },
    stopCamera: (): void => {},
  };
}

export function OptionalFeatureProvider({ children }: { children: ReactNode }) {
  return children;
}

export function OptionalFeatureRuntime(): JSX.Element | null {
  return null;
}

export function OptionalSidebarArea(): JSX.Element | null {
  return null;
}

export function OptionalChatHistoryExtras(): JSX.Element | null {
  return null;
}

export function OptionalGeneralSettings(_props: {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}): JSX.Element | null {
  return null;
}

export function useOptionalMediaCapture() {
  return { captureAllMedia: async () => [] as Array<{
    source: 'screen';
    data: string;
    mime_type: string;
  }> };
}

export function OptionalSettingsTrigger(): JSX.Element | null {
  return null;
}

export function OptionalSettingsContent(): JSX.Element | null {
  return null;
}
