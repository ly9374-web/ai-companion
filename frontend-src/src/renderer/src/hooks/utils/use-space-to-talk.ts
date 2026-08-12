import { useEffect, useRef } from "react";
import { useVAD } from "@/context/vad-context";

/**
 * Hold Space to define one speech segment while the microphone is already on.
 * Space is reserved for speech and never activates the currently focused control.
 */
export function useSpaceToTalk() {
  const { startManualSpeech, finishManualSpeech } = useVAD();
  const spaceSessionActiveRef = useRef(false);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.code !== "Space" || event.isComposing) return;

      // Capture Space before focused buttons can turn it into a synthetic click.
      event.preventDefault();
      event.stopPropagation();

      if (
        event.repeat ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.shiftKey
      ) return;

      const started = startManualSpeech();
      if (!started) return;

      spaceSessionActiveRef.current = true;
    };

    const finishSpaceSession = (event?: globalThis.KeyboardEvent) => {
      if (!spaceSessionActiveRef.current) return;

      spaceSessionActiveRef.current = false;
      event?.preventDefault();
      finishManualSpeech();
    };

    const handleKeyUp = (event: globalThis.KeyboardEvent) => {
      if (event.code !== "Space" || event.isComposing) return;

      event.preventDefault();
      event.stopPropagation();
      finishSpaceSession(event);
    };

    const handleWindowBlur = () => finishSpaceSession();

    window.addEventListener("keydown", handleKeyDown, true);
    window.addEventListener("keyup", handleKeyUp, true);
    window.addEventListener("blur", handleWindowBlur);

    return () => {
      window.removeEventListener("keydown", handleKeyDown, true);
      window.removeEventListener("keyup", handleKeyUp, true);
      window.removeEventListener("blur", handleWindowBlur);
      finishSpaceSession();
    };
  }, [finishManualSpeech, startManualSpeech]);
}
