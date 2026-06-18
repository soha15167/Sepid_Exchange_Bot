"use client";

import clsx from "clsx";
import { ClipboardList } from "lucide-react";
import { fmtNum, type Advert, type PublicOffer } from "@/lib/api";

function offerLine(o: PublicOffer) {
  const parts: string[] = [fmtNum(o.seq)];
  if (o.skips_toman_rate) {
    parts.push("معاوضه یورو به یورو");
  } else if (o.rate_toman != null) {
    parts.push(`${fmtNum(o.rate_toman)} تومان`);
  }
  if (o.proposed_euro_amount != null) {
    parts.push(`${fmtNum(o.proposed_euro_amount)} €`);
  }
  parts.push(o.proposer_label);
  return parts.join(" — ");
}

export function AdvertPublicOffers({ ad }: { ad: Advert }) {
  const offers = ad.public_offers || [];
  if (offers.length === 0 && !ad.offers_completed) return null;

  return (
    <div className="relative mt-4 rounded-xl border border-white/10 bg-ink-950/50 p-4">
      <p className="mb-3 flex items-center gap-2 text-xs font-semibold text-white/55">
        <ClipboardList className="h-3.5 w-3.5 text-brand-300" />
        پیشنهادهای ارسال‌شده
      </p>
      {ad.offers_completed && (
        <p className="mb-3 text-sm text-brand-200">✅ این آگهی تکمیل شده است.</p>
      )}
      {offers.length === 0 ? (
        <p className="text-sm text-white/40">هنوز پیشنهادی ثبت نشده.</p>
      ) : (
        <ul className="space-y-2 text-sm leading-7 text-white/80">
          {offers.map((o) => (
            <li
              key={`${o.seq}-${o.proposer_label}-${o.status}`}
              className={clsx(
                "flex items-start gap-2",
                o.status === "rejected" && "text-white/35 line-through decoration-white/20",
                o.status === "accepted" && "text-brand-100",
              )}
            >
              <span className="shrink-0 text-white/45">
                {o.status === "rejected" ? "❌" : o.status === "accepted" ? "✅" : "•"}
              </span>
              <span>{offerLine(o)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
