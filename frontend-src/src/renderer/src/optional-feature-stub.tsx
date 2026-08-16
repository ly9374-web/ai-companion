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
