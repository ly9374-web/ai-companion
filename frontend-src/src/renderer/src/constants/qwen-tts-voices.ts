export const TTS_VOICE_STORAGE_KEY = "qwenTtsVoice";
export const TTS_LANGUAGE_HINT_STORAGE_KEY = "qwenTtsLanguageHint";
export const TTS_INSTRUCTION_PRESET_STORAGE_KEY = "qwenTtsInstructionPreset";

export const DEFAULT_TTS_VOICE =
  "qwen-audio-3.0-tts-flash-longyuyaoluan";
export const DEFAULT_TTS_LANGUAGE_HINT = "en";
export const DEFAULT_TTS_INSTRUCTION_PRESET = "none";

export const QWEN_TTS_LANGUAGE_HINTS = [
  { label: "English", value: "en" },
  { label: "中文", value: "zh" },
];

export const QWEN_TTS_INSTRUCTION_PRESETS = {
  none: "",
  instruction1: "语气温柔，平静，保持无情绪的阅读文本。",
} as const;

export const QWEN_TTS_VOICES: Array<{ label: string; value: string }> = [
  { label: "龙羽瑶鸾", value: "qwen-audio-3.0-tts-flash-longyuyaoluan" },
  { label: "龙杉竹昕", value: "qwen-audio-3.0-tts-flash-longshanzhuxin" },
  { label: "龙溪霓燕", value: "qwen-audio-3.0-tts-flash-longxiniyan" },
  { label: "Ivy Hu（艾薇·胡）", value: "qwen-audio-3.0-tts-flash-loongivyhu" },
  { label: "龙晴湘菊", value: "qwen-audio-3.0-tts-flash-longqingxiangju" },
  { label: "Olivia Lin（奥利维亚·林）", value: "qwen-audio-3.0-tts-flash-loongolivialin" },
  { label: "龙璇松杏", value: "qwen-audio-3.0-tts-flash-longxuansongxing" },
  { label: "Adrian Gao（艾德里安·高）", value: "qwen-audio-3.0-tts-flash-loongadriangao" },
  { label: "龙峰瑾鹤", value: "qwen-audio-3.0-tts-flash-longfengjinhe" },
  { label: "龙晦露凌", value: "qwen-audio-3.0-tts-flash-longhuiluling" },
  { label: "龙雪瑜珺", value: "qwen-audio-3.0-tts-flash-longxueyujun" },
  { label: "龙荷雪翎", value: "qwen-audio-3.0-tts-flash-longhexueling" },
  { label: "龙鸿薇枫", value: "qwen-audio-3.0-tts-flash-longhongweifeng" },
  { label: "龙凤岫澈", value: "qwen-audio-3.0-tts-flash-longfengxiuche" },
];

const QWEN_TTS_VOICE_IDS = new Set<string>(
  QWEN_TTS_VOICES.map(({ value }) => value),
);

const getStoredChoice = (
  storageKey: string,
  allowedValues: ReadonlySet<string>,
  fallback: string,
): string => {
  try {
    const stored = localStorage.getItem(storageKey);
    if (!stored) return fallback;
    const parsed: unknown = JSON.parse(stored);
    if (typeof parsed === "string" && allowedValues.has(parsed)) {
      return parsed;
    }
  } catch (error) {
    console.error(`Failed to read saved Qwen TTS setting ${storageKey}:`, error);
  }
  return fallback;
};

export const getStoredTtsVoice = (): string => {
  return getStoredChoice(
    TTS_VOICE_STORAGE_KEY,
    QWEN_TTS_VOICE_IDS,
    DEFAULT_TTS_VOICE,
  );
};

export const getStoredTtsLanguageHint = (): string => {
  return getStoredChoice(
    TTS_LANGUAGE_HINT_STORAGE_KEY,
    new Set(QWEN_TTS_LANGUAGE_HINTS.map(({ value }) => value)),
    DEFAULT_TTS_LANGUAGE_HINT,
  );
};

export const getStoredTtsInstructionPreset = (): string => {
  return getStoredChoice(
    TTS_INSTRUCTION_PRESET_STORAGE_KEY,
    new Set(Object.keys(QWEN_TTS_INSTRUCTION_PRESETS)),
    DEFAULT_TTS_INSTRUCTION_PRESET,
  );
};

export const getTtsInstruction = (preset: string): string => {
  if (preset in QWEN_TTS_INSTRUCTION_PRESETS) {
    return QWEN_TTS_INSTRUCTION_PRESETS[
      preset as keyof typeof QWEN_TTS_INSTRUCTION_PRESETS
    ];
  }
  return "";
};

export const getStoredQwenTtsOptions = () => {
  const instructionPreset = getStoredTtsInstructionPreset();
  return {
    voice: getStoredTtsVoice(),
    language_hint: getStoredTtsLanguageHint(),
    instruction: getTtsInstruction(instructionPreset),
  };
};
