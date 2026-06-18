"use client";

import clsx from "clsx";
import { Check } from "lucide-react";
import { PAYMENT_HINTS } from "@/lib/flowHints";

/** Must match keyboards/menus.py PAYMENT_OPTIONS exactly */
export const PAYMENT_OPTIONS = ["IBAN", "PayPal", "Wise", "Revolut"] as const;

export const PAYMENT_LAYOUT_ROWS: readonly (readonly string[])[] = [
  ["IBAN", "PayPal"],
  ["Wise", "Revolut"],
];

export type PaymentOption = (typeof PAYMENT_OPTIONS)[number];

type Props = {
  operation: "خرید" | "فروش";
  selected: string[];
  onChange: (methods: string[]) => void;
  disabled?: boolean;
};

export function paymentSelectionTitle(operation: "خرید" | "فروش"): string {
  return operation === "خرید" ? "روش‌های دریافت" : "روش‌های پرداخت";
}

export function paymentSelectionHint(operation: "خرید" | "فروش"): string {
  return operation === "خرید" ? PAYMENT_HINTS.buy : PAYMENT_HINTS.sell;
}

export function PaymentMethodPicker({ operation, selected, onChange, disabled }: Props) {
  const set = new Set(selected);
  const hintLines = paymentSelectionHint(operation).split("\n");

  function toggle(method: string) {
    if (disabled) return;
    if (set.has(method)) {
      onChange(selected.filter((m) => m !== method));
    } else {
      onChange([...selected, method]);
    }
  }

  return (
    <div className="space-y-4">
      <div className="mb-4 space-y-1">
        <h2 className="text-lg font-bold text-white">{hintLines[0]}</h2>
        {hintLines.slice(1).map((line, i) => (
          <p key={i} className="text-sm leading-7 text-white/65">
            {line}
          </p>
        ))}
      </div>

      <div className="grid gap-2">
        {PAYMENT_LAYOUT_ROWS.map((row, ri) => (
          <div key={ri} className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-2">
            {row.map((method) => {
              const active = set.has(method);
              return (
                <button
                  key={method}
                  type="button"
                  disabled={disabled}
                  onClick={() => toggle(method)}
                  className={clsx(
                    "chip-select flex items-center justify-center gap-2 py-3 font-medium",
                    active && "chip-select-active",
                  )}
                >
                  {active && <Check className="h-4 w-4 shrink-0" />}
                  {active ? `✅ ${method}` : method}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {selected.length > 0 && (
        <p className="rounded-lg bg-brand-500/10 px-3 py-2 text-xs text-brand-100">
          ✅ {paymentSelectionTitle(operation)}: {selected.join(" · ")}
        </p>
      )}
    </div>
  );
}
