/* eslint-disable no-use-before-define */
import {
  createContext, useContext, useRef, useCallback, useEffect, useReducer, useMemo, useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { MicVAD } from '@ricky0123/vad-web';
import { useInterrupt } from '@/components/canvas/live2d';
import { audioTaskQueue } from '@/utils/task-queue';
import { useSendAudio } from '@/hooks/utils/use-send-audio';
import { AiStateContext, AiState } from './ai-state-context';
import { useLocalStorage } from '@/hooks/utils/use-local-storage';
import { toaster } from '@/components/ui/toaster';

/**
 * VAD settings configuration interface
 * @interface VADSettings
 */
export interface VADSettings {
  /** Threshold for positive speech detection (0-100) */
  positiveSpeechThreshold: number;

  /** Threshold for negative speech detection (0-100) */
  negativeSpeechThreshold: number;

  /** Number of frames for speech redemption */
  redemptionFrames: number;
}

/**
 * VAD context state interface
 * @interface VADState
 */
interface VADState {
  /** Auto stop mic feature state */
  autoStopMic: boolean;

  /** Microphone active state */
  micOn: boolean;

  /** Set microphone state */
  setMicOn: (value: boolean) => void;

  /** Set Auto stop mic state */
  setAutoStopMic: (value: boolean) => void;

  /** Start microphone and VAD */
  startMic: () => Promise<void>;

  /** Stop microphone and VAD */
  stopMic: () => void;

  /** Start a manual speech segment while the space key is held */
  startManualSpeech: () => boolean;

  /** Finish and send the manual speech segment */
  finishManualSpeech: () => void;

  /** Previous speech probability value */
  previousTriggeredProbability: number;

  /** Set previous speech probability */
  setPreviousTriggeredProbability: (value: number) => void;

  /** VAD settings configuration */
  settings: VADSettings;

  /** Update VAD settings */
  updateSettings: (newSettings: VADSettings) => void;

  /** Auto start microphone when AI starts speaking */
  autoStartMicOn: boolean;

  /** Set auto start microphone state */
  setAutoStartMicOn: (value: boolean) => void;

  /** Auto start microphone when conversation ends */
  autoStartMicOnConvEnd: boolean;

  /** Set auto start microphone when conversation ends state */
  setAutoStartMicOnConvEnd: (value: boolean) => void;
}

/**
 * Default values and constants
 */
const DEFAULT_VAD_SETTINGS: VADSettings = {
  positiveSpeechThreshold: 50,
  negativeSpeechThreshold: 35,
  redemptionFrames: 35,
};

const DEFAULT_VAD_STATE = {
  micOn: false,
  autoStopMic: false,
  autoStartMicOn: false,
  autoStartMicOnConvEnd: false,
};

const VAD_PRE_SPEECH_PAD_FRAMES = 20;

/**
 * Create the VAD context
 */
export const VADContext = createContext<VADState | null>(null);

/**
 * VAD Provider Component
 * Manages voice activity detection and microphone state
 *
 * @param {Object} props - Provider props
 * @param {React.ReactNode} props.children - Child components
 */
export function VADProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  // Refs for VAD instance and state
  const vadRef = useRef<MicVAD | null>(null);
  const previousTriggeredProbabilityRef = useRef(0);
  const previousAiStateRef = useRef<AiState>('idle');
  const micOnRef = useRef(false);
  const manualSpeechActiveRef = useRef(false);
  const manualSpeechFramesRef = useRef<Float32Array[]>([]);
  const manualSpeechPreviousAiStateRef = useRef<AiState>('idle');
  const recentAudioFramesRef = useRef<Float32Array[]>([]);
  const activeSpeechFramesRef = useRef<Float32Array[]>([]);

  // Persistent state management
  const [micOn, setMicOn] = useState(false);
  const [autoStopMic, setAutoStopMicState] = useLocalStorage(
    'autoStopMic',
    DEFAULT_VAD_STATE.autoStopMic,
  );
  const autoStopMicRef = useRef(autoStopMic);
  const [settings, setSettings] = useLocalStorage<VADSettings>(
    'vadSettings',
    DEFAULT_VAD_SETTINGS,
  );
  const [autoStartMicOn, setAutoStartMicOnState] = useLocalStorage(
    'autoStartMicOn',
    DEFAULT_VAD_STATE.autoStartMicOn,
  );
  const autoStartMicRef = useRef(false);
  const [autoStartMicOnConvEnd, setAutoStartMicOnConvEndState] = useLocalStorage(
    'autoStartMicOnConvEnd',
    DEFAULT_VAD_STATE.autoStartMicOnConvEnd,
  );
  const autoStartMicOnConvEndRef = useRef(false);

  // Force update mechanism for ref updates
  const [, forceUpdate] = useReducer((x) => x + 1, 0);

  // External hooks and contexts
  const { interrupt } = useInterrupt();
  const { sendAudioPartition } = useSendAudio();
  const { aiState, setAiState } = useContext(AiStateContext)!;

  // Refs for callback stability
  const interruptRef = useRef(interrupt);
  const sendAudioPartitionRef = useRef(sendAudioPartition);
  const aiStateRef = useRef<AiState>(aiState);
  const setAiStateRef = useRef(setAiState);

  const isProcessingRef = useRef(false);

  // Update refs when dependencies change
  useEffect(() => {
    aiStateRef.current = aiState;
  }, [aiState]);

  useEffect(() => {
    micOnRef.current = micOn;
  }, [micOn]);

  useEffect(() => {
    interruptRef.current = interrupt;
  }, [interrupt]);

  useEffect(() => {
    sendAudioPartitionRef.current = sendAudioPartition;
  }, [sendAudioPartition]);

  useEffect(() => {
    setAiStateRef.current = setAiState;
  }, [setAiState]);

  useEffect(() => {
    autoStartMicRef.current = autoStartMicOn;
  }, []);

  useEffect(() => {
    autoStartMicOnConvEndRef.current = autoStartMicOnConvEnd;
  }, []);

  /**
   * Update previous triggered probability and force re-render
   */
  const setPreviousTriggeredProbability = useCallback((value: number) => {
    previousTriggeredProbabilityRef.current = value;
    forceUpdate();
  }, []);

  /**
   * Handle speech start event (initial detection)
   */
  const handleSpeechStart = useCallback(() => {
    console.log('Speech started - saving current state');
    activeSpeechFramesRef.current = manualSpeechActiveRef.current
      ? []
      : [...recentAudioFramesRef.current];

    if (manualSpeechActiveRef.current) {
      isProcessingRef.current = true;
      return;
    }

    // Save current AI state but DON'T change to listening yet
    previousAiStateRef.current = aiStateRef.current;
    isProcessingRef.current = true;
    // Don't change state here - wait for onSpeechRealStart
  }, []);

  /**
   * Handle real speech start event (confirmed speech)
   */
  const handleSpeechRealStart = useCallback(() => {
    console.log('Real speech confirmed - checking if need to interrupt');
    if (manualSpeechActiveRef.current) {
      setAiStateRef.current('listening');
      aiStateRef.current = 'listening';
      return;
    }

    // Check if we need to interrupt based on the PREVIOUS state (before speech started)
    if (previousAiStateRef.current === 'thinking-speaking') {
      console.log('Interrupting AI speech due to user speaking');
      interruptRef.current();
    }
    // Now change to listening state
    setAiStateRef.current('listening');
  }, []);

  /**
   * Handle frame processing event
   */
  const handleFrameProcessed = useCallback((
    probs: { isSpeech: number },
    frame: Float32Array,
  ) => {
    const frameCopy = frame.slice();
    recentAudioFramesRef.current.push(frameCopy);
    if (recentAudioFramesRef.current.length > VAD_PRE_SPEECH_PAD_FRAMES) {
      recentAudioFramesRef.current.shift();
    }

    if (manualSpeechActiveRef.current) {
      manualSpeechFramesRef.current.push(frameCopy);
    } else if (isProcessingRef.current) {
      activeSpeechFramesRef.current.push(frameCopy);
    }

    if (probs.isSpeech > previousTriggeredProbabilityRef.current) {
      setPreviousTriggeredProbability(probs.isSpeech);
    }
  }, []);

  /**
   * Handle speech end event
   */
  const handleSpeechEnd = useCallback((audio: Float32Array) => {
    if (!isProcessingRef.current) return;
    console.log('Speech ended');

    if (manualSpeechActiveRef.current) {
      console.log('Manual speech active - deferring audio until space is released');
      setPreviousTriggeredProbability(0);
      isProcessingRef.current = false;
      activeSpeechFramesRef.current = [];
      setAiStateRef.current('listening');
      aiStateRef.current = 'listening';
      return;
    }

    audioTaskQueue.clearQueue();

    if (autoStopMicRef.current) {
      stopMic();
    } else {
      console.log('Auto stop mic is OFF, keeping mic active');
    }

    setPreviousTriggeredProbability(0);
    sendAudioPartitionRef.current(audio);
    isProcessingRef.current = false;
    activeSpeechFramesRef.current = [];
    setAiStateRef.current("thinking-speaking");
  }, []);

  /**
   * Handle VAD misfire event
   */
  const handleVADMisfire = useCallback(() => {
    if (!isProcessingRef.current) return;
    console.log('VAD misfire detected');
    setPreviousTriggeredProbability(0);
    isProcessingRef.current = false;
    activeSpeechFramesRef.current = [];

    if (manualSpeechActiveRef.current) {
      setAiStateRef.current('listening');
      aiStateRef.current = 'listening';
      return;
    }

    // Restore the previous AI state silently. Short/misfired speech is expected
    // during normal microphone use and should not interrupt the interface.
    setAiStateRef.current(previousAiStateRef.current);
  }, []);

  /**
   * Update VAD settings and restart if active
   */
  const updateSettings = useCallback((newSettings: VADSettings) => {
    setSettings(newSettings);
    if (vadRef.current) {
      stopMic();
      setTimeout(() => {
        startMic();
      }, 100);
    }
  }, []);

  /**
   * Initialize new VAD instance
   */
  const initVAD = async () => {
    const newVAD = await MicVAD.new({
      model: "v5",
      preSpeechPadFrames: VAD_PRE_SPEECH_PAD_FRAMES,
      positiveSpeechThreshold: settings.positiveSpeechThreshold / 100,
      negativeSpeechThreshold: settings.negativeSpeechThreshold / 100,
      redemptionFrames: settings.redemptionFrames,
      baseAssetPath: './libs/',
      onnxWASMBasePath: './libs/',
      onSpeechStart: handleSpeechStart,
      onSpeechRealStart: handleSpeechRealStart,
      onFrameProcessed: handleFrameProcessed,
      onSpeechEnd: handleSpeechEnd,
      onVADMisfire: handleVADMisfire,
    });

    vadRef.current = newVAD;
    newVAD.start();
  };

  /**
   * Start microphone and VAD processing
   */
  const startMic = useCallback(async () => {
    try {
      if (!vadRef.current) {
        console.log('Initializing VAD');
        await initVAD();
      } else {
        console.log('Starting VAD');
        vadRef.current.start();
      }
      micOnRef.current = true;
      setMicOn(true);
    } catch (error) {
      console.error('Failed to start VAD:', error);
      toaster.create({
        title: `${t('error.failedStartVAD')}: ${error}`,
        type: 'error',
        duration: 2000,
      });
    }
  }, [t]);

  /**
   * Stop microphone and VAD processing
   */
  const stopMic = useCallback(() => {
    console.log('Stopping VAD');
    manualSpeechActiveRef.current = false;
    manualSpeechFramesRef.current = [];
    recentAudioFramesRef.current = [];
    activeSpeechFramesRef.current = [];
    if (vadRef.current) {
      vadRef.current.pause();
      vadRef.current.destroy();
      vadRef.current = null;
      console.log('VAD stopped and destroyed successfully');
      setPreviousTriggeredProbability(0);
    } else {
      console.log('VAD instance not found');
    }
    micOnRef.current = false;
    setMicOn(false);
    isProcessingRef.current = false;
  }, []);

  useEffect(() => () => {
    manualSpeechActiveRef.current = false;
    manualSpeechFramesRef.current = [];
    recentAudioFramesRef.current = [];
    activeSpeechFramesRef.current = [];
    isProcessingRef.current = false;
    if (vadRef.current) {
      vadRef.current.pause();
      vadRef.current.destroy();
      vadRef.current = null;
    }
    micOnRef.current = false;
  }, []);

  /**
   * Start a manual speech segment controlled by holding the space key.
   * The microphone must already be active; this never opens it implicitly.
   */
  const startManualSpeech = useCallback(() => {
    if (!micOnRef.current || !vadRef.current || manualSpeechActiveRef.current) {
      return false;
    }

    const speechAlreadyInProgress = isProcessingRef.current;
    manualSpeechPreviousAiStateRef.current = aiStateRef.current;
    manualSpeechFramesRef.current = speechAlreadyInProgress
      ? [...activeSpeechFramesRef.current]
      : [...recentAudioFramesRef.current];
    manualSpeechActiveRef.current = true;
    activeSpeechFramesRef.current = [];
    setPreviousTriggeredProbability(0);

    if (aiStateRef.current === 'thinking-speaking') {
      interruptRef.current();
    }

    setAiStateRef.current('listening');
    aiStateRef.current = 'listening';
    return true;
  }, []);

  /**
   * End a space-controlled segment and send it as one utterance. Pausing and
   * immediately restarting VAD clears its internal buffer without closing the
   * microphone, preventing a duplicate automatic speech-end event.
   */
  const finishManualSpeech = useCallback(() => {
    if (!manualSpeechActiveRef.current) return;

    manualSpeechActiveRef.current = false;
    const frames = manualSpeechFramesRef.current;
    const previousAiState = manualSpeechPreviousAiStateRef.current;
    manualSpeechFramesRef.current = [];
    recentAudioFramesRef.current = [];
    activeSpeechFramesRef.current = [];
    isProcessingRef.current = false;
    setPreviousTriggeredProbability(0);

    if (vadRef.current && micOnRef.current) {
      vadRef.current.pause();
      vadRef.current.start();
    }

    // Space mode is fully manual: VAD confidence only controls automatic mode.
    // Releasing Space sends every frame collected during the manual session.
    if (frames.length === 0) {
      const nextState = previousAiState === 'thinking-speaking'
        ? 'interrupted'
        : previousAiState;
      setAiStateRef.current(nextState);
      aiStateRef.current = nextState;
      return;
    }

    const totalLength = frames.reduce((length, frame) => length + frame.length, 0);
    const audio = new Float32Array(totalLength);
    let offset = 0;
    frames.forEach((frame) => {
      audio.set(frame, offset);
      offset += frame.length;
    });

    audioTaskQueue.clearQueue();
    sendAudioPartitionRef.current(audio);
    setAiStateRef.current('thinking-speaking');
    aiStateRef.current = 'thinking-speaking';
  }, []);

  /**
   * Set Auto stop mic state
   */
  const setAutoStopMic = useCallback((value: boolean) => {
    autoStopMicRef.current = value;
    setAutoStopMicState(value);
    forceUpdate();
  }, []);

  const setAutoStartMicOn = useCallback((value: boolean) => {
    autoStartMicRef.current = value;
    setAutoStartMicOnState(value);
    forceUpdate();
  }, []);

  const setAutoStartMicOnConvEnd = useCallback((value: boolean) => {
    autoStartMicOnConvEndRef.current = value;
    setAutoStartMicOnConvEndState(value);
    forceUpdate();
  }, []);

  // Memoized context value
  const contextValue = useMemo(
    () => ({
      autoStopMic: autoStopMicRef.current,
      micOn,
      setMicOn,
      setAutoStopMic,
      startMic,
      stopMic,
      startManualSpeech,
      finishManualSpeech,
      previousTriggeredProbability: previousTriggeredProbabilityRef.current,
      setPreviousTriggeredProbability,
      settings,
      updateSettings,
      autoStartMicOn: autoStartMicRef.current,
      setAutoStartMicOn,
      autoStartMicOnConvEnd: autoStartMicOnConvEndRef.current,
      setAutoStartMicOnConvEnd,
    }),
    [
      micOn,
      startMic,
      stopMic,
      startManualSpeech,
      finishManualSpeech,
      settings,
      updateSettings,
    ],
  );

  return (
    <VADContext.Provider value={contextValue}>
      {children}
    </VADContext.Provider>
  );
}

/**
 * Custom hook to use the VAD context
 * @throws {Error} If used outside of VADProvider
 */
export function useVAD() {
  const context = useContext(VADContext);

  if (!context) {
    throw new Error('useVAD must be used within a VADProvider');
  }

  return context;
}
