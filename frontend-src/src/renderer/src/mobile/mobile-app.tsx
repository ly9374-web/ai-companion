import {
  useCallback, useEffect, useRef, useState,
} from 'react';
import { MicVAD } from '@ricky0123/vad-web';
import { wsService, type MessageEvent, type Message } from '@/services/websocket-service';
import type { HistoryInfo } from '@/context/websocket-context';
import {
  getAccountSessionToken,
  getLastAccountName,
  isAccountSessionActive,
  rememberAuthenticatedAccount,
  rememberLoggedOut,
} from '@/constants/account-settings';
import {
  QWEN_TTS_INSTRUCTION_PRESETS,
  getTtsInstruction,
  isValidQwenTtsVoice,
} from '@/constants/qwen-tts-voices';
import { formatBrowserTime } from '@/utils/browser-time';

type Phase = 'loading' | 'login' | 'connecting' | 'ready';
type ChatMode = 'text' | 'call';
type ChatImage = { id?: string; url?: string };
type ChatLine = { id: number; role: 'human' | 'ai'; text: string; images?: ChatImage[] };
type LogCategory = 'input' | 'short' | 'long' | 'score' | 'rag';
type PromptLog = { id: string; created_at: string; prompt: string };
type ScreenWakeLock = {
  release: () => Promise<void>;
  addEventListener: (type: 'release', callback: () => void) => void;
};
type ScreenWakeLockNavigator = Navigator & {
  wakeLock?: { request: (type: 'screen') => Promise<ScreenWakeLock> };
};
const ALL_LOG_CATEGORIES: LogCategory[] = ['input', 'short', 'long', 'score', 'rag'];
type LastState = {
  role_file: string | null;
  voice: string | null;
  instruction_preset: string | null;
} | null;
type ExpressionConfig = {
  default_emotion?: string;
  transition?: { enabled?: boolean; duration_ms?: number; easing?: string };
  emotions?: Record<string, string>;
};
type ExpressionManifest = { available?: boolean; config?: ExpressionConfig };

const apiUrl = (path: string): URL => new URL(path, window.location.origin);
const wsUrl = (): string => {
  const url = apiUrl('/client-ws');
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('client', 'mobile');
  return url.toString();
};

// VAD resamples everything to 16 kHz. A resumed sentence is only merged with
// the previous one while the last submission is still waiting for her voice.
const VAD_SAMPLE_RATE = 16000;
const MERGE_WINDOW_MS = 8000;
const MAX_UTTERANCE_SAMPLES = VAD_SAMPLE_RATE * 20;
const AUDIO_CHUNK_SAMPLES = 4096;
const PRE_SPEECH_FRAMES = 20;

const sendUtterance = (audio: Float32Array) => {
  for (let offset = 0; offset < audio.length; offset += AUDIO_CHUNK_SAMPLES) {
    wsService.sendMessage({
      type: 'mic-audio-data',
      audio: Array.from(audio.slice(offset, offset + AUDIO_CHUNK_SAMPLES)),
    });
  }
  wsService.sendMessage({ type: 'mic-audio-end', browser_time: formatBrowserTime() });
};

const readLastState = (value: unknown): LastState => {
  if (!value || typeof value !== 'object') return null;
  const state = value as Record<string, unknown>;
  return {
    role_file: typeof state.role_file === 'string' ? state.role_file : null,
    voice: typeof state.voice === 'string' ? state.voice : null,
    instruction_preset: typeof state.instruction_preset === 'string'
      ? state.instruction_preset : null,
  };
};

const visibleHistory = (
  history: Message[], generatedImages: { id: string; created_at: number }[] = [],
): ChatLine[] => [
  ...history.filter((item) => (item.role === 'human' || item.role === 'ai')
    && typeof item.content === 'string' && item.content.length > 0)
    .map((item) => ({ role: item.role, text: item.content,
      time: item.sort_time ?? Date.parse(item.timestamp) / 1000 })),
  ...generatedImages.map((item) => ({
    role: 'ai' as const, text: '', images: [{ id: item.id }], time: item.created_at,
  })),
].sort((a, b) => a.time - b.time)
  .map((item, index) => ({ id: index + 1, role: item.role, text: item.text,
    images: 'images' in item ? item.images : undefined }));

function GeneratedImage({ image, session, onOpen }: {
  image: ChatImage;
  session: { account: string; token: string } | null;
  onOpen: (src: string) => void;
}) {
  const [src, setSrc] = useState<string | null>(image.url || null);
  const lastTapRef = useRef(0);

  useEffect(() => {
    if (!image.id || !session) return undefined;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    const url = apiUrl(`/api/generated-images/${image.id}`);
    url.searchParams.set('account', session.account);
    void fetch(url, {
      headers: { Authorization: `Bearer ${session.token}` },
      cache: 'no-store',
      signal: controller.signal,
    }).then((response) => {
      if (!response.ok) throw new Error('Image unavailable');
      return response.blob();
    }).then((blob) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setSrc(objectUrl);
    }).catch(() => { if (!controller.signal.aborted) setSrc(null); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [image.id, session?.account, session?.token]);

  if (!src) return <span className="message-image-loading">图片加载中或已过期</span>;
  return (
    <button className="message-image-button" type="button" aria-label="双击全屏查看图片"
      onPointerUp={(event) => {
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const now = Date.now();
        if (now - lastTapRef.current < 350) {
          event.preventDefault();
          lastTapRef.current = 0;
          onOpen(src);
        } else lastTapRef.current = now;
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onOpen(src);
      }}>
      <img className="message-image" src={src} alt="生成的图片" loading="lazy" />
    </button>
  );
}

function PhoneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6.6 3.5 4.7 5.4a2 2 0 0 0-.5 2.1c2 5.5 6.8 10.3 12.3 12.3a2 2 0 0 0 2.1-.5l1.9-1.9a1.4 1.4 0 0 0-.4-2.2l-3.3-1.5a1.4 1.4 0 0 0-1.6.3l-1.2 1.2a15.4 15.4 0 0 1-5.2-5.2l1.2-1.2a1.4 1.4 0 0 0 .3-1.6L8.8 3.9a1.4 1.4 0 0 0-2.2-.4Z" />
    </svg>
  );
}

export default function MobileApp() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [accountInput, setAccountInput] = useState(getLastAccountName);
  const [passwordInput, setPasswordInput] = useState('');
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginFailed, setLoginFailed] = useState(false);
  const [mode, setMode] = useState<ChatMode>('text');
  const [callStarting, setCallStarting] = useState(false);
  const [input, setInput] = useState('');
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [histories, setHistories] = useState<HistoryInfo[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activeHistoryUid, setActiveHistoryUid] = useState<string | null>(null);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const previewTapRef = useRef(0);
  const [status, setStatus] = useState('');
  const [minimaxConfigured, setMinimaxConfigured] = useState<boolean | null>(null);
  const [minimaxAvailable, setMinimaxAvailable] = useState<boolean | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [previousImageUrl, setPreviousImageUrl] = useState<string | null>(null);
  const [transition, setTransition] = useState({ duration: 160, easing: 'ease-out' });
  const [darkMode, setDarkMode] = useState(false);
  const [holdProgress, setHoldProgress] = useState(0);
  const [wakeLockError, setWakeLockError] = useState(false);
  const [logEnabled, setLogEnabled] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logCategories, setLogCategories] = useState<LogCategory[]>(ALL_LOG_CATEGORIES);
  const [promptLogs, setPromptLogs] = useState<PromptLog[]>([]);
  const [logExpiresAt, setLogExpiresAt] = useState<number | null>(null);
  const [holdingToTalk, setHoldingToTalk] = useState(false);
  const [needsRecovery, setNeedsRecovery] = useState(false);

  const sessionRef = useRef<{ account: string; token: string } | null>(null);
  const lastStateRef = useRef<LastState>(null);
  const awaitingSwitchRef = useRef(false);
  const selectionAppliedRef = useRef(false);
  const initialHistoryListRef = useRef(true);
  const currentVoiceRef = useRef<string | null>(null);
  const modeRef = useRef<ChatMode>('text');
  const vadRef = useRef<MicVAD | null>(null);
  const callAttemptRef = useRef(0);
  const replyingRef = useRef(false);
  const awaitingReplyRef = useRef(false);
  const ignoreReplyRef = useRef(false);
  const assistantIndexRef = useRef<number | null>(null);
  const assistantTextRef = useRef('');
  const nextLineIdRef = useRef(1);
  const imageMapRef = useRef<Record<string, string>>({});
  const emotionRef = useRef('中性');
  const imageUrlRef = useRef<string | null>(null);
  const imageRequestRef = useRef(0);
  const imageTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transitionRef = useRef({ duration: 160, easing: 'ease-out' });
  const audioEpochRef = useRef(0);
  const audioQueueRef = useRef<Promise<void>>(Promise.resolve());
  const audioContextRef = useRef<AudioContext | null>(null);
  const currentAudioRef = useRef<AudioBufferSourceNode | null>(null);
  const finishAudioRef = useRef<(() => void) | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const authTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disposedRef = useRef(false);
  const messageAreaRef = useRef<HTMLDivElement | null>(null);
  const micMutedRef = useRef(false);
  const replyAudioRef = useRef(false);
  const lastUtteranceRef = useRef<{ audio: Float32Array; sentAt: number } | null>(null);
  const holdFrameRef = useRef<number | null>(null);
  const holdStartRef = useRef(0);
  const talkPointerRef = useRef<number | null>(null);
  const manualRecordingRef = useRef(false);
  const manualFramesRef = useRef<Float32Array[]>([]);
  const recentFramesRef = useRef<Float32Array[]>([]);
  const automaticSpeechRef = useRef(false);
  const automaticFramesRef = useRef<Float32Array[]>([]);
  const logOpenRef = useRef(false);
  const logCategoriesRef = useRef<LogCategory[]>(ALL_LOG_CATEGORIES);
  const lastMicFrameAtRef = useRef(0);
  const micRestartAttemptsRef = useRef(0);
  const replyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const updateViewport = () => {
      const viewport = window.visualViewport;
      const height = viewport?.height ?? window.innerHeight;
      document.documentElement.style.setProperty('--mobile-view-height', `${height}px`);
      document.documentElement.style.setProperty('--mobile-view-top', `${viewport?.offsetTop ?? 0}px`);
      document.documentElement.classList.toggle(
        'mobile-keyboard-open', window.innerHeight - height > 140,
      );
    };
    updateViewport();
    window.visualViewport?.addEventListener('resize', updateViewport);
    window.visualViewport?.addEventListener('scroll', updateViewport);
    window.addEventListener('resize', updateViewport);
    return () => {
      window.visualViewport?.removeEventListener('resize', updateViewport);
      window.visualViewport?.removeEventListener('scroll', updateViewport);
      window.removeEventListener('resize', updateViewport);
      document.documentElement.style.removeProperty('--mobile-view-height');
      document.documentElement.style.removeProperty('--mobile-view-top');
      document.documentElement.classList.remove('mobile-keyboard-open');
    };
  }, []);

  const requestPromptLogs = useCallback((categories: LogCategory[] = logCategoriesRef.current) => {
    if (wsService.getCurrentState() === 'OPEN') {
      wsService.sendMessage({ type: 'fetch-mobile-prompt-logs', categories });
    }
  }, []);

  useEffect(() => {
    if (!darkMode) {
      setWakeLockError(false);
      return undefined;
    }
    let disposed = false;
    let pending = false;
    const lockRef: { current: ScreenWakeLock | null } = { current: null };
    const acquire = async () => {
      if (disposed || pending || lockRef.current || document.visibilityState !== 'visible') return;
      const wakeLock = (navigator as ScreenWakeLockNavigator).wakeLock;
      if (!window.isSecureContext || !wakeLock) {
        setWakeLockError(true);
        return;
      }
      pending = true;
      try {
        const acquired = await wakeLock.request('screen');
        if (disposed || document.visibilityState !== 'visible') {
          await acquired.release();
        } else {
          lockRef.current = acquired;
          acquired.addEventListener('release', () => {
            if (lockRef.current !== acquired) return;
            lockRef.current = null;
            if (!disposed && document.visibilityState === 'visible') setWakeLockError(true);
          });
          setWakeLockError(false);
        }
      } catch (_error) {
        if (!disposed && document.visibilityState === 'visible') setWakeLockError(true);
      } finally {
        pending = false;
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        lockRef.current = null;
        void acquire();
      } else {
        lockRef.current = null;
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    void acquire();
    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      if (lockRef.current) void lockRef.current.release().catch(() => {});
    };
  }, [darkMode]);
  const showImage = useCallback((nextUrl: string | null, animate = true) => {
    if (imageUrlRef.current === nextUrl) return;
    if (imageTimerRef.current) clearTimeout(imageTimerRef.current);
    const previous = animate && transitionRef.current.duration > 0
      ? imageUrlRef.current : null;
    imageUrlRef.current = nextUrl;
    setPreviousImageUrl(previous);
    setImageUrl(nextUrl);
    if (previous) {
      imageTimerRef.current = setTimeout(() => setPreviousImageUrl(null),
        transitionRef.current.duration);
    }
  }, []);

  const applyEmotion = useCallback((emotion: string) => {
    if (!emotion) return;
    emotionRef.current = emotion;
    const url = imageMapRef.current[emotion];
    if (url) showImage(url);
  }, [showImage]);

  const loadExpression = useCallback(async (expressionDir: string | undefined) => {
    const requestId = ++imageRequestRef.current;
    imageMapRef.current = {};
    emotionRef.current = '中性';
    showImage(null, false);
    if (!expressionDir) return;
    const url = apiUrl('/optional-features/expression/manifest');
    url.searchParams.set('dir', expressionDir);
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error('表情贴图暂不可用');
      const manifest = await response.json() as ExpressionManifest;
      if (requestId !== imageRequestRef.current || !manifest.available) return;
      const config = manifest.config || {};
      const imageMap = Object.fromEntries(Object.entries(config.emotions || {})
        .map(([emotion, path]) => {
          const imageUrl = new URL(path, response.url);
          imageUrl.searchParams.set('mobile', '1');
          return [emotion, imageUrl.href];
        }));
      imageMapRef.current = imageMap;
      const declaredTransition = config.transition || {};
      const duration = declaredTransition.enabled === false ? 0
        : typeof declaredTransition.duration_ms === 'number'
          ? Math.max(0, Math.min(1000, declaredTransition.duration_ms)) : 160;
      const easing = ['linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out']
        .includes(declaredTransition.easing || '')
        ? declaredTransition.easing! : 'ease-out';
      transitionRef.current = { duration, easing };
      setTransition({ duration, easing });
      const initial = imageMap[emotionRef.current]
        || imageMap[config.default_emotion || '中性']
        || imageMap['中性'] || Object.values(imageMap)[0] || null;
      showImage(initial, false);
    } catch (_error) {
      if (requestId === imageRequestRef.current) setStatus('表情贴图暂不可用');
    }
  }, [showImage]);

  const stopAudio = useCallback(() => {
    audioEpochRef.current += 1;
    const current = currentAudioRef.current;
    if (current) {
      try { current.stop(); } catch (_error) { /* Already stopped. */ }
    }
    finishAudioRef.current?.();
    currentAudioRef.current = null;
    audioQueueRef.current = Promise.resolve();
  }, []);

  // The microphone stays closed for the whole reply instead of reopening in the
  // gaps between her sentences: pausing the VAD discards whatever the user was
  // already saying, so repeated open/close cycles used to swallow it.
  const muteMic = useCallback(() => {
    if (micMutedRef.current || manualRecordingRef.current) return;
    micMutedRef.current = true;
    recentFramesRef.current = [];
    automaticSpeechRef.current = false;
    automaticFramesRef.current = [];
    vadRef.current?.pause();
  }, []);

  const unmuteMic = useCallback(() => {
    if (!micMutedRef.current) return;
    micMutedRef.current = false;
    if (modeRef.current === 'call') {
      lastMicFrameAtRef.current = performance.now();
      micRestartAttemptsRef.current = 0;
      vadRef.current?.start();
    }
  }, []);

  const HOLD_DURATION_MS = 2000;

  const startHold = useCallback(() => {
    if (holdFrameRef.current !== null) return;
    holdStartRef.current = performance.now();
    setHoldProgress(0);
    const step = () => {
      const elapsed = performance.now() - holdStartRef.current;
      if (elapsed >= HOLD_DURATION_MS) {
        holdFrameRef.current = null;
        setHoldProgress(1);
        setDarkMode((current) => !current);
        return;
      }
      setHoldProgress(elapsed / HOLD_DURATION_MS);
      holdFrameRef.current = requestAnimationFrame(step);
    };
    holdFrameRef.current = requestAnimationFrame(step);
  }, []);

  const endHold = useCallback(() => {
    if (holdFrameRef.current !== null) {
      cancelAnimationFrame(holdFrameRef.current);
      holdFrameRef.current = null;
    }
    setHoldProgress(0);
  }, []);

  const enqueueAudio = useCallback((base64: string) => {
    const epoch = audioEpochRef.current;
    audioQueueRef.current = audioQueueRef.current.then(async () => {
      if (epoch !== audioEpochRef.current || modeRef.current !== 'call') return;
      const context = audioContextRef.current;
      if (!context) return;
      const decoded = atob(base64);
      const bytes = new Uint8Array(decoded.length);
      for (let index = 0; index < decoded.length; index += 1) {
        bytes[index] = decoded.charCodeAt(index);
      }
      const buffer = await context.decodeAudioData(bytes.buffer);
      if (epoch !== audioEpochRef.current || modeRef.current !== 'call') return;
      if (context.state !== 'running') {
        await Promise.race([
          context.resume(),
          new Promise<never>((_resolve, reject) => {
            setTimeout(() => reject(new Error('AudioContext resume timed out')), 8000);
          }),
        ]);
      }
      await new Promise<void>((resolve) => {
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        currentAudioRef.current = source;
        let finished = false;
        const fallback = setTimeout(() => {
          try { source.stop(); } catch (_error) { /* Already stopped. */ }
          finish();
        }, buffer.duration * 1000 + 15000);
        const finish = () => {
          if (finished) return;
          finished = true;
          clearTimeout(fallback);
          source.onended = null;
          source.disconnect();
          if (currentAudioRef.current === source) currentAudioRef.current = null;
          if (finishAudioRef.current === finish) finishAudioRef.current = null;
          resolve();
        };
        finishAudioRef.current = finish;
        source.onended = finish;
        muteMic();
        try { source.start(); } catch (_error) { finish(); }
      });
    }).catch(() => {
      if (epoch === audioEpochRef.current && modeRef.current === 'call') {
        setStatus('语音播放中断，正在恢复通话');
      }
    });
  }, [muteMic]);

  const interruptReply = useCallback(() => {
    if (!replyingRef.current && !awaitingReplyRef.current && !currentAudioRef.current) return;
    const heardResponse = replyingRef.current || currentAudioRef.current
      ? assistantTextRef.current : '';
    ignoreReplyRef.current = true;
    replyingRef.current = false;
    awaitingReplyRef.current = false;
    stopAudio();
    if (wsService.getCurrentState() === 'OPEN') {
      wsService.sendMessage({ type: 'interrupt-signal', text: heardResponse });
    }
  }, [stopAudio]);

  const clearReplyTimer = useCallback(() => {
    if (replyTimerRef.current) clearTimeout(replyTimerRef.current);
    replyTimerRef.current = null;
  }, []);

  const watchReply = useCallback(() => {
    clearReplyTimer();
    replyTimerRef.current = setTimeout(() => {
      replyTimerRef.current = null;
      if (modeRef.current !== 'call'
        || (!awaitingReplyRef.current && !replyingRef.current)) return;
      interruptReply();
      unmuteMic();
      setStatus('等待回复超时，已恢复收音。请再说一次或按住说话');
    }, 90000);
  }, [clearReplyTimer, interruptReply, unmuteMic]);

  const finishManualSpeech = useCallback((send: boolean) => {
    if (!manualRecordingRef.current) return;
    talkPointerRef.current = null;
    setHoldingToTalk(false);
    const frames = manualFramesRef.current;
    manualFramesRef.current = [];
    recentFramesRef.current = [];
    automaticSpeechRef.current = false;
    automaticFramesRef.current = [];

    // Reset VAD's current segment so it cannot submit the held audio again.
    vadRef.current?.pause();
    manualRecordingRef.current = false;
    if (modeRef.current === 'call' && !micMutedRef.current) {
      vadRef.current?.start();
    }

    if (!send || modeRef.current !== 'call'
      || wsService.getCurrentState() !== 'OPEN' || frames.length === 0) return;

    const length = frames.reduce((total, frame) => total + frame.length, 0);
    const audio = new Float32Array(length);
    let offset = 0;
    frames.forEach((frame) => {
      audio.set(frame, offset);
      offset += frame.length;
    });
    lastUtteranceRef.current = null;
    awaitingReplyRef.current = true;
    sendUtterance(audio);
    setStatus('正在识别…');
    watchReply();
  }, [watchReply]);

  const startManualSpeech = useCallback((pointerId: number) => {
    if (modeRef.current !== 'call' || !vadRef.current
      || wsService.getCurrentState() !== 'OPEN' || manualRecordingRef.current) return false;

    talkPointerRef.current = pointerId;
    // Keep the beginning of a sentence when the user presses halfway through it.
    manualFramesRef.current = automaticSpeechRef.current && !micMutedRef.current
      ? [...automaticFramesRef.current] : [];
    automaticSpeechRef.current = false;
    automaticFramesRef.current = [];
    manualRecordingRef.current = true;
    lastUtteranceRef.current = null;
    setHoldingToTalk(true);
    interruptReply();
    // Also invalidate queued audio that is still being decoded.
    stopAudio();
    ignoreReplyRef.current = true;
    unmuteMic();
    return true;
  }, [interruptReply, stopAudio, unmuteMic]);

  const stopCall = useCallback(() => {
    callAttemptRef.current += 1;
    setCallStarting(false);
    modeRef.current = 'text';
    finishManualSpeech(false);
    setMode('text');
    setStatus('');
    setNeedsRecovery(false);
    clearReplyTimer();
    vadRef.current?.pause();
    vadRef.current?.destroy();
    vadRef.current = null;
    micMutedRef.current = false;
    replyAudioRef.current = false;
    lastUtteranceRef.current = null;
    recentFramesRef.current = [];
    automaticSpeechRef.current = false;
    automaticFramesRef.current = [];
    interruptReply();
    stopAudio();
    const audioContext = audioContextRef.current;
    audioContextRef.current = null;
    if (audioContext) void audioContext.close().catch(() => {});
    if (wsService.getCurrentState() === 'OPEN') {
      wsService.sendMessage({ type: 'set-generate-audio', enabled: false });
    }
  }, [clearReplyTimer, finishManualSpeech, interruptReply, stopAudio]);

  const startCall = useCallback(async () => {
    if (modeRef.current === 'call' || wsService.getCurrentState() !== 'OPEN') return;
    const attempt = ++callAttemptRef.current;
    modeRef.current = 'call';
    setMode('call');
    setCallStarting(true);
    setNeedsRecovery(false);
    setStatus('正在开启麦克风…');
    try {
      const playbackContext = new AudioContext();
      audioContextRef.current = playbackContext;
      void playbackContext.resume().catch(() => {
        setNeedsRecovery(true);
        setStatus('语音播放已中断，请点“恢复通话”');
      });
      const silence = playbackContext.createBufferSource();
      silence.buffer = playbackContext.createBuffer(1, 1, playbackContext.sampleRate);
      silence.connect(playbackContext.destination);
      silence.start();
      const assets = new URL('./libs/', window.location.href).href;
      const vad = await MicVAD.new({
        model: 'v5',
        preSpeechPadFrames: PRE_SPEECH_FRAMES,
        submitUserSpeechOnPause: false,
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        // One second of silence ends the sentence: the mic is closed for the
        // whole reply now, so her own voice can no longer be mistaken for it.
        redemptionFrames: Math.round(1 / 0.032),
        baseAssetPath: assets,
        onnxWASMBasePath: assets,
        onFrameProcessed: (_probs, frame: Float32Array) => {
          if (modeRef.current !== 'call' || micMutedRef.current) return;
          lastMicFrameAtRef.current = performance.now();
          if (micRestartAttemptsRef.current) {
            micRestartAttemptsRef.current = 0;
            setNeedsRecovery(false);
            setStatus((current) => current === '正在恢复麦克风…' ? '正在聆听…' : current);
          }
          const copy = frame.slice();
          recentFramesRef.current.push(copy);
          if (recentFramesRef.current.length > PRE_SPEECH_FRAMES) recentFramesRef.current.shift();
          if (manualRecordingRef.current) manualFramesRef.current.push(copy);
          else if (automaticSpeechRef.current) automaticFramesRef.current.push(copy);
        },
        onSpeechStart: () => {
          if (manualRecordingRef.current || micMutedRef.current) return;
          automaticSpeechRef.current = true;
          automaticFramesRef.current = [...recentFramesRef.current];
        },
        onVADMisfire: () => {
          automaticSpeechRef.current = false;
          automaticFramesRef.current = [];
        },
        onSpeechRealStart: () => {
          if (!manualRecordingRef.current) interruptReply();
        },
        onSpeechEnd: (audio: Float32Array) => {
          automaticSpeechRef.current = false;
          automaticFramesRef.current = [];
          if (manualRecordingRef.current) return;
          if (micMutedRef.current) return;
          if (modeRef.current !== 'call' || wsService.getCurrentState() !== 'OPEN') return;
          // If the user pauses mid-sentence, the VAD submits the first half.
          // Speaking again before she starts answering means the sentence was
          // not finished: resend both halves as a single utterance so only one
          // reply is produced. The reply that was already running is cancelled
          // by the interrupt raised when this speech started.
          const previous = lastUtteranceRef.current;
          const mergeable = previous !== null
            && !replyAudioRef.current
            && performance.now() - previous.sentAt < MERGE_WINDOW_MS
            && previous.audio.length + audio.length <= MAX_UTTERANCE_SAMPLES;
          let utterance = audio;
          if (mergeable && previous) {
            utterance = new Float32Array(previous.audio.length + audio.length);
            utterance.set(previous.audio, 0);
            utterance.set(audio, previous.audio.length);
          }
          awaitingReplyRef.current = true;
          sendUtterance(utterance);
          setStatus('正在识别…');
          watchReply();
          lastUtteranceRef.current = { audio: utterance, sentAt: performance.now() };
        },
      });
      if (attempt !== callAttemptRef.current || disposedRef.current
        || wsService.getCurrentState() !== 'OPEN') {
        vad.destroy();
        if (audioContextRef.current === playbackContext) {
          audioContextRef.current = null;
          void playbackContext.close().catch(() => {});
        }
        return;
      }
      vadRef.current = vad;
      wsService.sendMessage({ type: 'set-generate-audio', enabled: true });
      vad.start();
      lastMicFrameAtRef.current = performance.now();
      micRestartAttemptsRef.current = 0;
      setStatus('正在聆听…');
    } catch (_error) {
      if (attempt !== callAttemptRef.current) return;
      vadRef.current?.destroy();
      vadRef.current = null;
      const audioContext = audioContextRef.current;
      audioContextRef.current = null;
      if (audioContext) void audioContext.close().catch(() => {});
      modeRef.current = 'text';
      setMode('text');
      setStatus('无法开启麦克风，请检查浏览器权限');
    } finally {
      if (attempt === callAttemptRef.current) setCallStarting(false);
    }
  }, [interruptReply, watchReply]);

  const recoverCall = useCallback(() => {
    stopCall();
    void startCall();
  }, [startCall, stopCall]);

  useEffect(() => {
    if (mode !== 'call') return undefined;
    const checkMicrophone = () => {
      if (modeRef.current !== 'call' || callStarting || micMutedRef.current
        || document.visibilityState !== 'visible' || !vadRef.current) return;
      if (performance.now() - lastMicFrameAtRef.current < 12000) return;
      if (micRestartAttemptsRef.current === 0) {
        micRestartAttemptsRef.current = 1;
        lastMicFrameAtRef.current = performance.now();
        setStatus('正在恢复麦克风…');
        try {
          vadRef.current.pause();
          vadRef.current.start();
        } catch (_error) {
          setNeedsRecovery(true);
          setStatus('麦克风已中断，请点“恢复通话”');
        }
      } else {
        setNeedsRecovery(true);
        setStatus('麦克风已中断，请点“恢复通话”');
      }
    };
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      const audioContext = audioContextRef.current;
      if (audioContext && audioContext.state !== 'running') {
        void audioContext.resume().catch(() => {
          setStatus('语音播放已中断，请点“恢复通话”');
          setNeedsRecovery(true);
        });
      }
      lastMicFrameAtRef.current = performance.now();
    };
    const timer = window.setInterval(checkMicrophone, 4000);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [callStarting, mode]);

  const finishSelection = useCallback(() => {
    if (selectionAppliedRef.current) return;
    selectionAppliedRef.current = true;
    const saved = lastStateRef.current;
    const preset = saved?.instruction_preset;
    const voice = saved?.voice || currentVoiceRef.current;
    if (isValidQwenTtsVoice(voice)) {
      wsService.sendMessage({
        type: 'set-qwen-tts-options',
        voice,
        instruction: preset && Object.prototype.hasOwnProperty.call(QWEN_TTS_INSTRUCTION_PRESETS, preset)
          ? getTtsInstruction(preset) : '',
        instruction_preset: preset || 'none',
        sync_ai_preferences: true,
      });
    }
    initialHistoryListRef.current = true;
    wsService.sendMessage({ type: 'fetch-history-list' });
  }, []);

  const appendLine = useCallback((role: ChatLine['role'], text: string, images?: ChatImage[]) => {
    if (!text && !images?.length) return;
    const id = nextLineIdRef.current++;
    setLines((current) => [...current, { id, role, text, images }]);
  }, []);

  const appendAssistant = useCallback((text: string) => {
    if (!text) return;
    assistantTextRef.current += text;
    const existingId = assistantIndexRef.current;
    if (existingId === null) {
      const id = nextLineIdRef.current++;
      assistantIndexRef.current = id;
      setLines((current) => [...current, { id, role: 'ai', text }]);
    } else {
      setLines((current) => current.map((line) => line.id === existingId
        ? { ...line, text: line.text + text } : line));
    }
  }, []);

  const handleMessage = useCallback((message: MessageEvent) => {
    switch (message.type) {
      case 'set-model-and-conf': {
        const current = message as MessageEvent & { tts_voice?: string };
        currentVoiceRef.current = current.tts_voice || null;
        void loadExpression(message.expression_dir);
        break;
      }
      case 'config-switched':
        if (awaitingSwitchRef.current) {
          awaitingSwitchRef.current = false;
          finishSelection();
        }
        break;
      case 'history-list':
        setHistories(message.histories || []);
        if (initialHistoryListRef.current) {
          initialHistoryListRef.current = false;
          if (message.histories?.length) {
            wsService.sendMessage({
              type: 'fetch-and-set-history',
              history_uid: message.histories[0].uid,
            });
          } else {
            wsService.sendMessage({ type: 'create-new-history' });
          }
        }
        break;
      case 'history-data':
      case 'new-history-created': {
        const history = visibleHistory(message.messages || [], message.generated_images || []);
        nextLineIdRef.current = history.length + 1;
        assistantIndexRef.current = null;
        setLines(history);
        setActiveHistoryUid(message.history_uid || null);
        setHistoryOpen(false);
        setPhase('ready');
        setStatus('');
        if (message.type === 'new-history-created') {
          wsService.sendMessage({ type: 'fetch-history-list' });
        }
        if (logOpenRef.current) requestPromptLogs();
        break;
      }
      case 'mobile-prompt-logging-updated':
        setLogEnabled(Boolean(message.enabled));
        if (!message.enabled) {
          logOpenRef.current = false;
          setLogOpen(false);
        }
        break;
      case 'mobile-prompt-logs':
        setPromptLogs(message.entries || []);
        setLogExpiresAt(typeof message.expires_at === 'number' ? message.expires_at : null);
        break;
      case 'minimax-key-status':
        setMinimaxConfigured(Boolean(message.configured));
        setMinimaxAvailable(Boolean(message.available));
        break;
      case 'tool_call_status':
        // Only finished image results are shown; the tool's own content is the
        // instruction sent to the model, not something to display.
        if (message.status === 'completed' && message.tool_name === 'text_to_image') {
          const images = (message.media_urls || []).map((url, index) =>
            message.image_ids?.[index] ? { id: message.image_ids[index] } : { url });
          if (images.length) appendLine('ai', '', images);
          if (images.some((image) => 'url' in image)) {
            setStatus('部分图片只能本次查看，未能保留 24 小时');
          }
        }
        break;
      case 'control':
        if (message.text === 'conversation-chain-start') {
          // A cancelled reply can still have messages in flight when pressing.
          if (manualRecordingRef.current
            || (ignoreReplyRef.current && !awaitingReplyRef.current)) {
            ignoreReplyRef.current = true;
            break;
          }
          awaitingReplyRef.current = false;
          replyingRef.current = true;
          if (modeRef.current === 'call') {
            setStatus('正在回复…');
            watchReply();
          }
          replyAudioRef.current = false;
          ignoreReplyRef.current = false;
          assistantIndexRef.current = null;
          assistantTextRef.current = '';
        } else if (message.text === 'conversation-chain-end') {
          clearReplyTimer();
          awaitingReplyRef.current = false;
          replyingRef.current = false;
          unmuteMic();
          if (modeRef.current === 'call') setStatus('正在聆听…');
          if (logOpenRef.current) requestPromptLogs();
        }
        break;
      case 'audio':
        if (!ignoreReplyRef.current && !manualRecordingRef.current) {
          if (modeRef.current === 'call') {
            setStatus('正在回复…');
            watchReply();
          }
          appendAssistant(message.display_text?.text || '');
          if (message.emotion) applyEmotion(message.emotion);
          if (modeRef.current === 'call' && message.audio) {
            replyAudioRef.current = true;
            enqueueAudio(message.audio);
          }
        }
        break;
      case 'expression-update':
        if (message.emotion) applyEmotion(message.emotion);
        break;
      case 'user-input-transcription':
        if (message.text) appendLine('human', message.text);
        if (modeRef.current === 'call') setStatus('正在回复…');
        break;
      case 'force-new-message':
        assistantIndexRef.current = null;
        break;
      case 'backend-synth-complete': {
        clearReplyTimer();
        const pending = audioQueueRef.current;
        const requestId = message.request_id;
        void pending.then(() => {
          if (requestId && wsService.getCurrentState() === 'OPEN') {
            wsService.sendMessage({
              type: 'frontend-playback-complete', request_id: requestId,
            });
          }
        });
        break;
      }
      case 'error':
        clearReplyTimer();
        awaitingReplyRef.current = false;
        replyingRef.current = false;
        if (awaitingSwitchRef.current) {
          awaitingSwitchRef.current = false;
          finishSelection();
        }
        // A failed turn never sends chain-end, so reopen the mic here.
        unmuteMic();
        setStatus(message.message || '连接出现问题');
        break;
      case 'api-key-required':
        clearReplyTimer();
        awaitingReplyRef.current = false;
        replyingRef.current = false;
        unmuteMic();
        setStatus('服务器尚未配置聊天密钥，请稍后再试');
        break;
      default:
        break;
    }
  }, [appendAssistant, appendLine, applyEmotion, clearReplyTimer, enqueueAudio,
    finishSelection, loadExpression, requestPromptLogs, unmuteMic, watchReply]);

  useEffect(() => {
    const cancelHeldSpeech = () => finishManualSpeech(false);
    const cancelWhenHidden = () => {
      if (document.visibilityState === 'hidden') cancelHeldSpeech();
    };
    window.addEventListener('blur', cancelHeldSpeech);
    document.addEventListener('visibilitychange', cancelWhenHidden);
    return () => {
      window.removeEventListener('blur', cancelHeldSpeech);
      document.removeEventListener('visibilitychange', cancelWhenHidden);
    };
  }, [finishManualSpeech]);

  const handleState = useCallback((state: string) => {
    if (disposedRef.current) return;
    if (state === 'CONNECTING' || state === 'OPEN') {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (state === 'OPEN') {
      setLogEnabled(false);
      selectionAppliedRef.current = false;
      awaitingSwitchRef.current = Boolean(lastStateRef.current?.role_file);
      wsService.sendMessage({ type: 'set-generate-audio', enabled: false });
      if (awaitingSwitchRef.current) {
        wsService.sendMessage({
          type: 'switch-config',
          file: lastStateRef.current?.role_file,
        });
      } else {
        finishSelection();
      }
    } else if (state === 'CLOSED' && sessionRef.current) {
      setLogEnabled(false);
      logOpenRef.current = false;
      setLogOpen(false);
      stopCall();
      setPhase('connecting');
      if (!reconnectTimerRef.current) {
        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null;
          if (!disposedRef.current && sessionRef.current) wsService.connect(wsUrl(), false);
        }, 2000);
      }
    }
  }, [finishSelection, stopCall]);

  const loadSession = useCallback(async (account: string, token: string) => {
    const url = apiUrl('/api/last-state');
    url.searchParams.set('account', account);
    url.searchParams.set('session', token);
    try {
      const response = await fetch(url, { cache: 'no-store', referrerPolicy: 'no-referrer' });
      if (disposedRef.current) return;
      if (response.status === 401) {
        rememberLoggedOut();
        sessionRef.current = null;
        setPhase('login');
        return;
      }
      if (!response.ok) throw new Error('Unable to load account state');
      lastStateRef.current = readLastState(await response.json());
      sessionRef.current = { account, token };
      setPhase('connecting');
      wsService.setAccount(account, token);
      wsService.connect(wsUrl(), false);
    } catch (_error) {
      if (disposedRef.current) return;
      setPhase('loading');
      if (authTimerRef.current) clearTimeout(authTimerRef.current);
      authTimerRef.current = setTimeout(() => {
        authTimerRef.current = null;
        void loadSession(account, token);
      }, 3000);
    }
  }, []);

  useEffect(() => {
    disposedRef.current = false;
    const messages = wsService.onMessage(handleMessage);
    const states = wsService.onStateChange(handleState);
    const account = getLastAccountName();
    const token = getAccountSessionToken();
    if (isAccountSessionActive() && token) {
      void loadSession(account, token);
    } else {
      setPhase('login');
    }
    return () => {
      disposedRef.current = true;
      messages.unsubscribe();
      states.unsubscribe();
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (authTimerRef.current) clearTimeout(authTimerRef.current);
      if (imageTimerRef.current) clearTimeout(imageTimerRef.current);
      if (holdFrameRef.current !== null) cancelAnimationFrame(holdFrameRef.current);
      manualRecordingRef.current = false;
      manualFramesRef.current = [];
      talkPointerRef.current = null;
      recentFramesRef.current = [];
      automaticSpeechRef.current = false;
      automaticFramesRef.current = [];
      vadRef.current?.destroy();
      clearReplyTimer();
      stopAudio();
      const audioContext = audioContextRef.current;
      audioContextRef.current = null;
      if (audioContext) void audioContext.close().catch(() => {});
      wsService.disconnect();
    };
  }, [clearReplyTimer, handleMessage, handleState, loadSession, stopAudio]);

  useEffect(() => {
    messageAreaRef.current?.scrollTo({ top: messageAreaRef.current.scrollHeight });
  }, [lines, status]);

  useEffect(() => {
    if (!logOpen || logExpiresAt === null) return undefined;
    const delay = Math.max(0, logExpiresAt * 1000 - Date.now() + 100);
    const timer = window.setTimeout(() => requestPromptLogs(), delay);
    return () => window.clearTimeout(timer);
  }, [logOpen, logExpiresAt, requestPromptLogs]);

  const toggleLogCategory = (category: LogCategory) => {
    const next = logCategories.includes(category)
      ? logCategories.filter((item) => item !== category)
      : [...logCategories, category];
    logCategoriesRef.current = next;
    setLogCategories(next);
    requestPromptLogs(next);
  };

  const openPromptLogs = () => {
    logOpenRef.current = true;
    setLogOpen(true);
    requestPromptLogs();
  };

  const login = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loginBusy || !accountInput.trim() || !passwordInput) return;
    setLoginBusy(true);
    setLoginFailed(false);
    try {
      const response = await fetch(apiUrl('/api/accounts/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: accountInput.trim(), password: passwordInput }),
      });
      const result = await response.json() as { account?: string; sessionToken?: string };
      if (!response.ok || !result.account || !result.sessionToken) {
        setLoginFailed(true);
        return;
      }
      rememberAuthenticatedAccount(result.account, result.sessionToken);
      setPasswordInput('');
      setPhase('loading');
      void loadSession(result.account, result.sessionToken);
    } catch (_error) {
      setLoginFailed(true);
    } finally {
      setLoginBusy(false);
    }
  };

  const sendText = () => {
    const text = input.trim();
    if (!text || wsService.getCurrentState() !== 'OPEN') return;
    interruptReply();
    awaitingReplyRef.current = true;
    wsService.sendMessage({ type: 'set-generate-audio', enabled: false });
    wsService.sendMessage({ type: 'text-input', text, browser_time: formatBrowserTime() });
    appendLine('human', text);
    setInput('');
    setStatus('');
  };

  const guard = (
    <>
      {darkMode && <div className="guard-overlay" aria-hidden="true" />}
      <button
        className={`guard-button ${darkMode ? 'locked' : ''}`}
        type="button"
        aria-label={darkMode ? '长按两秒退出防误触' : '长按两秒进入防误触'}
        onPointerDown={(event) => { event.preventDefault(); startHold(); }}
        onPointerUp={endHold}
        onPointerCancel={endHold}
        onContextMenu={(event) => event.preventDefault()}
      >
        <svg className="guard-ring" viewBox="0 0 40 40" aria-hidden="true">
          <circle className="guard-ring-track" cx="20" cy="20" r="17" />
          <circle className="guard-ring-fill" cx="20" cy="20" r="17" pathLength={100}
            style={{ strokeDashoffset: 100 - holdProgress * 100 }} />
        </svg>
      </button>
      {darkMode && wakeLockError && (
        <p className="wake-lock-warning" role="status">当前浏览器无法保持亮屏</p>
      )}
    </>
  );

  if (phase === 'login') {
    return (
      <>
        {guard}
        <main className="mobile-root mobile-login">
          <form onSubmit={(event) => { void login(event); }}>
            <input aria-label="账号" autoComplete="username" placeholder="账号"
              value={accountInput} onChange={(event) => setAccountInput(event.target.value)} />
            <input aria-label="密码" type="password" autoComplete="current-password"
              placeholder="密码" value={passwordInput}
              onChange={(event) => setPasswordInput(event.target.value)} />
            <button type="submit" disabled={loginBusy}>
              {loginBusy ? '登录中…' : loginFailed ? '登录失败，重试' : '登录'}
            </button>
          </form>
        </main>
      </>
    );
  }

  if (phase !== 'ready') {
    return (
      <>
        {guard}
        <main className="mobile-root mobile-loading">连接中…</main>
      </>
    );
  }

  return (
    <main className="mobile-root mobile-shell">
      {guard}
      {!darkMode && (
        <div className="log-controls" aria-label="日志控制">
          <button className="log-control-button" type="button" aria-label="查看聊天记录"
            onClick={() => {
              setHistoryOpen(true);
              wsService.sendMessage({ type: 'fetch-history-list' });
            }}>记录</button>
          <button className={`log-control-button ${logEnabled ? 'active' : ''}`}
            type="button" aria-label={logEnabled ? '关闭日志模式' : '开启日志模式'}
            aria-pressed={logEnabled} disabled={wsService.getCurrentState() !== 'OPEN'}
            onClick={() => wsService.sendMessage({
              type: 'set-mobile-prompt-logging', enabled: !logEnabled,
            })}>
            日志
          </button>
          {logEnabled && (
            <button className="log-control-button" type="button" aria-label="查看日志"
              onClick={openPromptLogs}>查看</button>
          )}
        </div>
      )}
      {historyOpen && (
        <section className="prompt-log-panel" role="dialog" aria-modal="true" aria-label="聊天记录">
          <header className="prompt-log-header">
            <div><h2>聊天记录</h2><p>超过一小时后，下一条消息会开启新聊天</p></div>
            <button type="button" className="prompt-log-close" aria-label="关闭聊天记录"
              onClick={() => setHistoryOpen(false)}>关闭</button>
          </header>
          <div className="prompt-log-list history-list">
            {histories.length === 0 && <p className="prompt-log-empty">暂无聊天记录</p>}
            {histories.map((history) => (
              <button key={history.uid} type="button"
                className={`history-list-item ${history.uid === activeHistoryUid ? 'active' : ''}`}
                onClick={() => {
                  if (awaitingReplyRef.current || replyingRef.current) return;
                  wsService.sendMessage({
                    type: 'fetch-and-set-history', history_uid: history.uid,
                  });
                }}>
                <time>{history.timestamp
                  ? new Date(history.timestamp).toLocaleString('zh-CN') : '新聊天'}</time>
                <span>{history.latest_message?.content || '暂无消息'}</span>
              </button>
            ))}
          </div>
        </section>
      )}
      {logOpen && (
        <section className="prompt-log-panel" role="dialog" aria-modal="true" aria-label="本轮输入日志">
          <header className="prompt-log-header">
            <div>
              <h2>日志</h2>
              <p>记录本轮输入与后台总结，24 小时后自动清空</p>
            </div>
            <button type="button" className="prompt-log-close" aria-label="关闭日志"
              onClick={() => { logOpenRef.current = false; setLogOpen(false); }}>关闭</button>
          </header>
          <details className="prompt-log-filter">
            <summary>筛选显示内容（已选 {logCategories.length} 项）</summary>
            <div className="prompt-log-filter-options">
              {([
                ['input', '本轮输入及其他拼接内容'],
                ['short', '短期记忆'],
                ['long', '长期记忆'],
                ['score', '关系打分'],
                ['rag', '本轮 RAG 召回'],
              ] as [LogCategory, string][]).map(([category, label]) => (
                <label key={category}>
                  <input type="checkbox" checked={logCategories.includes(category)}
                    onChange={() => toggleLogCategory(category)} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </details>
          <div className="prompt-log-list">
            {promptLogs.length === 0 && <p className="prompt-log-empty">暂无日志</p>}
            {promptLogs.map((entry) => (
              <article className="prompt-log-entry" key={entry.id}>
                <time dateTime={entry.created_at}>
                  {new Date(entry.created_at).toLocaleString('zh-CN')}
                </time>
                <pre>{entry.prompt || (logCategories.includes('rag')
                  ? '本轮没有 RAG 召回结果' : '所选内容暂无记录')}</pre>
              </article>
            ))}
          </div>
        </section>
      )}
      <section className="emotion-stage" aria-label="角色情绪贴图">
        {previousImageUrl && <img className="emotion-image" src={previousImageUrl} alt="" />}
        {imageUrl && <img className="emotion-image current" key={imageUrl} src={imageUrl}
          alt="角色情绪贴图" style={{
            animationDuration: `${transition.duration}ms`,
            animationTimingFunction: transition.easing,
          }} />}
      </section>
      <section className="message-area" ref={messageAreaRef} aria-label="对话消息">
        <div className="message-list">
          {lines.map((line) => (
            <div key={line.id} className={`message ${line.role}`}>
              {line.text}
              {line.images?.map((image) => (
                <GeneratedImage key={image.id || image.url} image={image}
                  session={sessionRef.current} onOpen={setPreviewSrc} />
              ))}
            </div>
          ))}
        </div>
        {minimaxConfigured === false && (
          <p className="message-status" role="status">请先在电脑版为当前账号配置 MiniMax Key</p>
        )}
        {minimaxConfigured && minimaxAvailable === false && (
          <p className="message-status" role="status">MiniMax 生图服务暂不可用</p>
        )}
        {status && <p className="message-status" role="status">{status}</p>}
        {needsRecovery && mode === 'call' && (
          <button className="recovery-button" type="button" onClick={recoverCall}>
            恢复通话
          </button>
        )}
      </section>
      <div className="composer">
        {mode === 'text' ? (
          <input className="composer-input" aria-label="输入消息" placeholder="输入消息"
            enterKeyHint="send" value={input} onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                event.preventDefault();
                sendText();
              }
            }} />
        ) : (
          <button
            className={`talk-button ${holdingToTalk ? 'recording' : ''}`}
            type="button"
            disabled={callStarting}
            aria-pressed={holdingToTalk}
            onPointerDown={(event) => {
              if (event.button !== 0 && event.pointerType === 'mouse') return;
              event.preventDefault();
              if (startManualSpeech(event.pointerId)) {
                event.currentTarget.setPointerCapture(event.pointerId);
              }
            }}
            onPointerUp={(event) => {
              if (talkPointerRef.current !== event.pointerId) return;
              finishManualSpeech(true);
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
            }}
            onPointerCancel={(event) => {
              if (talkPointerRef.current === event.pointerId) finishManualSpeech(false);
            }}
            onLostPointerCapture={(event) => {
              if (talkPointerRef.current === event.pointerId) finishManualSpeech(false);
            }}
            onContextMenu={(event) => event.preventDefault()}
          >
            {callStarting ? '正在开启麦克风…' : holdingToTalk ? '松开发送' : '按住说话'}
          </button>
        )}
        <button className={`call-button ${mode === 'call' ? 'active' : ''}`} type="button"
          aria-label={mode === 'call' ? '挂断' : '开始通话'}
          aria-busy={callStarting}
          onClick={() => { if (mode === 'call') stopCall(); else void startCall(); }}>
          <PhoneIcon />
        </button>
      </div>
      {previewSrc && (
        <div className="image-preview" role="dialog" aria-modal="true" aria-label="图片全屏预览">
          <button className="image-preview-close" type="button" aria-label="关闭图片预览"
            onClick={() => setPreviewSrc(null)}>×</button>
          <img src={previewSrc} alt="生成的图片全屏预览"
            onPointerUp={(event) => {
              if (event.pointerType === 'mouse' && event.button !== 0) return;
              const now = Date.now();
              if (now - previewTapRef.current < 350) {
                event.preventDefault();
                previewTapRef.current = 0;
                setPreviewSrc(null);
              } else previewTapRef.current = now;
            }} />
        </div>
      )}
    </main>
  );
}
