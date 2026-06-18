import { useEffect } from "react";

type Options = {
  disabled?: boolean;
};

/** Enter (without Shift) advances wizard steps — same as clicking «بعدی». */
export function useWizardEnterKey(onEnter: () => void, options?: Options) {
  const disabled = options?.disabled ?? false;

  useEffect(() => {
    if (disabled) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;

      const el = e.target as HTMLElement | null;
      if (!el) return;
      if (el.tagName === "TEXTAREA") return;
      if (el.tagName === "BUTTON" || el.tagName === "A") return;
      if (el.isContentEditable) return;

      e.preventDefault();
      onEnter();
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onEnter, disabled]);
}

export function wizardTextareaEnter(
  e: React.KeyboardEvent<HTMLTextAreaElement>,
  onEnter: () => void,
) {
  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
    e.preventDefault();
    onEnter();
  }
}
