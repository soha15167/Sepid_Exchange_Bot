"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, X } from "lucide-react";
import { apiFetch, fmtNum, type DealStatus, type Offer } from "@/lib/api";
import { offerStatusLabel } from "@/components/IncomingOffersPanel";
import { DealGatePanel } from "@/components/DealGatePanel";
import { NegotiationPanel } from "@/components/NegotiationPanel";

type Props = {
  advertId: number;
  token: string | null;
};

export function OwnerOffersPanel({ advertId, token }: Props) {
  const [items, setItems] = useState<Offer[]>([]);
  const [deals, setDeals] = useState<Record<number, DealStatus>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState("");

  function reload() {
    if (!token) return;
    apiFetch<{ items: Offer[] }>(`/api/adverts/${advertId}/offers`, {}, token)
      .then(async (d) => {
        setItems(d.items);
        const gateItems = d.items.filter(
          (o) => (o.status || "") === "accepted" || o.has_deal_gate,
        );
        const ds: Record<number, DealStatus> = {};
        await Promise.all(
          gateItems.map(async (o) => {
            try {
              ds[o.id] = await apiFetch<DealStatus>(`/api/deals/${o.id}`, {}, token);
            } catch {
              /* ignore */
            }
          }),
        );
        setDeals(ds);
      })
      .catch((e) => setErr(e.message));
  }

  useEffect(() => {
    reload();
  }, [advertId, token]);

  async function act(id: number, action: "accept" | "reject") {
    if (!token) return;
    setBusy(id);
    setErr("");
    try {
      await apiFetch(`/api/offers/${id}/${action}`, { method: "POST" }, token);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(null);
    }
  }

  const pending = items.filter((o) => (o.status || "pending") === "pending");

  return (
    <section id="offers" className="glass scroll-mt-24 p-4 sm:p-6">
      <h2 className="mb-4 font-bold">پیشنهادهای این آگهی</h2>
      {err && <p className="mb-3 text-sm text-red-300">{err}</p>}
      {items.length === 0 ? (
        <p className="text-sm text-white/50">هنوز پیشنهادی دریافت نشده.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((o) => {
            const isPending = (o.status || "pending") === "pending";
            const isAccepted = (o.status || "") === "accepted";
            const deal = deals[o.id];
            return (
              <li key={o.id} className="rounded-xl border border-white/10 bg-white/5 p-4">
                <div className="card-stack">
                  <div className="card-stack-body">
                    <p className="font-semibold">
                      #{o.seq} · {o.proposer_name}
                    </p>
                    {!o.skips_toman_rate && (
                      <p className="text-sm">{fmtNum(o.rate_toman)} تومان</p>
                    )}
                    {o.proposed_euro_amount ? (
                      <p className="text-xs text-brand-200">
                        مقدار: {fmtNum(o.proposed_euro_amount)} €
                      </p>
                    ) : null}
                    {o.description && (
                      <p className="mt-1 text-sm text-white/70 break-anywhere">{o.description}</p>
                    )}
                    <p className="mt-1 text-xs text-white/40">{offerStatusLabel(o.status)}</p>
                  </div>
                  {isPending && (
                    <div className="panel-actions">
                      <button
                        type="button"
                        disabled={busy === o.id}
                        onClick={() => act(o.id, "accept")}
                        className="inline-flex items-center gap-1 rounded-lg bg-brand-500/30 px-3 py-2.5 text-xs text-brand-100 sm:py-2"
                      >
                        <Check className="h-3.5 w-3.5" />
                        پذیرش
                      </button>
                      <button
                        type="button"
                        disabled={busy === o.id}
                        onClick={() => act(o.id, "reject")}
                        className="inline-flex items-center gap-1 rounded-lg border border-red-400/30 px-3 py-2.5 text-xs text-red-200 sm:py-2"
                      >
                        <X className="h-3.5 w-3.5" />
                        رد
                      </button>
                    </div>
                  )}
                  {isAccepted && !deal && (
                    <Link href={`/dashboard/deals/${o.id}`} className="text-xs text-brand-200 underline">
                      تأیید نهایی
                    </Link>
                  )}
                </div>
                {isPending && (
                  <NegotiationPanel offerId={o.id} token={token} />
                )}
                {deal?.gate?.active && (
                  <DealGatePanel offerId={o.id} deal={deal} token={token} onChange={reload} compact />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {pending.length === 0 && items.length > 0 && (
        <p className="mt-3 text-xs text-white/40">پیشنهاد در انتظار تأیید ندارید.</p>
      )}
    </section>
  );
}
