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
  HStack,
  Input,
  Stack,
  Tabs,
  Text,
} from '@chakra-ui/react';
import { FiCamera } from 'react-icons/fi';
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
import { sidebarStyles } from '@/components/sidebar/sidebar-styles';
import { settingStyles } from '@/components/sidebar/setting/setting-styles';
import { getCurrentBaseUrl } from '@/constants/connection-settings';

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

interface EmotionAggregate {
  emotions: string[];
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
