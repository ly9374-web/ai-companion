import { optionalFeature } from '@optional-feature';

export interface SpeechSegment {
  text: string;
  start_ms: number;
  end_ms: number;
}

interface ProfilerAudioTimeline {
  hasSegments: boolean;
  sync: (currentTimeMs: number) => void;
  finish: (interrupted: boolean, currentTimeMs: number) => void;
}

export const createProfilerAudioTimeline = (
  speechSegments: SpeechSegment[],
  updateSubtitle: (text: string) => void,
): ProfilerAudioTimeline => {
  const segments = speechSegments.filter((segment) => (
    typeof segment.text === 'string'
    && Number.isFinite(segment.start_ms)
    && Number.isFinite(segment.end_ms)
    && segment.start_ms >= 0
    && segment.end_ms > segment.start_ms
  ));
  let nextSegmentIndex = 0;
  let activeSegmentIndex = -1;

  const finish = (interrupted: boolean, currentTimeMs: number) => {
    if (activeSegmentIndex < 0) return;
    const segment = segments[activeSegmentIndex];
    const segmentDuration = segment.end_ms - segment.start_ms;
    const playbackRatio = interrupted
      ? Math.max(0, Math.min(1, (currentTimeMs - segment.start_ms) / segmentDuration))
      : 1;
    optionalFeature.onAssistantAudioEnd({
      text: segment.text,
      interrupted,
      playbackRatio,
    });
    activeSegmentIndex = -1;
  };

  const sync = (currentTimeMs: number) => {
    if (!segments.length) return;
    if (
      activeSegmentIndex >= 0
      && currentTimeMs >= segments[activeSegmentIndex].end_ms
    ) {
      finish(false, currentTimeMs);
    }
    if (
      activeSegmentIndex < 0
      && nextSegmentIndex < segments.length
      && currentTimeMs >= segments[nextSegmentIndex].start_ms
    ) {
      activeSegmentIndex = nextSegmentIndex;
      nextSegmentIndex += 1;
      const segment = segments[activeSegmentIndex];
      updateSubtitle(segment.text);
      optionalFeature.onAssistantAudioStart({ text: segment.text });
    }
  };

  return {
    hasSegments: segments.length > 0,
    sync,
    finish,
  };
};
