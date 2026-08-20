import { useEffect } from "react";
import { optionalFeature } from "@optional-feature";
import { useAccount } from "@/context/account-context";

/** 与后端 account_key(name).endswith("cs") 的“cs 账号”判定保持一致。 */
function isCsAccount(account: string | null): boolean {
  return !!account && account.trim().toLowerCase().endsWith("cs");
}

/**
 * 按 j（非打字状态）让下一条用户消息附带发送时刻的心率。仅 cs 账号启用；
 * 打字时（输入框/文本域/富文本编辑/输入法组合）不触发，避免吞掉字母 j。
 */
export function useHeartRateShortcut() {
  const { account } = useAccount();
  const enabled = isCsAccount(account);

  useEffect(() => {
    if (!enabled) return;

    const isTypingTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) return false;

      if (
        target.isContentEditable ||
        target.closest('[contenteditable="true"], [role="textbox"]')
      ) {
        return true;
      }

      if (target instanceof HTMLTextAreaElement) return true;
      if (!(target instanceof HTMLInputElement)) return false;

      return [
        "email",
        "number",
        "password",
        "search",
        "tel",
        "text",
        "url",
      ].includes(target.type);
    };

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.code !== "KeyJ" || event.key.toLowerCase() !== "j") return;
      if (event.isComposing) return;
      if (isTypingTarget(event.target)) return;
      if (
        event.repeat ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey
      ) return;

      optionalFeature.requestHeartRateForNextMessage();
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => {
      window.removeEventListener("keydown", handleKeyDown, true);
      // 切换账号/登出时丢弃未使用的一次性标记，避免泄漏到其他账号。
      optionalFeature.clearHeartRateRequest();
    };
  }, [enabled]);
}
