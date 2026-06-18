"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { apiFetch, type DealStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { DealGatePanel } from "@/components/DealGatePanel";

export default function DealPage({ params }: { params: { offerId: string } }) {
  const { token, user, loading } = useAuth();
  const router = useRouter();
  const [deal, setDeal] = useState<DealStatus | null>(null);
  const [err, setErr] = useState("");

  function reload() {
    if (!token) return;
    apiFetch<DealStatus>(`/api/deals/${params.offerId}`, {}, token)
      .then(setDeal)
      .catch((e) => setErr(e instanceof Error ? e.message : "خطا"));
  }

  useEffect(() => {
    if (!loading && !user) router.replace("/auth");
  }, [loading, user, router]);

  useEffect(() => {
    reload();
  }, [token, params.offerId]);

  if (loading || !user) return <p className="text-white/50">...</p>;

  return (
    <div className="mx-auto w-full min-w-0 max-w-2xl space-y-6">
      <Link href="/dashboard/offers" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        پیشنهادهای من
      </Link>

      <div className="glass p-4 sm:p-6">
        <h1 className="text-2xl font-bold">تأیید نهایی معامله</h1>
        <p className="mt-1 text-sm text-white/50">پیشنهاد #{params.offerId}</p>
      </div>

      {err && <p className="text-sm text-red-300">{err}</p>}

      {deal && (
        <>
          <div className="glass p-4 text-sm text-white/70">
            <p>آگهی #{deal.advert_id}</p>
            <p className="mt-1">نقش شما: {deal.party_role === "buyer" ? "خریدار یورو" : deal.party_role === "seller" ? "فروشنده یورو" : deal.role}</p>
            {deal.my_response && (
              <p className="mt-1">پاسخ شما: {deal.my_response === "yes" ? "تأیید" : "رد"}</p>
            )}
          </div>
          <DealGatePanel offerId={Number(params.offerId)} deal={deal} token={token} onChange={reload} />
        </>
      )}
    </div>
  );
}
