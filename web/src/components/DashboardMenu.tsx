"use client";

import Link from "next/link";
import clsx from "clsx";
import { ArrowLeft, ClipboardList, Newspaper, Rocket, Scale, ScrollText, UserCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const ITEMS: { href: string; label: string; desc: string; icon: LucideIcon; accent: string }[] = [
  {
    href: "/dashboard/new-advert",
    label: "درخواست خدمات",
    desc: "ثبت آگهی خرید، فروش یا معاوضه",
    icon: Rocket,
    accent: "hover:border-brand-400/35 hover:bg-brand-500/10",
  },
  {
    href: "/dashboard/profile",
    label: "مشاهده پروفایل",
    desc: "مشخصات و وضعیت حساب",
    icon: UserCircle,
    accent: "hover:border-white/20 hover:bg-white/[0.04]",
  },
  {
    href: "/dashboard/offers",
    label: "پیشنهادهای من",
    desc: "پیشنهادهای ارسالی و وضعیت",
    icon: ClipboardList,
    accent: "hover:border-accent-cyan/35 hover:bg-accent-cyan/5",
  },
  {
    href: "/dashboard#my-adverts",
    label: "آگهی‌های من",
    desc: "ویرایش، حذف و پیشنهادهای ورودی",
    icon: Newspaper,
    accent: "hover:border-accent-violet/35 hover:bg-accent-violet/5",
  },
  {
    href: "/rules",
    label: "قوانین کانال",
    desc: "همان متن ربات",
    icon: ScrollText,
    accent: "hover:border-white/20 hover:bg-white/[0.04]",
  },
  {
    href: "/fees",
    label: "کارمزدها",
    desc: "جدول کارمزد یورو",
    icon: Scale,
    accent: "hover:border-white/20 hover:bg-white/[0.04]",
  },
];

export function DashboardMenu() {
  return (
    <section className="bento-card p-4 sm:p-6 lg:p-7">
      <h2 className="text-lg font-bold text-white">منوی خدمات</h2>
      <p className="mt-1 text-xs text-white/40">همان گزینه‌های منوی اصلی ربات</p>
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        {ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={clsx(
              "group flex items-start gap-4 rounded-xl border border-white/[0.06] bg-ink-950/40 p-4 transition duration-300",
              item.accent,
            )}
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/[0.05] ring-1 ring-white/10 transition group-hover:ring-brand-400/30">
              <item.icon className="h-5 w-5 text-brand-300" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-start justify-between gap-2 font-semibold text-white xs:items-center">
                <span className="min-w-0 break-anywhere">{item.label}</span>
                <ArrowLeft className="mt-0.5 h-4 w-4 shrink-0 text-white/20 transition group-hover:text-brand-300 xs:mt-0" />
              </span>
              <span className="mt-0.5 block text-xs leading-6 text-white/40">{item.desc}</span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
