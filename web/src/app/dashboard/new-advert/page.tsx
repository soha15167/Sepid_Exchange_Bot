"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import { ArrowRight } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { EuroAdvertWizard } from "@/components/EuroAdvertWizard";
import { ExchangeAdvertWizard } from "@/components/ExchangeAdvertWizard";
import { PageHeader } from "@/components/ui/PageHeader";
import { Rocket } from "lucide-react";

type Kind = "euro_buy" | "euro_sell" | "exchange_buy" | "exchange_sell" | null;

const KINDS: { id: Kind; title: string; desc: string; accent: string }[] = [
  {
    id: "euro_sell",
    title: "فروش یورو",
    desc: "IBAN · PayPal · Wise · Revolut",
    accent: "hover:border-brand-400/40 hover:shadow-glow-sm",
  },
  {
    id: "euro_buy",
    title: "خرید یورو",
    desc: "IBAN · PayPal · Wise · Revolut",
    accent: "hover:border-accent-cyan/40 hover:shadow-glow-sm",
  },
  {
    id: "exchange_sell",
    title: "معاوضه — فروش",
    desc: "یورو به یورو · بدون نرخ تومان",
    accent: "hover:border-accent-violet/40",
  },
  {
    id: "exchange_buy",
    title: "معاوضه — خرید",
    desc: "یورو به یورو · بدون نرخ تومان",
    accent: "hover:border-accent-violet/40",
  },
];

export default function NewAdvertPage() {
  const { token, user, loading } = useAuth();
  const router = useRouter();
  const [kind, setKind] = useState<Kind>(null);
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!loading && !user) router.replace("/auth");
  }, [loading, user, router]);

  if (loading || !user) return <p className="text-white/50">...</p>;

  return (
    <div className="mx-auto w-full min-w-0 max-w-2xl space-y-6">
      <Link href="/dashboard" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        داشبورد
      </Link>

      {success && (
        <div className="rounded-xl border border-brand-400/30 bg-brand-500/10 p-4 text-brand-100">
          {success}
          <Link href="/dashboard" className="mt-2 block text-sm underline">
            بازگشت به داشبورد
          </Link>
        </div>
      )}

      {!kind && !success && (
        <div className="bento-card space-y-5 p-4 sm:p-6 lg:p-8">
          <PageHeader
            badge="درخواست خدمات"
            badgeIcon={Rocket}
            title="ثبت آگهی جدید"
            subtitle="مرحله‌به‌مرحله مثل ربات — با پیش‌نمایش قبل از انتشار در کانال"
            className="mb-0"
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {KINDS.map((k) => (
              <button
                key={k.id}
                type="button"
                onClick={() => setKind(k.id)}
                className={clsx(
                  "group rounded-xl border border-white/[0.08] bg-ink-950/50 p-5 text-start transition duration-300",
                  k.accent,
                )}
              >
                <p className="font-bold text-white">{k.title}</p>
                <p className="mt-1.5 text-xs leading-6 text-white/45">{k.desc}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === "euro_buy" && !success && (
        <EuroAdvertWizard
          operation="خرید"
          token={token}
          onDone={(msg) => {
            setSuccess(msg);
            setKind(null);
          }}
        />
      )}
      {kind === "euro_sell" && !success && (
        <EuroAdvertWizard
          operation="فروش"
          token={token}
          onDone={(msg) => {
            setSuccess(msg);
            setKind(null);
          }}
        />
      )}
      {kind === "exchange_buy" && !success && (
        <ExchangeAdvertWizard
          side="خرید"
          token={token}
          onDone={(msg) => {
            setSuccess(msg);
            setKind(null);
          }}
        />
      )}
      {kind === "exchange_sell" && !success && (
        <ExchangeAdvertWizard
          side="فروش"
          token={token}
          onDone={(msg) => {
            setSuccess(msg);
            setKind(null);
          }}
        />
      )}

      {kind && !success && (
        <button type="button" onClick={() => setKind(null)} className="btn-ghost w-full text-sm">
          تغییر نوع آگهی
        </button>
      )}
    </div>
  );
}
