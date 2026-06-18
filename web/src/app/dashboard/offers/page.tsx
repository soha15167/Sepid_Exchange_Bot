"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Trash2 } from "lucide-react";
import { apiFetch, fmtNum, type DealStatus, type Offer } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { offerStatusLabel } from "@/components/IncomingOffersPanel";
import { DealGatePanel } from "@/components/DealGatePanel";
import { NegotiationPanel } from "@/components/NegotiationPanel";

export default function MyOffersPage() {
  const { user, token, loading } = useAuth();
  const router = useRouter();
  const [items, setItems] = useState<Offer[]>([]);
  const [deals, setDeals] = useState<Record<number, DealStatus>>({});
  const [editRate, setEditRate] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState("");

  function reload() {
    if (!token) return;
    apiFetch<{ items: Offer[] }>("/api/offers/mine", {}, token).then(async (d) => {
      setItems(d.items);
      const gateItems = d.items.filter((o) => o.has_deal_gate || o.status === "accepted");
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
    });
  }

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/auth");
      return;
    }
    reload();
  }, [user, token, loading, router]);

  async function withdraw(id: number) {
    if (!token || !confirm("پیشنهاد حذف شود؟")) return;
    setBusy(id);
    setErr("");
    try {
      await apiFetch(`/api/offers/${id}`, { method: "DELETE" }, token);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(null);
    }
  }

  async function saveRate(id: number) {
    if (!token) return;
    const raw = editRate[id]?.replace(/\D/g, "") || "";
    const rate = Number(raw);
    if (!rate) return;
    setBusy(id);
    setErr("");
    try {
      await apiFetch(
        `/api/offers/${id}/rate`,
        { method: "PATCH", body: JSON.stringify({ rate_toman: rate }) },
        token,
      );
      setEditRate((m) => ({ ...m, [id]: "" }));
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(null);
    }
  }

  if (loading || !user) return <p className="text-white/50">...</p>;

  return (
    <div className="mx-auto w-full min-w-0 max-w-3xl space-y-6">
      <Link href="/dashboard" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        داشبورد
      </Link>

      <div className="glass p-4 sm:p-6">
        <h1 className="text-2xl font-bold">پیشنهادهای من</h1>
        <p className="mt-1 text-sm text-white/50">همه پیشنهادهایی که روی آگهی‌های دیگران داده‌اید</p>
      </div>

      {err && <p className="text-sm text-red-300">{err}</p>}

      {items.length === 0 ? (
        <p className="text-center text-white/50">پیشنهادی ثبت نکرده‌اید.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((o) => {
            const deal = deals[o.id];
            const pending = (o.status || "pending") === "pending";
            return (
              <li key={o.id} className="glass p-4 sm:p-5">
                <div className="card-stack">
                  <div className="card-stack-body">
                    <p className="font-semibold">
                      #{o.seq} · آگهی{" "}
                      <Link href={`/adverts/${o.advert_id}`} className="text-brand-300 hover:underline">
                        #{o.advert_id}
                      </Link>
                    </p>
                    <p className="text-sm text-white/50">
                      {o.advert_operation} — {fmtNum(o.advert_euro_amount)} €
                    </p>
                    {!o.skips_toman_rate && (
                      <p className="text-sm">نرخ: {fmtNum(o.rate_toman)} تومان</p>
                    )}
                    <p className="mt-1 text-xs text-white/40">وضعیت: {offerStatusLabel(o.status)}</p>
                  </div>
                  <div className="flex w-full flex-col gap-2 sm:w-auto">
                    {pending && !o.skips_toman_rate && (
                      <div className="flex w-full gap-2 sm:w-auto">
                        <input
                          className="input-field min-w-0 flex-1 py-1.5 text-xs sm:w-28 sm:flex-none"
                          placeholder="نرخ جدید"
                          value={editRate[o.id] ?? ""}
                          onChange={(e) => setEditRate((m) => ({ ...m, [o.id]: e.target.value }))}
                        />
                        <button
                          type="button"
                          disabled={busy === o.id}
                          onClick={() => saveRate(o.id)}
                          className="btn-ghost py-1.5 text-xs"
                        >
                          ذخیره
                        </button>
                      </div>
                    )}
                    {pending && (
                      <button
                        type="button"
                        disabled={busy === o.id}
                        onClick={() => withdraw(o.id)}
                        className="btn-ghost inline-flex items-center gap-1 py-1.5 text-xs text-red-300"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        انصراف
                      </button>
                    )}
                  </div>
                </div>

                {deal?.gate?.active && (
                  <DealGatePanel
                    offerId={o.id}
                    deal={deal}
                    token={token}
                    onChange={reload}
                    compact
                  />
                )}
                {(o.status || "pending") === "pending" && (
                  <NegotiationPanel offerId={o.id} token={token} />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
