import { useEffect, useRef } from "react";
import { useVAD } from "@/context/vad-context";

const INTERACTIVE_SELECTOR = [
  "input",
  "textarea",
  "select",
  "button",
  "a[href]",
  '[contenteditable]:not([contenteditable="false"])',
  '[role="button"]',
  '[role="textbox"]',
].join(",");

function isInteractiveTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element && Boolean(target.closest(INTERACTIVE_SELECTOR))
  );
}

/**
 * Hold Space to define one speech segment while the microphone is already on.
 * Interactive controls retain their native Space-key behavior.
 */
export function useSpaceToTalk() {
  const { startManualSpeech, finishManualSpeech } = useVAD();
  const spaceSessionActiveRef = useRef(false);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (
        event.code !== "Space" ||
        event.repeat ||
        event.isComposing ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.shiftKey ||
        isInteractiveTarget(event.target)
      ) {
        return;
      }

      const started = startManualSpeech();
      if (!started) return;

      spaceSessionActiveRef.current = true;
      event.preventDefault();
    };

    const finishSpaceSession = (event?: globalThis.KeyboardEvent) => {
      if (!spaceSessionActiveRef.current) return;

      spaceSessionActiveRef.current = false;
      event?.preventDefault();
      finishManualSpeech();
    };

    const handleKeyUp = (event: globalThis.KeyboardEvent) => {
      if (event.code === "Space") {
        finishSpaceSession(event);
      }
    };

    const handleWindowBlur = () => finishSpaceSession();

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    window.addEventListener("blur", handleWindowBlur);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      window.removeEventListener("blur", handleWindowBlur);
      finishSpaceSession();
    };
  }, [finishManualSpeech, startManualSpeech]);
}
