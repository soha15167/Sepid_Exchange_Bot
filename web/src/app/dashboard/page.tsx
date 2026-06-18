"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trash2, Pencil, Lock } from "lucide-react";
import { apiFetch, fmtNum, type Advert, type DealStatus, type Offer } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { IncomingOffersPanel } from "@/components/IncomingOffersPanel";
import { DashboardMenu } from "@/components/DashboardMenu";
import { PageHeader } from "@/components/ui/PageHeader";
import { LayoutDashboard, Plus } from "lucide-react";

export default function DashboardPage() {
  const { user, token, loading } = useAuth();
  const router = useRouter();
  const [items, setItems] = useState<Advert[]>([]);
  const [incoming, setIncoming] = useState<Offer[]>([]);
  const [incomingDeals, setIncomingDeals] = useState<Record<number, DealStatus>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState("");

  function reload() {
    if (!token) return;
    apiFetch<{ items: Advert[] }>("/api/adverts/mine", {}, token).then((d) => setItems(d.items));
    apiFetch<{ items: Offer[] }>("/api/offers/incoming", {}, token).then(async (d) => {
      setIncoming(d.items);
      const accepted = d.items.filter((o) => (o.status || "") === "accepted" || o.has_deal_gate);
      const ds: Record<number, DealStatus> = {};
      await Promise.all(
        accepted.map(async (o) => {
          try {
            ds[o.id] = await apiFetch<DealStatus>(`/api/deals/${o.id}`, {}, token);
          } catch {
            /* ignore */
          }
        }),
      );
      setIncomingDeals(ds);
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

  async function deleteAdvert(id: number) {
    if (!token || !confirm("آگهی حذف شود؟")) return;
    setBusyId(id);
    setErr("");
    try {
      await apiFetch(`/api/adverts/${id}`, { method: "DELETE" }, token);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusyId(null);
    }
  }

  if (loading || !user) return <p className="text-white/50">...</p>;

  return (
    <div className="space-y-8">
      <PageHeader
        badge="پنل کاربری"
        badgeIcon={LayoutDashboard}
        title={`سلام، ${user.display_name}`}
        subtitle={
          user.has_telegram
            ? "حساب متصل به تلگرام — مدیریت آگهی و پیشنهادها"
            : "حساب وب — همان امکانات ربات در مرورگر"
        }
      >
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
          <Link href="/dashboard/offers" className="btn-ghost w-full text-sm sm:w-auto">
            پیشنهادهای من
          </Link>
          <Link href="/dashboard/new-advert" className="btn-primary inline-flex w-full gap-2 text-sm sm:w-auto">
            <Plus className="h-4 w-4" />
            آگهی جدید
          </Link>
        </div>
      </PageHeader>

      <DashboardMenu />

      <IncomingOffersPanel
        offers={incoming}
        token={token}
        onChange={reload}
        deals={incomingDeals}
        onDealChange={reload}
      />

      <section id="my-adverts" className="glass p-4 sm:p-6">
        <h2 className="mb-4 font-bold">آگهی‌های من</h2>
        {err && <p className="mb-3 text-sm text-red-300">{err}</p>}
        {items.length === 0 ? (
          <p className="text-white/50">هنوز آگهی ندارید.</p>
        ) : (
          <ul className="space-y-3">
            {items.map((ad) => (
              <li
                key={ad.id}
                className="card-stack rounded-xl border border-white/10 bg-white/5 p-4"
              >
                <div className="card-stack-body">
                  <p className="flex flex-wrap items-center gap-2 font-semibold">
                    #{ad.id} · {ad.operation}
                    {ad.locked && (
                      <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/20 px-2 py-0.5 text-xs text-amber-200">
                        <Lock className="h-3 w-3" />
                        قفل
                      </span>
                    )}
                  </p>
                  <p className="text-sm text-white/50">
                    {fmtNum(ad.euro_amount)} €
                    {!ad.is_exchange && <> — {fmtNum(ad.rate_toman)} تومان</>}
                  </p>
                </div>
                <div className="panel-actions">
                  <Link href={`/adverts/${ad.id}`} className="btn-ghost w-full py-2.5 text-xs sm:w-auto sm:py-2">
                    مشاهده
                  </Link>
                  {!ad.locked && (
                    <Link
                      href={`/dashboard/adverts/${ad.id}/edit`}
                      className="btn-ghost inline-flex w-full items-center justify-center gap-1 py-2.5 text-xs sm:w-auto sm:py-2"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      ویرایش
                    </Link>
                  )}
                  {!ad.locked && (
                    <button
                      type="button"
                      disabled={busyId === ad.id}
                      onClick={() => deleteAdvert(ad.id)}
                      className="btn-ghost inline-flex w-full items-center justify-center gap-1 py-2.5 text-xs text-red-300 sm:w-auto sm:py-2"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      حذف
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
