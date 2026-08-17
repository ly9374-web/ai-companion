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
import { Box, Tabs, Text } from '@chakra-ui/react';
import { FiCamera } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import { toaster } from '@/components/ui/toaster';
import { Tooltip } from '@/components/ui/tooltip';
import { sidebarStyles } from '@/components/sidebar/sidebar-styles';
import { getCurrentBaseUrl } from '@/constants/connection-settings';

interface EmotionAggregate {
  emotions: string[];
  valid_duration_ms: number;
}

interface RuntimeFeature {
  start: (stream: MediaStream) => Promise<void>;
  stop: () => void;
  startWindow: () => void;
  pauseWindow: () => void;
  resumeWindow: () => void;
  consumeWindow: () => EmotionAggregate | null;
}

interface CameraState {
  available: boolean;
  isStreaming: boolean;
  stream: MediaStream | null;
  startCamera: () => Promise<void>;
  stopCamera: () => void;
}

const CAMERA_COPY = {
  zh: {
    label: '摄像头', control: '点击启动摄像头', stopping: '点击停止摄像头',
    apiUnsupported: '此设备不支持摄像头API',
    secureRequired: '摄像头只能在 localhost 或 HTTPS 安全页面中使用。',
    permissionBlocked: '此站点的摄像头权限已被阻止，请允许摄像头权限后刷新。',
    permissionDenied: '没有获得摄像头权限，请在权限提示中选择允许。',
    notFound: '未找到摄像头设备', inUse: '摄像头可能正被其他应用占用。',
    startFailed: '启动摄像头失败',
  },
  en: {
    label: 'Camera', control: 'Click to start camera', stopping: 'Click to stop camera',
    apiUnsupported: 'Camera API is not supported on this device',
    secureRequired: 'Camera access requires localhost or HTTPS.',
    permissionBlocked: 'Camera access is blocked. Allow camera access, then reload.',
    permissionDenied: 'Camera permission was not granted.', notFound: 'No camera found on this device',
    inUse: 'The camera may be in use by another application.', startFailed: 'Failed to start camera',
  },
};

function useCameraCopy() {
  const { i18n } = useTranslation();
  return i18n.language.toLowerCase().startsWith('zh') ? CAMERA_COPY.zh : CAMERA_COPY.en;
}

const CameraContext = createContext<CameraState | null>(null);
let runtime: RuntimeFeature | null = null;
let loadPromise: Promise<void> | null = null;
let featureAvailable = false;
let proactiveSpeakPending = false;
const availabilityListeners = new Set<(available: boolean) => void>();

function publishAvailability(available: boolean) {
  featureAvailable = available;
  availabilityListeners.forEach((listener) => listener(available));
}

async function ensureLoaded() {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    try {
      const baseUrl = getCurrentBaseUrl();
      const response = await fetch(new URL('/optional-feature/manifest', baseUrl), { cache: 'no-store' });
      const manifest = await response.json();
      if (!response.ok || !manifest.available || !manifest.frontend_entry) {
        publishAvailability(false);
        return;
      }
      const module = await import(/* @vite-ignore */ new URL(manifest.frontend_entry, response.url || baseUrl).href);
      runtime = module.createCameraEmotionFeature(manifest.config || {});
      publishAvailability(true);
    } catch (error) {
      console.warn('[OptionalFeature] 摄像头模块不可用:', error);
      runtime = null;
      publishAvailability(false);
    }
  })();
  return loadPromise;
}

void ensureLoaded();

export const optionalFeature = {
  consumeForUserMessage(): Record<string, unknown> | null {
    proactiveSpeakPending = false;
    const aggregate = runtime?.consumeWindow() || null;
    return aggregate ? { camera_emotion: aggregate } : null;
  },
  beginProactiveSpeak() {
    proactiveSpeakPending = true;
  },
  onConversationStart() {
    if (!proactiveSpeakPending) return;
    proactiveSpeakPending = false;
    runtime?.pauseWindow();
  },
  onConversationEnd() {
    proactiveSpeakPending = false;
    runtime?.startWindow();
  },
};

export function useOptionalFeatureAvailability() {
  const [available, setAvailable] = useState(featureAvailable);
  useEffect(() => {
    availabilityListeners.add(setAvailable);
    void ensureLoaded();
    return () => {
      availabilityListeners.delete(setAvailable);
    };
  }, []);
  return available;
}

function useCamera() {
  const value = useContext(CameraContext);
  if (!value) throw new Error('Camera feature must be inside OptionalFeatureProvider');
  return value;
}

async function permissionState(): Promise<PermissionState | null> {
  try {
    return (await navigator.permissions?.query({ name: 'camera' } as PermissionDescriptor))?.state || null;
  } catch (_error) {
    return null;
  }
}

export function OptionalFeatureProvider({ children }: { children: ReactNode }) {
  const copy = useCameraCopy();
  const available = useOptionalFeatureAvailability();
  const [stream, setStream] = useState<MediaStream | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const openCamera = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error(copy.apiUnsupported);
    if (!window.isSecureContext) throw new Error(copy.secureRequired);
    return navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        frameRate: { ideal: 25, max: 30 },
      },
      audio: false,
    });
  }, [copy]);

  const startCamera = useCallback(async () => {
    let next: MediaStream | null = null;
    try {
      next = await openCamera();
      streamRef.current = next;
      setStream(next);
      await ensureLoaded();
      await runtime?.start(next);
      runtime?.startWindow();
    } catch (error) {
      next?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setStream(null);
      let message = error instanceof Error ? error.message : String(error);
      if (error instanceof DOMException && ['NotAllowedError', 'SecurityError'].includes(error.name)) {
        message = await permissionState() === 'denied'
          ? copy.permissionBlocked : copy.permissionDenied;
      } else if (error instanceof DOMException && error.name === 'NotFoundError') {
        message = copy.notFound;
      } else if (error instanceof DOMException && error.name === 'NotReadableError') {
        message = copy.inUse;
      }
      toaster.create({ title: copy.startFailed, description: message, type: 'error', duration: 8000 });
      throw new Error(message);
    }
  }, [openCamera, copy]);

  const stopCamera = useCallback(() => {
    runtime?.stop();
    setStream((current) => {
      current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      return null;
    });
  }, []);

  useEffect(() => () => {
    runtime?.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const value = useMemo(() => ({
    available,
    isStreaming: stream !== null,
    stream,
    startCamera,
    stopCamera,
  }), [available, stream, startCamera, stopCamera]);

  return <CameraContext.Provider value={value}>{children}</CameraContext.Provider>;
}

export function OptionalSidebarTrigger() {
  const copy = useCameraCopy();
  return <Tabs.Trigger value="optional-feature" {...sidebarStyles.bottomTab.trigger}><FiCamera />{copy.label}</Tabs.Trigger>;
}

export function OptionalSidebarContent() {
  const copy = useCameraCopy();
  const { stream, isStreaming, startCamera, stopCamera } = useCamera();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState('');
  useEffect(() => { if (videoRef.current) videoRef.current.srcObject = stream; }, [stream]);
  const toggle = async () => {
    try {
      if (isStreaming) stopCamera(); else await startCamera();
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };
  return (
    <Tabs.Content value="optional-feature">
      <Box width="97%" px={4} minH="240px">
        <Tooltip showArrow content={isStreaming ? copy.stopping : copy.control}>
          <Box height="240px" display="flex" alignItems="center" justifyContent="center" overflow="hidden" cursor="pointer" onClick={toggle} bg="blackAlpha.400" borderRadius="8px">
            {error ? <Text color="red.300" px={4}>{error}</Text> : (
              isStreaming
                ? <video ref={videoRef} autoPlay playsInline muted style={{ width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' }} />
                : <Box textAlign="center"><FiCamera size={24} /><Text>{copy.control}</Text></Box>
            )}
          </Box>
        </Tooltip>
      </Box>
    </Tabs.Content>
  );
}
