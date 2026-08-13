import { ReactNode } from 'react';

export const optionalFeature = {
  consumeForUserMessage: (): Record<string, unknown> | null => null,
  beginProactiveSpeak: (): void => {},
  onConversationStart: (): void => {},
  onConversationEnd: (): void => {},
};

export function useOptionalFeatureAvailability(): boolean {
  return false;
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

export function OptionalGeneralSettings(_props: {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}): JSX.Element | null {
  return null;
}

export function OptionalBackground({ fallback }: { fallback: ReactNode }) {
  return fallback;
}

export function useOptionalBackgroundActive(): boolean {
  return false;
}
