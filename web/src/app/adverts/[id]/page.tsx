"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { apiFetch, type Advert } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { AdvertChannelCard } from "@/components/AdvertChannelCard";
import { OfferWizard } from "@/components/OfferWizard";
import { OwnerOffersPanel } from "@/components/OwnerOffersPanel";

export default function AdvertDetailPage({ params }: { params: { id: string } }) {
  const { token, user } = useAuth();
  const router = useRouter();
  const [ad, setAd] = useState<Advert | null>(null);
  const [err, setErr] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    apiFetch<Advert>(`/api/adverts/${params.id}`, {}, token).then(setAd).catch((e) => setErr(e.message));
  }, [params.id, token]);

  if (!ad && !err) {
    return (
      <div className="mx-auto max-w-2xl">
        <div className="h-96 animate-pulse rounded-2xl bg-white/5" />
      </div>
    );
  }
  if (err && !ad) return <p className="text-center text-red-300">{err}</p>;

  const isOwner = Boolean(ad?.is_mine);

  return (
    <div className="mx-auto w-full min-w-0 max-w-2xl space-y-6">
      <Link href="/adverts" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        بازگشت به فید آگهی‌ها
      </Link>

      {ad && <AdvertChannelCard ad={ad} compact />}

      {ad?.locked && !isOwner && (
        <p className="rounded-xl border border-amber-400/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          این آگهی پیشنهاد فعال دارد و موقتاً قفل است.
        </p>
      )}

      {isOwner && !user && (
        <div className="rounded-xl border border-brand-400/25 bg-brand-500/10 p-4 text-sm text-brand-100">
          <p>برای مشاهده و مدیریت پیشنهادها وارد حساب خود شوید.</p>
          <Link href="/auth" className="btn-primary mt-3 inline-flex text-sm">
            ورود
          </Link>
        </div>
      )}

      {success && (
        <div className="rounded-xl border border-brand-400/30 bg-brand-500/10 p-4 text-brand-100">
          {success}
        </div>
      )}

      {!isOwner && (
        <OfferWizard
          advertId={Number(params.id)}
          token={token}
          onNeedAuth={() => router.push("/auth")}
          onDone={setSuccess}
        />
      )}

      {isOwner && user && (
        <>
          <div className="rounded-xl border border-brand-400/20 bg-brand-500/10 p-4 text-sm text-brand-100">
            این آگهی متعلق به شماست — پیشنهادهای دریافتی را پایین مدیریت کنید.
          </div>
          <OwnerOffersPanel advertId={Number(params.id)} token={token} />
        </>
      )}
    </div>
  );
}
