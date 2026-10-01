import { ReactNode } from 'react';
import { useCamera } from '@optional-feature';
import { ProfilerMediaGateProvider } from '@/context/profiler-media-gate-context';

export function ProfilerMediaGateWithCamera({ children }: { children: ReactNode }) {
  const camera = useCamera();
  return (
    <ProfilerMediaGateProvider camera={camera}>
      {children}
    </ProfilerMediaGateProvider>
  );
}
