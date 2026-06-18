"use client";

import Link from "next/link";
import clsx from "clsx";
import {
  ArrowLeft,
  Banknote,
  Clock,
  ExternalLink,
  Globe2,
  Lock,
  MapPin,
  Send,
  Sparkles,
  User,
  Zap,
} from "lucide-react";
import { Advert, advertBadgeColor, fmtNum } from "@/lib/api";
import { AdvertPublicOffers } from "@/components/AdvertPublicOffers";

type Props = {
  ad: Advert;
  index?: number;
  compact?: boolean;
};

function ownerInitial(name?: string) {
  const n = (name || "?").trim();
  return n[0]?.toUpperCase() || "?";
}

export function AdvertChannelCard({ ad, index = 0, compact = false }: Props) {
  const badge = advertBadgeColor(ad.advert_type);
  const methods = ad.methods || [];

  return (
    <article
      className={clsx(
        "group relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.07] to-white/[0.02] shadow-glass backdrop-blur-xl transition hover:border-brand-400/25 hover:shadow-glow",
        compact ? "p-4" : "p-5 sm:p-6",
      )}
    >
      <div className="pointer-events-none absolute -end-8 -top-8 h-32 w-32 rounded-full bg-brand-500/10 blur-2xl transition group-hover:bg-brand-400/15" />

      {/* Header — like channel post title */}
      <div className="relative flex flex-col gap-3 border-b border-white/5 pb-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-500/15 text-sm font-bold text-brand-200 ring-1 ring-brand-400/20">
            {ownerInitial(ad.owner_name)}
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={clsx(
                  "inline-flex items-center gap-1 rounded-lg border bg-gradient-to-l px-2.5 py-1 text-xs font-semibold",
                  badge,
                )}
              >
                <Sparkles className="h-3 w-3" />
                {ad.advert_type}
              </span>
              <span className="text-xs font-medium text-brand-300/90">#{fmtNum(ad.id)}</span>
              {ad.locked && (
                <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-200">
                  <Lock className="h-3 w-3" />
                  پیشنهاد فعال
                </span>
              )}
            </div>
            <p className="mt-1.5 flex items-center gap-1.5 text-sm text-white/50">
              <User className="h-3.5 w-3.5" />
              {ad.owner_name}
            </p>
          </div>
        </div>
        {ad.created_at && !compact && (
          <span className="flex items-center gap-1 text-[11px] text-white/35">
            <Clock className="h-3 w-3" />
            {new Date(ad.created_at).toLocaleDateString("fa-IR")}
          </span>
        )}
      </div>

      {/* Key figures */}
      <div className="relative mt-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-white/5 bg-ink-900/40 p-3.5">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] text-white/40">
            <Banknote className="h-3.5 w-3.5" />
            مقدار یورو
          </p>
          <p className="text-lg font-bold tracking-tight text-white">
            {fmtNum(ad.euro_amount)}
            <span className="ms-1 text-sm font-normal text-white/50">€</span>
          </p>
        </div>
        {!ad.is_exchange && (
          <div className="rounded-xl border border-white/5 bg-ink-900/40 p-3.5">
            <p className="mb-1 text-[11px] text-white/40">نرخ تومان</p>
            <p className="text-lg font-bold tracking-tight text-white">{fmtNum(ad.rate_toman ?? 0)}</p>
          </div>
        )}
        <div className="rounded-xl border border-brand-500/15 bg-brand-500/5 p-3.5">
          <p className="mb-1 text-[11px] text-brand-200/70">کارمزد هر طرف</p>
          <p className="text-sm font-semibold text-brand-100">{ad.fee_eur || "—"}</p>
        </div>
      </div>

      {/* Methods */}
      {methods.length > 0 && (
        <div className="relative mt-4">
          <p className="mb-2 text-xs font-medium text-white/45">{ad.methods_label || "روش‌ها"}</p>
          <div className="flex flex-wrap gap-1.5">
            {methods.map((m) => (
              <span
                key={m}
                className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-white/75"
              >
                {m}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Location / country */}
      <div className="relative mt-4 space-y-2 text-sm">
        {ad.account_country && (
          <p className="flex items-start gap-2 text-white/65">
            <Globe2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-400/80" />
            <span>
              {ad.country_label ? `${ad.country_label}: ` : "کشور: "}
              <span className="text-white/85">{ad.account_country}</span>
            </span>
          </p>
        )}
        {ad.city_int && (
          <p className="flex items-center gap-2 text-white/65">
            <MapPin className="h-4 w-4 shrink-0 text-brand-400/80" />
            شهر خارج: <span className="text-white/85">{ad.city_int}</span>
          </p>
        )}
        {ad.city_ir && (
          <p className="flex items-center gap-2 text-white/65">
            <MapPin className="h-4 w-4 shrink-0 text-brand-400/80" />
            شهر ایران: <span className="text-white/85">{ad.city_ir}</span>
          </p>
        )}
        {ad.instant_transfer && (
          <p className="flex items-center gap-2 text-amber-200/90">
            <Zap className="h-4 w-4 shrink-0" />
            واریز آنی: {ad.instant_transfer}
          </p>
        )}
      </div>

      {/* Description — full text like channel */}
      <div className="relative mt-4 rounded-xl border border-white/5 bg-black/20 p-4">
        <p className="mb-1.5 text-[11px] font-medium text-white/40">توضیحات</p>
        <p className="whitespace-pre-wrap text-sm leading-7 text-white/80">{ad.description}</p>
      </div>

      <AdvertPublicOffers ad={ad} />

      {/* Actions */}
      <div className="relative mt-5 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {ad.is_mine ? (
          <Link
            href={`/adverts/${ad.id}#offers`}
            className="btn-primary w-full py-2.5 text-sm sm:w-auto sm:px-6"
          >
            مشاهده پیشنهادها
            <ArrowLeft className="h-4 w-4" />
          </Link>
        ) : (
          <Link
            href={`/adverts/${ad.id}`}
            className="btn-primary w-full py-2.5 text-sm sm:w-auto sm:px-6"
          >
            <Send className="h-4 w-4" />
            پیشنهاد به آگهی
            <ArrowLeft className="h-4 w-4" />
          </Link>
        )}
        {ad.channel_link && (
          <a
            href={ad.channel_link}
            target="_blank"
            rel="noreferrer"
            className="btn-ghost w-full gap-2 py-2.5 text-sm sm:w-auto"
          >
            <ExternalLink className="h-4 w-4" />
            کانال
          </a>
        )}
      </div>

      {!compact && index > 0 && (
        <div className="pointer-events-none absolute -top-3 end-6 hidden text-[10px] text-white/20 sm:block">
          ···
        </div>
      )}
    </article>
  );
}
