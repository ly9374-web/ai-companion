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
import {
  Box,
  Button,
  Flex,
  HStack,
  Input,
  Stack,
  Tabs,
  Text,
} from '@chakra-ui/react';
import { FiCamera, FiGlobe, FiMonitor } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import { toaster } from '@/components/ui/toaster';
import { Tooltip } from '@/components/ui/tooltip';
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogBody,
  DialogFooter,
  DialogTitle,
  DialogDescription,
  DialogCloseTrigger,
} from '@/components/ui/dialog';
import { Slider } from '@/components/ui/slider';
import { settingStyles } from '@/components/sidebar/setting/setting-styles';
import { getCurrentBaseUrl } from '@/constants/connection-settings';
import { useAccount } from '@/context/account-context';
import { useChatHistory } from '@/context/chat-history-context';
import { useConfig } from '@/context/character-config-context';
import { useSendTextMessage } from '@/hooks/footer/use-text-input';
import { NumberField } from '@/components/sidebar/setting/common';
import {
  getStoredImageCompressionQuality,
  getStoredImageMaxWidth,
} from '@/constants/image-settings';

declare class ImageCapture {
  constructor(track: MediaStreamTrack);
  grabFrame(): Promise<ImageBitmap>;
}

interface EmotionSettings {
  minimumSignalNorm: number;
  emotionThresholdSadness: number;
  emotionThresholdAnger: number;
  emotionThresholdSurprise: number;
  emotionThresholdHappiness: number;
  emotionSegmentMinMs: number;
}

interface HeartRateAggregate {
  avg_bpm: number;
  sample_count: number;
}

interface EmotionDurationAggregate {
  emotions: string[];
  duration_ms: number;
}

interface EmotionAggregate {
  emotions: string[];
  emotion_durations: EmotionDurationAggregate[];
  valid_duration_ms: number;
  heart_rate?: HeartRateAggregate;
}

interface RuntimeProfileState {
  activeName: string;
  lastUsedName: string;
  profileNames: string[];
  calibrationActive: boolean;
  settings: EmotionSettings;
}

interface CalibrationStepProgress {
  state: 'step';
  stepIndex: number;
  total: number;
  label: string;
  prompt: string;
  kind: string;
  phase: 'ready' | 'capturing';
  message: string;
  displayName: string;
}

type CalibrationProgress =
  | CalibrationStepProgress
  | { state: 'done'; displayName?: string }
  | { state: 'cancelled' };

interface RuntimeActionResult {
  ok: boolean;
  error?: string;
}

interface RuntimeFeature {
  start: (stream: MediaStream) => Promise<void>;
  stop: () => void;
  startWindow: () => void;
  pauseWindow: () => void;
  resumeWindow: () => void;
  consumeWindow: () => EmotionAggregate | null;
  beginAssistantResponse: () => void;
  startAssistantSegment: (text: string) => void;
  finishAssistantSegment: (details: {
    interrupted?: boolean;
    playbackRatio?: number;
  }) => void;
  finishAssistantResponse: () => void;
  consumeAssistantResponse: () => Record<string, unknown> | null;
  getProfileState: () => RuntimeProfileState;
  getSettings: () => EmotionSettings;
  subscribeProfileState: (listener: (state: RuntimeProfileState) => void) => () => void;
  subscribeCalibration: (listener: (progress: CalibrationProgress) => void) => () => void;
  applySettings: (partial: Partial<EmotionSettings>) => void;
  activateProfileByName: (name: string) => RuntimeActionResult;
  beginPersonalCalibration: (name: string) => RuntimeActionResult;
  captureCalibrationStep: () => void;
  cancelCalibration: () => void;
  useGenericProfile: () => void;
  deleteActiveProfile: () => RuntimeActionResult;
  getLatestHeartRate?: () => { bpm: number; at: number } | null;
}

interface CameraState {
  available: boolean;
  isStreaming: boolean;
  stream: MediaStream | null;
  startedAt: number | null;
  startCamera: () => Promise<void>;
  stopCamera: () => void;
}

interface ScreenCaptureState {
  stream: MediaStream | null;
  isStreaming: boolean;
  error: string;
  startCapture: () => Promise<void>;
  stopCapture: () => void;
}

interface BrowserViewData {
  debuggerFullscreenUrl: string;
  debuggerUrl: string;
  pages: Array<{
    id: string;
    url: string;
    faviconUrl: string;
    title: string;
    debuggerUrl: string;
    debuggerFullscreenUrl: string;
  }>;
  wsUrl: string;
  sessionId?: string;
}

interface OptionalSettingsCallbacks {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

const FEATURE_COPY = {
  zh: {
    screen: '共享屏幕', browser: '浏览器', screenControl: '点击开始屏幕共享',
    screenStopping: '点击停止屏幕共享', screenFailed: '启动屏幕捕获失败',
    noBrowserSession: '无活跃浏览器会话', browserSession: '浏览器会话',
    inviteTitle: '开启摄像头？',
    inviteDescription: '该账号支持表情识别。现在开启摄像头并采集你的表情基线吗？',
    inviteCancel: '暂不开启', inviteConfirm: '开启摄像头',
    emotionSegment: '表情段最小时长(ms)',
    emotionSegmentHelp: '单段表情连续持续不足此时长则不计入本轮输出（中性段不受此限制）。范围0-10000，默认1500。',
  },
  en: {
    screen: 'Screen', browser: 'Browser', screenControl: 'Click to start screen capture',
    screenStopping: 'Click to stop screen capture', screenFailed: 'Failed to start screen capture',
    noBrowserSession: 'No active browser session', browserSession: 'Browser Session',
    inviteTitle: 'Enable Camera?',
    inviteDescription: 'This account supports emotion recognition. Open the camera now to capture your expression baseline?',
    inviteCancel: 'Not Now', inviteConfirm: 'Enable Camera',
    emotionSegment: 'Min Expression Segment (ms)',
    emotionSegmentHelp: 'Expression segments shorter than this are excluded (neutral is exempt). Range 0-10000; default 1500.',
  },
};

const OPTIONAL_SIDEBAR_STYLES = {
  root: { width: '97%', px: 4, position: 'relative' as const, zIndex: 0 },
  list: { borderBottom: 'none', gap: 2 },
  trigger: {
    color: 'whiteAlpha.700', display: 'flex', alignItems: 'center', gap: 2,
    _selected: { color: 'white', bg: 'whiteAlpha.200' },
  },
  panel: { width: '97%', overflow: 'hidden', px: 4, minH: '240px' },
  preview: {
    width: '100%', height: '240px', display: 'flex', alignItems: 'center',
    justifyContent: 'center', overflow: 'hidden', bg: 'blackAlpha.400',
    border: '1px solid', borderColor: 'whiteAlpha.200', borderRadius: 'lg',
  },
};

const CAMERA_COPY = {
  zh: {
    label: '摄像头', control: '点击启动摄像头', stopping: '点击停止摄像头',
    relaxPrompt: '保持面部肌肉放松',
    apiUnsupported: '此设备不支持摄像头API',
    secureRequired: '摄像头只能在 localhost 或 HTTPS 安全页面中使用。',
    permissionBlocked: '此站点的摄像头权限已被阻止，请允许摄像头权限后刷新。',
    permissionDenied: '没有获得摄像头权限，请在权限提示中选择允许。',
    notFound: '未找到摄像头设备', inUse: '摄像头可能正被其他应用占用。',
    startFailed: '启动摄像头失败',
  },
  en: {
    label: 'Camera', control: 'Click to start camera', stopping: 'Click to stop camera',
    relaxPrompt: 'Keep your facial muscles relaxed',
    apiUnsupported: 'Camera API is not supported on this device',
    secureRequired: 'Camera access requires localhost or HTTPS.',
    permissionBlocked: 'Camera access is blocked. Allow camera access, then reload.',
    permissionDenied: 'Camera permission was not granted.', notFound: 'No camera found on this device',
    inUse: 'The camera may be in use by another application.', startFailed: 'Failed to start camera',
  },
};

const YIMOU_COPY = {
  zh: {
    tabLabel: '一眸',
    baselineSection: '个人基线',
    baselineButton: '个人基线',
    activeProfilePrefix: '当前使用个人模板',
    genericProfile: '当前使用通用表情模板',
    noProfile: '尚未录制个人基线',
    lastUsedPrefix: '下次开启摄像头将使用',
    nameDialogTitle: '个人表情基线',
    nameDialogDesc: '输入名字开始录制个人表情基线；已录制过的名字会直接启用对应基线。',
    nameLabel: '你的名字',
    namePlaceholder: '例如：小明',
    startRecord: '开始录制',
    cancel: '取消',
    recalibrate: '重新录制',
    exitPersonal: '退出个人模式',
    deleteProfile: '删除档案',
    deleteConfirm: '确定删除该档案？删除后将回退到通用表情模板。',
    settingsSection: '识别参数',
    minimumSignalNorm: '中性阈值（minimumSignalNorm）',
    thresholdSadness: '悲伤相似度阈值',
    thresholdAnger: '愤怒相似度阈值',
    thresholdSurprise: '惊讶相似度阈值',
    thresholdHappiness: '开心相似度阈值',
    profileLoaded: '已启用个人基线',
    calibratingTitle: '录制个人基线',
    stepLabel: '步骤',
    capture: '确认并录制',
    capturing: '正在采集…',
    calibCancel: '取消录制',
    calibDone: '录制完成，已启用个人基线',
    calibStartFailed: '无法开始录制',
    exited: '已切换回通用表情模板',
    deleted: '档案已删除',
  },
  en: {
    tabLabel: 'Yimou',
    baselineSection: 'Personal Baseline',
    baselineButton: 'Personal Baseline',
    activeProfilePrefix: 'Using personal template',
    genericProfile: 'Using generic emotion templates',
    noProfile: 'No personal baseline recorded yet',
    lastUsedPrefix: 'Will be used when the camera starts next time',
    nameDialogTitle: 'Personal Emotion Baseline',
    nameDialogDesc: 'Enter a name to record your personal emotion baseline; an existing name will be activated directly.',
    nameLabel: 'Your name',
    namePlaceholder: 'e.g. Alex',
    startRecord: 'Start Recording',
    cancel: 'Cancel',
    recalibrate: 'Re-record',
    exitPersonal: 'Use Generic',
    deleteProfile: 'Delete Profile',
    deleteConfirm: 'Delete this profile and fall back to generic templates?',
    settingsSection: 'Detection Parameters',
    minimumSignalNorm: 'Neutral threshold (minimumSignalNorm)',
    thresholdSadness: 'Sadness similarity threshold',
    thresholdAnger: 'Anger similarity threshold',
    thresholdSurprise: 'Surprise similarity threshold',
    thresholdHappiness: 'Happiness similarity threshold',
    profileLoaded: 'Personal baseline activated',
    calibratingTitle: 'Recording Personal Baseline',
    stepLabel: 'Step',
    capture: 'Confirm & Record',
    capturing: 'Capturing…',
    calibCancel: 'Cancel Recording',
    calibDone: 'Recording complete, personal baseline activated',
    calibStartFailed: 'Failed to start recording',
    exited: 'Switched back to generic templates',
    deleted: 'Profile deleted',
  },
};

const DEFAULT_EMOTION_SETTINGS: EmotionSettings = {
  minimumSignalNorm: 0.12,
  emotionThresholdSadness: 0.75,
  emotionThresholdAnger: 0.75,
  emotionThresholdSurprise: 0.75,
  emotionThresholdHappiness: 0.75,
  emotionSegmentMinMs: 1500,
};

function useCameraCopy() {
  const { i18n } = useTranslation();
  return i18n.language.toLowerCase().startsWith('zh') ? CAMERA_COPY.zh : CAMERA_COPY.en;
}

function useYimouCopy() {
  const { i18n } = useTranslation();
  return i18n.language.toLowerCase().startsWith('zh') ? YIMOU_COPY.zh : YIMOU_COPY.en;
}

function useFeatureCopy() {
  const { i18n } = useTranslation();
  return i18n.language.toLowerCase().startsWith('zh') ? FEATURE_COPY.zh : FEATURE_COPY.en;
}

const CameraContext = createContext<CameraState | null>(null);
const ScreenCaptureContext = createContext<ScreenCaptureState | null>(null);
const BrowserViewContext = createContext<BrowserViewData | null>(null);
let latestBrowserView: BrowserViewData | null = null;
const browserViewListeners = new Set<(value: BrowserViewData | null) => void>();
let runtime: RuntimeFeature | null = null;
let loadPromise: Promise<void> | null = null;
let featureAvailable = false;
let proactiveSpeakPending = false;
// 一次性标记：按 j 后，下一条用户消息附带发送时刻的心率。
let heartRateRequested = false;
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
    const heartRateRequestedNow = heartRateRequested;
    heartRateRequested = false;
    // 读取发送时刻的心率（5 秒内有效），与窗口聚合相互独立。
    const requestedHeartRate = heartRateRequestedNow
      ? runtime?.getLatestHeartRate?.() || null
      : null;
    const aggregate = runtime?.consumeWindow() || null;
    if (!aggregate && !requestedHeartRate) return null;
    const cameraEmotion: Record<string, unknown> = { ...(aggregate || {}) };
    if (requestedHeartRate) cameraEmotion.requested_heart_rate_bpm = requestedHeartRate.bpm;
    return { camera_emotion: cameraEmotion };
  },
  requestHeartRateForNextMessage() {
    heartRateRequested = true;
  },
  clearHeartRateRequest() {
    heartRateRequested = false;
  },
  beginProactiveSpeak() {
    proactiveSpeakPending = true;
  },
  onConversationStart() {
    proactiveSpeakPending = false;
    runtime?.beginAssistantResponse();
  },
  onConversationEnd() {
    proactiveSpeakPending = false;
    runtime?.finishAssistantResponse();
  },
  onAssistantAudioStart(details: { text?: string }) {
    runtime?.startAssistantSegment(details?.text || '');
  },
  onAssistantAudioEnd(details: { interrupted?: boolean; playbackRatio?: number }) {
    runtime?.finishAssistantSegment(details || {});
  },
  consumeAssistantResponse(): Record<string, unknown> | null {
    const aggregate = runtime?.consumeAssistantResponse() || null;
    return aggregate ? { camera_emotion: aggregate } : null;
  },
  handleWebSocketMessage(message: unknown) {
    if (!message || typeof message !== 'object') return;
    const browserView = (message as { browser_view?: unknown }).browser_view;
    if (!browserView || typeof browserView !== 'object') return;
    latestBrowserView = browserView as BrowserViewData;
    browserViewListeners.forEach((listener) => listener(latestBrowserView));
  },
  getEmotionSegmentMinMs(): number {
    return runtime?.getSettings()?.emotionSegmentMinMs ?? 1500;
  },
  setEmotionSegmentMinMs(ms: number): void {
    runtime?.applySettings({ emotionSegmentMinMs: ms });
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

export function useCamera() {
  const value = useContext(CameraContext);
  if (!value) throw new Error('Camera feature must be inside OptionalFeatureProvider');
  return value;
}

function OptionalMediaProvider({ children }: { children: ReactNode }) {
  const copy = useFeatureCopy();
  const [stream, setStream] = useState<MediaStream | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [error, setError] = useState('');
  const [browserView, setBrowserView] = useState<BrowserViewData | null>(latestBrowserView);

  const startCapture = useCallback(async () => {
    try {
      let mediaStream: MediaStream;
      if (window.electron) {
        const sourceId = await window.electron.ipcRenderer.invoke('get-screen-capture');
        mediaStream = await navigator.mediaDevices.getUserMedia({
          video: {
            // Electron's desktop capture constraint is Chromium-specific.
            // @ts-expect-error Chromium desktop constraint
            mandatory: {
              chromeMediaSource: 'desktop',
              chromeMediaSourceId: sourceId,
              minWidth: 1280,
              maxWidth: 1280,
              minHeight: 720,
              maxHeight: 720,
            },
          },
          audio: false,
        });
      } else {
        mediaStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      }
      streamRef.current = mediaStream;
      setStream(mediaStream);
      setError('');
      mediaStream.getVideoTracks()[0]?.addEventListener('ended', () => {
        streamRef.current = null;
        setStream(null);
      }, { once: true });
    } catch (caught) {
      const message = `${copy.screenFailed}: ${caught}`;
      setError(message);
      toaster.create({ title: message, type: 'error', duration: 2000 });
    }
  }, [copy]);

  const stopCapture = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setStream(null);
  }, []);

  useEffect(() => {
    browserViewListeners.add(setBrowserView);
    return () => {
      browserViewListeners.delete(setBrowserView);
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  const screenValue = useMemo(() => ({
    stream,
    isStreaming: stream !== null,
    error,
    startCapture,
    stopCapture,
  }), [stream, error, startCapture, stopCapture]);

  return (
    <ScreenCaptureContext.Provider value={screenValue}>
      <BrowserViewContext.Provider value={browserView}>
        {children}
      </BrowserViewContext.Provider>
    </ScreenCaptureContext.Provider>
  );
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
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const openCamera = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error(copy.apiUnsupported);
    if (!window.isSecureContext) throw new Error(copy.secureRequired);
    return navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 20, max: 24 },
      },
      audio: false,
    });
  }, [copy]);

  const startCamera = useCallback(async () => {
    let next: MediaStream | null = null;
    try {
      next = await openCamera();
      streamRef.current = next;
      setStartedAt(Date.now());
      setStream(next);
      await ensureLoaded();
      await runtime?.start(next);
      runtime?.startWindow();
    } catch (error) {
      next?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setStartedAt(null);
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
    setStartedAt(null);
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
    startedAt,
    startCamera,
    stopCamera,
  }), [available, stream, startedAt, startCamera, stopCamera]);

  return (
    <CameraContext.Provider value={value}>
      <OptionalMediaProvider>{children}</OptionalMediaProvider>
    </CameraContext.Provider>
  );
}

export function OptionalSidebarTrigger() {
  const copy = useCameraCopy();
  return <Tabs.Trigger value="optional-feature" {...OPTIONAL_SIDEBAR_STYLES.trigger}><FiCamera />{copy.label}</Tabs.Trigger>;
}

export function OptionalSidebarContent() {
  const copy = useCameraCopy();
  const { stream, startedAt, isStreaming, startCamera, stopCamera } = useCamera();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState('');
  const [countdown, setCountdown] = useState(0);
  useEffect(() => { if (videoRef.current) videoRef.current.srcObject = stream; }, [stream]);
  useEffect(() => {
    if (!stream || startedAt === null) {
      setCountdown(0);
      return;
    }

    const durationMs = 8000;
    const updateCountdown = () => {
      const remaining = Math.max(0, Math.ceil((durationMs - (Date.now() - startedAt)) / 1000));
      setCountdown(remaining);
      return remaining;
    };

    if (updateCountdown() === 0) return;
    const timer = window.setInterval(() => {
      if (updateCountdown() === 0) window.clearInterval(timer);
    }, 200);

    return () => window.clearInterval(timer);
  }, [stream, startedAt]);
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
          <Box height="240px" position="relative" display="flex" alignItems="center" justifyContent="center" overflow="hidden" cursor="pointer" onClick={toggle} bg="blackAlpha.400" borderRadius="8px">
            {error ? <Text color="red.300" px={4}>{error}</Text> : (
              isStreaming
                ? <video ref={videoRef} autoPlay playsInline muted style={{ width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' }} />
                : <Box textAlign="center"><FiCamera size={24} /><Text>{copy.control}</Text></Box>
            )}
            {isStreaming && countdown > 0 && (
              <Box
                position="absolute"
                inset={0}
                bg="rgba(0, 0, 0, 0.3)"
                display="flex"
                flexDirection="column"
                alignItems="center"
                justifyContent="center"
                color="white"
                pointerEvents="none"
              >
                <Text fontSize="56px" lineHeight="1" fontWeight="semibold" fontVariantNumeric="tabular-nums" textShadow="0 2px 8px rgba(0, 0, 0, 0.5)">
                  {countdown}
                </Text>
                <Text mt={3} fontSize="md" fontWeight="medium" textShadow="0 1px 5px rgba(0, 0, 0, 0.65)">
                  {copy.relaxPrompt}
                </Text>
              </Box>
            )}
          </Box>
        </Tooltip>
      </Box>
    </Tabs.Content>
  );
}

function ScreenPanel() {
  const copy = useFeatureCopy();
  const state = useContext(ScreenCaptureContext);
  const videoRef = useRef<HTMLVideoElement>(null);
  if (!state) throw new Error('Screen capture must be inside OptionalFeatureProvider');

  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = state.stream;
  }, [state.stream]);

  const toggle = () => {
    if (state.isStreaming) state.stopCapture();
    else void state.startCapture();
  };

  return (
    <Tabs.Content value="screen">
      <Box {...OPTIONAL_SIDEBAR_STYLES.panel}>
        <Tooltip showArrow content={state.isStreaming ? copy.screenStopping : copy.screenControl}>
          <Box {...OPTIONAL_SIDEBAR_STYLES.preview} cursor="pointer" onClick={toggle}>
            {state.error && !state.stream ? <Text color="red.300" px={4}>{state.error}</Text> : (
              state.stream
                ? <video ref={videoRef} autoPlay playsInline muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                : <Box textAlign="center"><FiMonitor size={24} /><Text>{copy.screenControl}</Text></Box>
            )}
          </Box>
        </Tooltip>
      </Box>
    </Tabs.Content>
  );
}

function BrowserPanel() {
  const copy = useFeatureCopy();
  const browserView = useContext(BrowserViewContext);
  return (
    <Tabs.Content value="browser">
      <Box {...OPTIONAL_SIDEBAR_STYLES.panel}>
        <Box {...OPTIONAL_SIDEBAR_STYLES.preview}>
          {browserView ? (
            <iframe
              src={browserView.debuggerFullscreenUrl}
              title={copy.browserSession}
              style={{ width: '100%', height: '100%', border: 'none', borderRadius: '8px' }}
            />
          ) : (
            <Box textAlign="center"><FiGlobe size={24} /><Text>{copy.noBrowserSession}</Text></Box>
          )}
        </Box>
      </Box>
    </Tabs.Content>
  );
}

export function OptionalSidebarArea(): JSX.Element | null {
  const available = useOptionalFeatureAvailability();
  const copy = useFeatureCopy();
  const [value, setValue] = useState('optional-feature');
  if (!available) return null;
  return (
    <Tabs.Root
      value={value}
      onValueChange={(details) => setValue(details.value)}
      variant="plain"
      {...OPTIONAL_SIDEBAR_STYLES.root}
    >
      <Tabs.List {...OPTIONAL_SIDEBAR_STYLES.list}>
        <OptionalSidebarTrigger />
        <Tabs.Trigger value="screen" {...OPTIONAL_SIDEBAR_STYLES.trigger}>
          <FiMonitor />{copy.screen}
        </Tabs.Trigger>
        <Tabs.Trigger value="browser" {...OPTIONAL_SIDEBAR_STYLES.trigger}>
          <FiGlobe />{copy.browser}
        </Tabs.Trigger>
      </Tabs.List>
      <OptionalSidebarContent />
      <ScreenPanel />
      <BrowserPanel />
    </Tabs.Root>
  );
}

export function useOptionalMediaCapture() {
  const copy = useFeatureCopy();
  const screen = useContext(ScreenCaptureContext);
  if (!screen) throw new Error('Media capture must be inside OptionalFeatureProvider');

  const captureAllMedia = useCallback(async () => {
    if (!screen.stream) return [];
    const videoTrack = screen.stream.getVideoTracks()[0];
    if (!videoTrack) return [];
    try {
      const bitmap = await new ImageCapture(videoTrack).grabFrame();
      const canvas = document.createElement('canvas');
      let { width, height } = bitmap;
      const maxWidth = getStoredImageMaxWidth();
      if (maxWidth > 0 && width > maxWidth) {
        height = (maxWidth / width) * height;
        width = maxWidth;
      }
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) return [];
      context.drawImage(bitmap, 0, 0, width, height);
      return [{
        source: 'screen' as const,
        data: canvas.toDataURL('image/jpeg', getStoredImageCompressionQuality()),
        mime_type: 'image/jpeg',
      }];
    } catch (error) {
      toaster.create({ title: `${copy.screenFailed}: ${error}`, type: 'error', duration: 2000 });
      return [];
    }
  }, [copy, screen.stream]);

  return { captureAllMedia };
}

function useHeartRateShortcut(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return undefined;
    const isTypingTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) return false;
      if (target.isContentEditable || target.closest('[contenteditable="true"], [role="textbox"]')) return true;
      if (target instanceof HTMLTextAreaElement) return true;
      if (!(target instanceof HTMLInputElement)) return false;
      return ['email', 'number', 'password', 'search', 'tel', 'text', 'url'].includes(target.type);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.code !== 'KeyJ' || event.key.toLowerCase() !== 'j') return;
      if (event.isComposing || isTypingTarget(event.target)) return;
      if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
      optionalFeature.requestHeartRateForNextMessage();
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
      optionalFeature.clearHeartRateRequest();
    };
  }, [enabled]);
}

export function OptionalFeatureRuntime(): JSX.Element | null {
  const copy = useFeatureCopy();
  const { features } = useAccount();
  const { available, isStreaming, startCamera } = useCamera();
  const enabled = features.csMode === true;
  const shownThisSession = useRef(false);
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  useHeartRateShortcut(enabled);

  useEffect(() => {
    if (shownThisSession.current || !enabled || !available || isStreaming) return;
    shownThisSession.current = true;
    setOpen(true);
  }, [available, enabled, isStreaming]);

  if (!enabled) return null;
  const handleConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await startCamera();
    } catch (error) {
      console.warn('[OptionalFeature] 启动摄像头失败:', error);
    } finally {
      setSubmitting(false);
      setOpen(false);
    }
  };
  return (
    <DialogRoot open={open} onOpenChange={(details) => {
      if (!details.open && !submitting) setOpen(false);
    }} role="alertdialog">
      <DialogContent>
        <DialogHeader><DialogTitle>{copy.inviteTitle}</DialogTitle></DialogHeader>
        <DialogBody><DialogDescription>{copy.inviteDescription}</DialogDescription></DialogBody>
        <DialogFooter gap={3}>
          <Button variant="ghost" onClick={() => setOpen(false)} disabled={submitting}>
            {copy.inviteCancel}
          </Button>
          <Button colorPalette="blue" onClick={() => void handleConfirm()} loading={submitting}>
            {copy.inviteConfirm}
          </Button>
        </DialogFooter>
        <DialogCloseTrigger />
      </DialogContent>
    </DialogRoot>
  );
}

const CONVERSATION_STARTERS = [
  { topic: 'english', label: '我想练英语', borderColor: 'blue.400' },
  { topic: 'psychology', label: '我想学心理学', borderColor: 'blue.400' },
  { topic: 'story', label: '给我讲个故事', borderColor: 'blue.400' },
  { topic: 'school', label: '我想聊学校', borderColor: 'purple.400' },
  { topic: 'relationships', label: '我想聊关系', borderColor: 'purple.400' },
  { topic: 'work', label: '我想聊工作', borderColor: 'purple.400' },
] as const;

const PROFILER_CONF_UID = 'profile_analyst_001';

export function OptionalChatHistoryExtras(): JSX.Element | null {
  const { features } = useAccount();
  const { messages, currentHistoryUid } = useChatHistory();
  const { confUid } = useConfig();
  const { sendTextMessage } = useSendTextMessage();
  const [pending, setPending] = useState(false);
  const show = (
    features.conversationStarters === true
    && Boolean(currentHistoryUid)
    && messages.some((message) => message.role === 'ai')
    && !messages.some((message) => message.role === 'human')
  );
  const sendStarter = async (topic: string, label: string) => {
    if (pending) return;
    setPending(true);
    try {
      await sendTextMessage(label, { optionalPayload: { quick_start_topic: topic } });
    } finally {
      setPending(false);
    }
  };
  const sendProfilerStart = async () => {
    if (pending) return;
    setPending(true);
    try {
      await sendTextMessage('开始侧写');
    } finally {
      setPending(false);
    }
  };

  if (!show) return null;

  if (confUid === PROFILER_CONF_UID) {
    return (
      <Flex px={3} py={3} justify="flex-start">
        <Button
          size="sm"
          variant="outline"
          color="white"
          borderColor="purple.400"
          _hover={{ bg: 'whiteAlpha.200', color: 'white' }}
          disabled={pending}
          onClick={() => void sendProfilerStart()}
        >
          开始侧写
        </Button>
      </Flex>
    );
  }

  return (
    <>
      {[CONVERSATION_STARTERS.slice(0, 3), CONVERSATION_STARTERS.slice(3)].map((row, index) => (
        <Flex key={index === 0 ? 'starter-row-one' : 'starter-row-two'} wrap="wrap" gap={2} px={3} py={index === 0 ? 3 : 1} justify="flex-start">
          {row.map(({ topic, label, borderColor }) => (
            <Button
              key={topic}
              size="sm"
              variant="outline"
              color="white"
              borderColor={borderColor}
              _hover={{ bg: 'whiteAlpha.200', color: 'white' }}
              disabled={pending}
              onClick={() => void sendStarter(topic, label)}
            >
              {label}
            </Button>
          ))}
        </Flex>
      ))}
    </>
  );
}

export function OptionalGeneralSettings({ onSave, onCancel }: OptionalSettingsCallbacks): JSX.Element | null {
  const copy = useFeatureCopy();
  const { features } = useAccount();
  const [value, setValue] = useState(() => optionalFeature.getEmotionSegmentMinMs());
  const valueRef = useRef(value);
  const savedRef = useRef(value);
  valueRef.current = value;

  useEffect(() => {
    if (!onSave || !onCancel) return undefined;
    const removeSave = onSave(() => { savedRef.current = valueRef.current; });
    const removeCancel = onCancel(() => {
      setValue(savedRef.current);
      optionalFeature.setEmotionSegmentMinMs(savedRef.current);
    });
    return () => {
      removeSave?.();
      removeCancel?.();
    };
  }, [onCancel, onSave]);

  if (features.csMode !== true) return null;
  return (
    <NumberField
      label={copy.emotionSegment}
      value={value}
      onChange={(nextValue) => {
        const ms = Number.parseInt(nextValue, 10);
        if (!Number.isNaN(ms) && ms >= 0 && ms <= 10000) {
          setValue(ms);
          optionalFeature.setEmotionSegmentMinMs(ms);
        }
      }}
      min={0}
      max={10000}
      step={100}
      allowMouseWheel
      help={copy.emotionSegmentHelp}
    />
  );
}

// ===== 一眸：设置抽屉里的个人表情基线面板 =====

interface YimouSettingsProps {
  onSave?: (callback: () => void) => () => void;
  onCancel?: (callback: () => void) => () => void;
}

function YimouSlider({
  label, value, onChange,
}: { label: string; value: number; onChange: (value: number) => void }): JSX.Element {
  return (
    <Box>
      <HStack justify="space-between" mb={3}>
        <Text color="whiteAlpha.800">{label}</Text>
        <Text color="blue.200" fontVariantNumeric="tabular-nums">{value.toFixed(2)}</Text>
      </HStack>
      <Slider
        min={0}
        max={1}
        step={0.01}
        value={[value]}
        colorPalette="blue"
        onValueChange={(details) => onChange(details.value[0])}
      />
    </Box>
  );
}

function YimouSettings({ onSave, onCancel }: YimouSettingsProps): JSX.Element {
  const copy = useYimouCopy();
  const { isStreaming, startCamera } = useCamera();
  const [profileState, setProfileState] = useState<RuntimeProfileState>(() => (
    runtime?.getProfileState() || {
      activeName: '',
      lastUsedName: '',
      profileNames: [],
      calibrationActive: false,
      settings: DEFAULT_EMOTION_SETTINGS,
    }
  ));
  const [draft, setDraft] = useState<EmotionSettings>(
    () => runtime?.getSettings() || DEFAULT_EMOTION_SETTINGS,
  );
  const [nameDialogOpen, setNameDialogOpen] = useState(false);
  const [nameValue, setNameValue] = useState('');
  const [starting, setStarting] = useState(false);
  const [calibration, setCalibration] = useState<CalibrationStepProgress | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    const unsubscribe = runtime?.subscribeProfileState((state) => {
      setProfileState(state);
      // 档案切换（含录制完成）时同步草稿参数。
      setDraft(state.settings);
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    const unsubscribe = runtime?.subscribeCalibration((progress) => {
      if (progress.state === 'step') {
        setCalibration(progress);
      } else if (progress.state === 'done') {
        setCalibration(null);
        toaster.create({
          title: copy.calibDone,
          description: progress.displayName || '',
          type: 'success',
          duration: 3000,
        });
      } else if (progress.state === 'cancelled') {
        setCalibration(null);
      }
    });
    return unsubscribe;
  }, [copy]);

  // 设置抽屉的保存/取消：保存应用 5 项参数，取消回滚草稿。
  useEffect(() => {
    const save = (): void => {
      runtime?.applySettings(draftRef.current);
    };
    const cancel = (): void => {
      setDraft(runtime?.getSettings() || DEFAULT_EMOTION_SETTINGS);
    };
    const unsave = onSave?.(save);
    const uncancel = onCancel?.(cancel);
    return () => {
      unsave?.();
      uncancel?.();
    };
  }, [onSave, onCancel]);

  const updateDraft = (key: keyof EmotionSettings, value: number) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  // 新名字录制前确保摄像头已开启。
  const ensureCamera = async (): Promise<boolean> => {
    if (isStreaming) return true;
    setStarting(true);
    try {
      await startCamera();
      return true;
    } catch (_error) {
      return false; // startCamera 已提示失败原因。
    } finally {
      setStarting(false);
    }
  };

  const submitName = async () => {
    const name = nameValue.trim();
    if (!name) return;
    setNameDialogOpen(false);
    setNameValue('');
    // 已有档案（按规范化名称匹配）则直接启用；否则进入录制流程。
    const activate = runtime?.activateProfileByName(name);
    if (activate?.ok) {
      toaster.create({
        title: copy.profileLoaded,
        description: name,
        type: 'success',
        duration: 3000,
      });
      return;
    }
    const cameraReady = await ensureCamera();
    if (!cameraReady) return;
    const result = runtime?.beginPersonalCalibration(name);
    if (!result?.ok && result?.error) {
      toaster.create({
        title: copy.calibStartFailed,
        description: result.error,
        type: 'error',
        duration: 5000,
      });
    }
  };

  const recalibrate = async () => {
    const name = profileState.activeName;
    if (!name) return;
    const cameraReady = await ensureCamera();
    if (!cameraReady) return;
    runtime?.beginPersonalCalibration(name);
  };

  const exitPersonal = () => {
    runtime?.useGenericProfile();
    setDraft(runtime?.getSettings() || DEFAULT_EMOTION_SETTINGS);
    toaster.create({ title: copy.exited, type: 'info', duration: 2500 });
  };

  const deleteProfile = () => {
    if (!profileState.activeName) return;
    if (!window.confirm(copy.deleteConfirm)) return;
    const result = runtime?.deleteActiveProfile();
    if (result?.ok) {
      setDraft(runtime?.getSettings() || DEFAULT_EMOTION_SETTINGS);
      toaster.create({ title: copy.deleted, type: 'info', duration: 2500 });
    }
  };

  const progressPercent = calibration
    ? Math.round(((calibration.stepIndex + (calibration.phase === 'capturing' ? 1 : 0))
      / calibration.total) * 100)
    : 0;

  return (
    <Stack {...settingStyles.common.container} gap={8}>
      <Box>
        <Text fontWeight="bold" mb={2}>{copy.baselineSection}</Text>
        <Text fontSize="sm" color="whiteAlpha.700" mb={4}>
          {profileState.activeName
            ? `${copy.activeProfilePrefix}：${profileState.activeName}`
            : copy.noProfile}
          {!profileState.activeName && profileState.lastUsedName
            ? `（${copy.lastUsedPrefix}：${profileState.lastUsedName}）`
            : ''}
        </Text>
        <HStack gap={2} flexWrap="wrap">
          <Button
            colorPalette="blue"
            size="sm"
            loading={starting}
            onClick={() => setNameDialogOpen(true)}
          >
            {copy.baselineButton}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!profileState.activeName || starting}
            onClick={recalibrate}
          >
            {copy.recalibrate}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!profileState.activeName}
            onClick={exitPersonal}
          >
            {copy.exitPersonal}
          </Button>
          <Button
            variant="outline"
            size="sm"
            colorPalette="red"
            disabled={!profileState.activeName}
            onClick={deleteProfile}
          >
            {copy.deleteProfile}
          </Button>
        </HStack>
      </Box>

      <Box>
        <Text fontWeight="bold" mb={4}>{copy.settingsSection}</Text>
        <Stack gap={6}>
          <YimouSlider
            label={copy.minimumSignalNorm}
            value={draft.minimumSignalNorm}
            onChange={(value) => updateDraft('minimumSignalNorm', value)}
          />
          <YimouSlider
            label={copy.thresholdSadness}
            value={draft.emotionThresholdSadness}
            onChange={(value) => updateDraft('emotionThresholdSadness', value)}
          />
          <YimouSlider
            label={copy.thresholdAnger}
            value={draft.emotionThresholdAnger}
            onChange={(value) => updateDraft('emotionThresholdAnger', value)}
          />
          <YimouSlider
            label={copy.thresholdSurprise}
            value={draft.emotionThresholdSurprise}
            onChange={(value) => updateDraft('emotionThresholdSurprise', value)}
          />
          <YimouSlider
            label={copy.thresholdHappiness}
            value={draft.emotionThresholdHappiness}
            onChange={(value) => updateDraft('emotionThresholdHappiness', value)}
          />
        </Stack>
      </Box>

      <DialogRoot
        open={nameDialogOpen}
        onOpenChange={(details) => {
          if (!details.open) setNameDialogOpen(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{copy.nameDialogTitle}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <DialogDescription>{copy.nameDialogDesc}</DialogDescription>
            <Input
              mt={3}
              autoFocus
              placeholder={copy.namePlaceholder}
              value={nameValue}
              onChange={(event) => setNameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submitName();
              }}
            />
          </DialogBody>
          <DialogFooter gap={3}>
            <Button variant="ghost" onClick={() => setNameDialogOpen(false)}>
              {copy.cancel}
            </Button>
            <Button colorPalette="blue" disabled={!nameValue.trim()} onClick={() => void submitName()}>
              {copy.startRecord}
            </Button>
          </DialogFooter>
          <DialogCloseTrigger />
        </DialogContent>
      </DialogRoot>

      {calibration && (
        <DialogRoot
          open
          onOpenChange={(details) => {
            if (!details.open) runtime?.cancelCalibration();
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {copy.calibratingTitle}
                {calibration.displayName ? ` · ${calibration.displayName}` : ''}
              </DialogTitle>
            </DialogHeader>
            <DialogBody>
              <Box
                height="6px"
                width="100%"
                bg="whiteAlpha.200"
                borderRadius="full"
                overflow="hidden"
                mb={4}
              >
                <Box height="100%" bg="blue.400" width={`${progressPercent}%`} />
              </Box>
              <Text fontSize="sm" color="whiteAlpha.700" mb={1}>
                {copy.stepLabel} {calibration.stepIndex + 1}/{calibration.total}
              </Text>
              <Text fontSize="xl" mb={2}>
                {calibration.label} — {calibration.prompt}
              </Text>
              {calibration.message && (
                <Text fontSize="sm" color={calibration.phase === 'ready' ? 'orange.300' : 'blue.200'}>
                  {calibration.message}
                </Text>
              )}
            </DialogBody>
            <DialogFooter gap={3}>
              <Button variant="ghost" onClick={() => runtime?.cancelCalibration()}>
                {copy.calibCancel}
              </Button>
              <Button
                colorPalette="blue"
                loading={calibration.phase === 'capturing'}
                disabled={calibration.phase !== 'ready'}
                onClick={() => runtime?.captureCalibrationStep()}
              >
                {calibration.phase === 'capturing' ? copy.capturing : copy.capture}
              </Button>
            </DialogFooter>
            <DialogCloseTrigger />
          </DialogContent>
        </DialogRoot>
      )}
    </Stack>
  );
}

export function OptionalSettingsTrigger(): JSX.Element {
  const copy = useYimouCopy();
  return (
    <Tabs.Trigger value="optional-emotion-settings" {...settingStyles.settingUI.tabs.trigger}>
      {copy.tabLabel}
    </Tabs.Trigger>
  );
}

export function OptionalSettingsContent({ onSave, onCancel }: YimouSettingsProps): JSX.Element {
  return (
    <Tabs.Content value="optional-emotion-settings" {...settingStyles.settingUI.tabs.content}>
      <YimouSettings onSave={onSave} onCancel={onCancel} />
    </Tabs.Content>
  );
}
