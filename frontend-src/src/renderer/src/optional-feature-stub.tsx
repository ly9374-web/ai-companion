import { ReactNode } from 'react';

export const optionalFeature = {
  consumeForUserMessage: (): Record<string, unknown> | null => null,
  beginProactiveSpeak: (): void => {},
  onConversationStart: (): void => {},
  onConversationEnd: (): void => {},
  getEmotionSegmentMinMs: (): number => 1500,
  setEmotionSegmentMinMs: (_ms: number): void => {},
};

export function useOptionalFeatureAvailability(): boolean {
  return false;
}

// Safe no-op defaults so callers (e.g. CameraInviteDialog) can render even
// when the optional camera module is not installed; `available` stays false
// so any "open camera" affordances are suppressed.
export function useCamera() {
  return {
    available: false,
    isStreaming: false,
    stream: null as MediaStream | null,
    startCamera: async (): Promise<void> => {},
    stopCamera: (): void => {},
  };
}

export function OptionalFeatureProvider({ children }: { children: ReactNode }) {
  return children;
}

export function OptionalSidebarTrigger(): JSX.Element | null {
  return null;
}

export function OptionalSidebarContent(): JSX.Element | null {
  return null;
}

// 设置抽屉里的“一眸”挂载点：无摄像头模块时不渲染任何内容。
export function OptionalSettingsTrigger(): JSX.Element | null {
  return null;
}

export function OptionalSettingsContent(): JSX.Element | null {
  return null;
}
