"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, X } from "lucide-react";
import { apiFetch, fmtNum, type DealStatus, type Offer } from "@/lib/api";
import { DealGatePanel } from "@/components/DealGatePanel";

const STATUS_FA: Record<string, string> = {
  pending: "در انتظار",
  accepted: "پذیرفته",
  rejected: "رد شده",
  gate_aborted: "لغو معامله",
  gate_rejected: "رد در gate",
  gate_closed: "بسته شده",
};

type Props = {
  offers: Offer[];
  token: string | null;
  onChange: () => void;
  deals?: Record<number, DealStatus>;
  onDealChange?: () => void;
};

export function IncomingOffersPanel({ offers, token, onChange, deals, onDealChange }: Props) {
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState("");

  async function act(id: number, action: "accept" | "reject") {
    if (!token) return;
    setBusy(id);
    setErr("");
    try {
      await apiFetch(`/api/offers/${id}/${action}`, { method: "POST" }, token);
      onChange();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(null);
    }
  }

  if (offers.length === 0) {
    return (
      <section className="glass p-4 sm:p-6">
        <h2 className="mb-2 font-bold">پیشنهادهای دریافتی</h2>
        <p className="text-sm text-white/45">پیشنهاد در انتظار یا پذیرفته‌شده‌ای روی آگهی‌های شما نیست.</p>
      </section>
    );
  }

  return (
    <section className="glass p-4 sm:p-6">
      <h2 className="mb-4 font-bold">پیشنهادهای دریافتی</h2>
      {err && <p className="mb-3 text-sm text-red-300">{err}</p>}
      <ul className="space-y-3">
        {offers.map((o) => (
          <li
            key={o.id}
            className="rounded-xl border border-white/10 bg-white/5 p-4"
          >
            <div className="card-stack">
              <div className="card-stack-body space-y-1">
                <p className="font-semibold">
                  #{o.seq} · آگهی #{o.advert_id} · {o.proposer_name}
                </p>
                <p className="text-sm text-white/60">
                  {o.advert_operation} — {fmtNum(o.advert_euro_amount)} €
                  {!o.skips_toman_rate && o.rate_toman != null && (
                    <> · {fmtNum(o.rate_toman)} تومان</>
                  )}
                </p>
                {o.proposed_euro_amount ? (
                  <p className="text-xs text-brand-200">
                    مقدار پیشنهادی: {fmtNum(o.proposed_euro_amount)} €
                  </p>
                ) : null}
                {o.proposer_account_country && (
                  <p className="text-xs text-white/50">کشور: {o.proposer_account_country}</p>
                )}
                {o.description && (
                  <p className="text-sm text-white/70 line-clamp-2">{o.description}</p>
                )}
                <p className="text-xs text-white/40">وضعیت: {offerStatusLabel(o.status)}</p>
              </div>
              {(o.status || "pending") === "pending" ? (
                <div className="panel-actions">
                  <button
                    type="button"
                    disabled={busy === o.id}
                    onClick={() => act(o.id, "accept")}
                    className="inline-flex w-full items-center justify-center gap-1 rounded-lg bg-brand-500/30 px-3 py-2.5 text-xs text-brand-100 hover:bg-brand-500/40 disabled:opacity-50 sm:w-auto sm:py-2"
                  >
                    <Check className="h-3.5 w-3.5" />
                    پذیرش
                  </button>
                  <button
                    type="button"
                    disabled={busy === o.id}
                    onClick={() => act(o.id, "reject")}
                    className="inline-flex w-full items-center justify-center gap-1 rounded-lg border border-red-400/30 px-3 py-2.5 text-xs text-red-200 hover:bg-red-500/10 disabled:opacity-50 sm:w-auto sm:py-2"
                  >
                    <X className="h-3.5 w-3.5" />
                    رد
                  </button>
                </div>
              ) : (o.status || "") === "accepted" && !deals?.[o.id] ? (
                <Link href={`/dashboard/deals/${o.id}`} className="text-xs text-brand-200 underline">
                  ادامه تأیید نهایی
                </Link>
              ) : null}
            </div>
            {(o.status || "") === "accepted" && deals?.[o.id] && (
              <DealGatePanel
                offerId={o.id}
                deal={deals[o.id]}
                token={token}
                onChange={() => onDealChange?.()}
                compact
              />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function offerStatusLabel(st?: string): string {
  return STATUS_FA[(st || "pending").toLowerCase()] || st || "—";
}
