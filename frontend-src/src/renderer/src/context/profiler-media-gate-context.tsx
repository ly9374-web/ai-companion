import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Button, Text } from '@chakra-ui/react';
import { useTranslation } from 'react-i18next';
import {
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogRoot,
  DialogTitle,
} from '@/components/ui/dialog';
import { useConfig } from '@/context/character-config-context';
import { wsService } from '@/services/websocket-service';
import {
  getGeneralRuntimeSettings,
  setGeneralRuntimeSettings,
} from '@/constants/general-runtime-settings';

const PROFILER_CONF_UID = 'profile_analyst_001';
const PROFILER_START_COMMAND = '开始侧写';

interface MissingRequirements {
  camera: boolean;
  generateAudio: boolean;
}

interface ProfilerMediaGateState {
  ensureReadyForMessage: (text: string) => Promise<boolean>;
}

export interface ProfilerCameraControl {
  isStreaming: boolean;
  startCamera: () => Promise<void>;
}

const ProfilerMediaGateContext = createContext<ProfilerMediaGateState | null>(null);

function isProfilerStartMessage(confUid: string, text: string): boolean {
  const firstLine = text.trim().split(/\r?\n/, 1)[0]?.trim();
  return confUid === PROFILER_CONF_UID && firstLine === PROFILER_START_COMMAND;
}

export function ProfilerMediaGateProvider({
  camera,
  children,
}: {
  camera: ProfilerCameraControl;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { confUid } = useConfig();
  const { isStreaming, startCamera } = camera;
  const [open, setOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startFailed, setStartFailed] = useState(false);
  const [missing, setMissing] = useState<MissingRequirements>({ camera: false, generateAudio: false });
  const resolverRef = useRef<((ready: boolean) => void) | null>(null);

  const finishRequest = useCallback((ready: boolean) => {
    resolverRef.current?.(ready);
    resolverRef.current = null;
    setOpen(false);
    setStarting(false);
    setStartFailed(false);
  }, []);

  useEffect(() => () => {
    resolverRef.current?.(false);
    resolverRef.current = null;
  }, []);

  const ensureReadyForMessage = useCallback(async (text: string) => {
    if (!isProfilerStartMessage(confUid, text)) return true;

    const nextMissing = {
      camera: !isStreaming,
      generateAudio: !getGeneralRuntimeSettings().generateAudio,
    };
    if (!nextMissing.camera && !nextMissing.generateAudio) return true;

    resolverRef.current?.(false);
    setMissing(nextMissing);
    setStartFailed(false);
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, [confUid, isStreaming]);

  const handleConfirm = useCallback(async () => {
    if (starting) return;
    setStarting(true);
    setStartFailed(false);

    if (missing.generateAudio) {
      setGeneralRuntimeSettings({
        ...getGeneralRuntimeSettings(),
        generateAudio: true,
      });
      wsService.sendMessage({ type: 'set-generate-audio', enabled: true });
    }

    const cameraResult = missing.camera
      ? await startCamera().then(() => true).catch(() => false)
      : true;

    if (cameraResult) {
      finishRequest(true);
      return;
    }

    setMissing({ camera: true, generateAudio: false });
    setStartFailed(true);
    setStarting(false);
  }, [finishRequest, missing, startCamera, starting]);

  const missingDevices = [
    missing.camera ? t('profilerMedia.camera') : '',
    missing.generateAudio ? t('profilerMedia.generateAudio') : '',
  ].filter(Boolean).join('、');

  const value = useMemo(() => ({ ensureReadyForMessage }), [ensureReadyForMessage]);

  return (
    <ProfilerMediaGateContext.Provider value={value}>
      {children}
      <DialogRoot
        open={open}
        onOpenChange={(details) => {
          if (!details.open && !starting) finishRequest(false);
        }}
        closeOnEscape={!starting}
        closeOnInteractOutside={!starting}
      >
        <DialogContent maxW="min(460px, calc(100vw - 32px))">
          <DialogHeader>
            <DialogTitle>{t('profilerMedia.title')}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <Text>
              {t('profilerMedia.description', { devices: missingDevices })}
            </Text>
            {startFailed && (
              <Text mt={3} color="red.300">
                {t('profilerMedia.startFailed')}
              </Text>
            )}
          </DialogBody>
          <DialogFooter gap={3}>
            <Button variant="outline" disabled={starting} onClick={() => finishRequest(false)}>
              {t('common.cancel')}
            </Button>
            <Button colorPalette="blue" loading={starting} onClick={handleConfirm}>
              {starting ? t('profilerMedia.starting') : t('profilerMedia.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogRoot>
    </ProfilerMediaGateContext.Provider>
  );
}

export function useProfilerMediaGate(): ProfilerMediaGateState {
  const context = useContext(ProfilerMediaGateContext);
  if (!context) {
    throw new Error('useProfilerMediaGate must be used within ProfilerMediaGateProvider');
  }
  return context;
}
