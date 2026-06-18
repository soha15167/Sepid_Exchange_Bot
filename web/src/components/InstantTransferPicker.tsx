"use client";

import clsx from "clsx";
import { EURO_FLOW_HINTS } from "@/lib/flowHints";
import { WizardStepHint } from "@/components/WizardStepHint";

type Props = {
  value: string;
  onChange: (value: string) => void;
};

export function InstantTransferPicker({ value, onChange }: Props) {
  const { title, body, options } = EURO_FLOW_HINTS.instant;

  return (
    <div>
      <WizardStepHint title={title} body={body} />
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={clsx(
              "rounded-xl border px-4 py-2.5 text-sm transition",
              value === o.value
                ? "border-brand-400/40 bg-brand-500/20 text-brand-100"
                : "border-white/10 text-white/70 hover:bg-white/5",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
