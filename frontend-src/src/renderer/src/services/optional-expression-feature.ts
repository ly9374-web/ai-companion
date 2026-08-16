import { getCurrentBaseUrl } from '@/constants/connection-settings';

interface RuntimeExpressionFeature {
  setEmotion: (emotion: string) => boolean;
  configureInteraction?: (options: ExpressionInteractionOptions) => void;
  destroy: () => void;
}

interface ExpressionInteractionOptions {
  modelUrl: string;
  pointerInteractive: boolean;
  scrollToResize: boolean;
}

interface ExpressionManifest {
  available: boolean;
  frontend_entry?: string;
  config?: {
    default_emotion?: string;
    emotions?: Record<string, string>;
  };
}

class OptionalExpressionFeatureBridge {
  private runtime: RuntimeExpressionFeature | null = null;

  private loadPromise: Promise<void> | null = null;

  private interactionOptions: ExpressionInteractionOptions = {
    modelUrl: '',
    pointerInteractive: false,
    scrollToResize: false,
  };

  constructor() {
    void this.ensureLoaded();
  }

  private getBaseUrl() {
    return getCurrentBaseUrl();
  }

  private async ensureLoaded() {
    if (this.loadPromise) return this.loadPromise;
    this.loadPromise = (async () => {
      try {
        const baseUrl = this.getBaseUrl();
        const response = await fetch(
          new URL('/optional-features/expression/manifest', baseUrl),
          { cache: 'no-store' },
        );
        if (!response.ok) return;

        const manifest = await response.json() as ExpressionManifest;
        if (!manifest.available || !manifest.frontend_entry) return;

        const responseBaseUrl = response.url || baseUrl;
        const runtimeEntry = new URL(manifest.frontend_entry, responseBaseUrl).href;
        const config = manifest.config || {};
        const emotionUrls = Object.fromEntries(
          Object.entries(config.emotions || {}).map(([emotion, url]) => [
            emotion,
            new URL(url, responseBaseUrl).href,
          ]),
        );
        const runtimeModule = await import(/* @vite-ignore */ runtimeEntry);
        if (typeof runtimeModule.createExpressionFeature !== 'function') return;

        this.runtime = await runtimeModule.createExpressionFeature({
          ...config,
          emotions: emotionUrls,
        });
        this.runtime?.configureInteraction?.(this.interactionOptions);
      } catch (error) {
        console.warn('[Expression] 可选模块不可用，继续使用 Live2D:', error);
        this.runtime = null;
      }
    })();
    return this.loadPromise;
  }

  async setEmotion(emotion: string) {
    await this.ensureLoaded();
    try {
      this.runtime?.setEmotion(emotion);
    } catch (error) {
      console.warn('[Expression] 表情切换失败，保留当前画面:', error);
    }
  }

  configureInteraction(options: ExpressionInteractionOptions) {
    this.interactionOptions = options;
    this.runtime?.configureInteraction?.(options);
  }
}

export const optionalExpressionFeature = new OptionalExpressionFeatureBridge();
