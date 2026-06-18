"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Bot,
  ChevronDown,
  Globe,
  Layers,
  MessageCircle,
  Shield,
  Sparkles,
  Zap,
} from "lucide-react";
import { useState } from "react";
import clsx from "clsx";
import { useAuth } from "@/lib/auth";

const fadeUp = {
  initial: { opacity: 0, y: 24 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-60px" },
  transition: { duration: 0.55, ease: [0.22, 1, 0.36, 1] },
};

const TRUST = ["IBAN", "PayPal", "Wise", "Revolut", "معاوضه یورو", "Deal Gate", "کانال تلگرام"];

const FEATURES = [
  {
    icon: Layers,
    title: "یک دیتابیس، دو پلتفرم",
    desc: "آگهی از وب منتشر می‌شود؛ پیشنهاد از ربات یا وب — همه در یک جریان.",
    stat: "100%",
    statLabel: "هماهنگی با ربات",
  },
  {
    icon: Zap,
    title: "انتشار آنی در کانال",
    desc: "پس از تأیید، آگهی با همان قالب کانال Sepid Exchange منتشر می‌شود.",
    stat: "< ۳۰ث",
    statLabel: "تا انتشار",
  },
  {
    icon: Shield,
    title: "OTP + رمز امن",
    desc: "ورود با ایمیل/موبایل و رمز، یا کد پیامکی — حساب تلگرام قابل اتصال.",
    stat: "۲FA",
    statLabel: "احراز هویت",
  },
  {
    icon: MessageCircle,
    title: "پیشنهاد و مذاکره",
    desc: "پذیرش، رد، ویرایش نرخ — همان منطق ربات برای صاحب آگهی.",
    stat: "Live",
    statLabel: "پیشنهادها",
  },
];

const STEPS = [
  { day: "۱", title: "ثبت‌نام یا ورود", desc: "با موبایل، ایمیل و OTP — یا اتصال حساب تلگرام." },
  { day: "۲", title: "ثبت آگهی", desc: "خرید/فروش یورو یا معاوضه — مرحله‌به‌مرحله مثل ربات." },
  { day: "۳", title: "انتشار در کانال", desc: "پیش‌نمایش، تأیید، انتشار — پیشنهاد از هر دو پلتفرم." },
  { day: "۴", title: "معامله امن", desc: "Deal Gate، رسید، کارمزد شفاف — طبق قوانین کانال." },
];

const FAQ = [
  {
    q: "وب با ربات تلگرام چه فرقی دارد؟",
    a: "منطق معامله، قوانین، کارمزد و انتشار کانال یکسان است. وب برای ثبت آگهی و مدیریت راحت‌تر است؛ ربات برای پیشنهاد و Deal Gate کامل‌تر.",
  },
  {
    q: "روش‌های پرداخت چیست؟",
    a: "IBAN، PayPal، Wise و Revolut — چندانتخابی. معاوضه یورو مسیر جداگانه دارد.",
  },
  {
    q: "آیا باید در کانال عضو باشم؟",
    a: "بله — برای انتشار آگهی، عضویت در کانال Sepid Exchange الزامی است (مثل ربات).",
  },
  {
    q: "ورود چگونه است؟",
    a: "کاربر جدید: OTP و تکمیل پروفایل. کاربر قبلی: رمز عبور یا کد پیامکی.",
  },
];

export function HomeLanding() {
  const { user, loading } = useAuth();
  const [openFaq, setOpenFaq] = useState<number | null>(0);

  return (
    <div className="space-y-0">
      {/* Hero */}
      <section className="relative overflow-x-clip pb-14 pt-2 sm:pb-28 sm:pt-8">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_60%_at_50%_-10%,rgba(99,102,241,0.35),transparent)]" />
        <div className="mx-auto grid max-w-6xl items-center gap-8 sm:gap-12 lg:grid-cols-2 lg:gap-16">
          <motion.div
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
            className="space-y-8"
          >
            <span className="section-badge max-w-full break-words">
              <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent-cyan" />
              Sepid Exchange · صرافی یورو
            </span>
            <h1 className="text-3xl font-black leading-[1.2] tracking-tight sm:text-4xl sm:leading-[1.15] lg:text-[3.25rem]">
              <span className="text-white">آگهی از وب.</span>
              <br />
              <span className="text-gradient">معامله از هر جا.</span>
            </h1>
            <p className="max-w-lg text-base leading-8 text-white/55 sm:text-lg">
              پلتفرم وب Sepid Exchange — همان مراحل ربات، همان کانال، همان قوانین.
              بدون یادگیری دوباره.
            </p>
            <div className="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:gap-3">
              <Link href="/dashboard/new-advert" className="btn-primary w-full px-6 py-3.5 text-base sm:w-auto">
                شروع کنید
                <ArrowLeft className="h-4 w-4" />
              </Link>
              <Link href="/adverts" className="btn-ghost w-full px-6 py-3.5 text-base sm:w-auto">
                مشاهده آگهی‌ها
              </Link>
            </div>
            {!loading && !user && (
              <p className="text-xs text-white/35">
                حساب دارید؟{" "}
                <Link href="/auth" className="text-brand-300 underline-offset-2 hover:underline">
                  ورود
                </Link>
              </p>
            )}
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.92, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
            className="relative mx-auto w-full max-w-md lg:max-w-none"
          >
            <div className="hero-mockup animate-float p-4 sm:p-5">
              <div className="mb-3 flex items-center justify-between text-xs text-white/40">
                <span className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
                  sepid.exchange
                </span>
                <span>Live</span>
              </div>
              <div className="space-y-3 rounded-xl border border-white/10 bg-ink-950/80 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-brand-200">فروش یورو</span>
                  <span className="rounded-md bg-violet-500/20 px-2 py-0.5 text-xs text-violet-200">فعال</span>
                </div>
                <div className="grid grid-cols-1 gap-2 text-xs xs:grid-cols-2">
                  <div className="rounded-lg bg-white/[0.04] p-2.5">
                    <p className="text-white/40">مقدار</p>
                    <p className="mt-0.5 font-bold text-white">۱٬۲۰۰ €</p>
                  </div>
                  <div className="rounded-lg bg-white/[0.04] p-2.5">
                    <p className="text-white/40">نرخ</p>
                    <p className="mt-0.5 font-bold text-white">۱۹۰٬۰۰۰</p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {["IBAN", "Wise"].map((m) => (
                    <span key={m} className="rounded-md border border-brand-400/30 bg-brand-500/10 px-2 py-0.5 text-[10px] text-brand-100">
                      {m}
                    </span>
                  ))}
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                  <div className="h-full w-4/5 rounded-full bg-gradient-to-l from-brand-500 to-accent-cyan" />
                </div>
                <p className="text-[11px] text-white/35">مرحله ۵ از ۷ — نرخ (تومان)</p>
              </div>
              <div className="mt-3 flex gap-2">
                <div className="flex-1 rounded-lg border border-white/10 bg-ink-900/60 p-2 text-center text-[10px] text-white/50">
                  <Bot className="mx-auto mb-1 h-4 w-4 text-accent-violet" />
                  ربات
                </div>
                <div className="flex-1 rounded-lg border border-brand-400/30 bg-brand-500/10 p-2 text-center text-[10px] text-brand-100">
                  <Globe className="mx-auto mb-1 h-4 w-4" />
                  وب
                </div>
              </div>
            </div>
            <div className="pointer-events-none absolute -inset-2 -z-10 rounded-3xl bg-brand-500/20 blur-2xl sm:-inset-4 sm:blur-3xl" />
          </motion.div>
        </div>
      </section>

      {/* Trust marquee */}
      <section className="overflow-x-clip border-y border-white/[0.06] bg-ink-900/40 py-5">
        <p className="mb-4 text-center text-[11px] uppercase tracking-widest text-white/30">
          مورد اعتماد معامله‌گران Sepid Exchange
        </p>
        <div className="marquee-mask overflow-hidden">
          <div className="marquee-track flex gap-10 whitespace-nowrap">
            {[...TRUST, ...TRUST].map((t, i) => (
              <span key={i} className="text-sm font-medium text-white/25">
                {t}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-14 sm:py-20 lg:py-28">
        <div className="mx-auto max-w-6xl">
          <motion.div {...fadeUp} className="mb-10 max-w-2xl sm:mb-14">
            <span className="section-badge mb-4">یک پلتفرم · همه‌چیز</span>
            <h2 className="text-2xl font-black text-white sm:text-3xl lg:text-4xl">
              از ثبت آگهی تا
              <span className="text-gradient"> بستن معامله</span>
            </h2>
            <p className="mt-4 text-base leading-8 text-white/50">
              ویزارد مرحله‌ای، پیش‌نمایش کانال، پیشنهاد دوطرفه — طراحی شده برای سرعت و شفافیت.
            </p>
          </motion.div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((f, i) => (
              <motion.div
                key={f.title}
                {...fadeUp}
                transition={{ ...fadeUp.transition, delay: i * 0.08 }}
                className="bento-card group p-5 sm:p-6"
              >
                <f.icon className="mb-4 h-6 w-6 text-brand-300 transition group-hover:text-accent-cyan" />
                <p className="font-bold text-white">{f.title}</p>
                <p className="mt-2 text-sm leading-7 text-white/45">{f.desc}</p>
                <div className="mt-6 border-t border-white/[0.06] pt-4">
                  <p className="text-2xl font-black text-gradient">{f.stat}</p>
                  <p className="text-xs text-white/35">{f.statLabel}</p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Timeline */}
      <section className="relative py-14 sm:py-20 lg:py-28">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_100%,rgba(167,139,250,0.12),transparent)]" />
        <div className="relative mx-auto max-w-6xl">
          <motion.div {...fadeUp} className="mb-14 text-center">
            <span className="section-badge mb-4">مسیر شما</span>
            <h2 className="text-2xl font-black text-white sm:text-3xl lg:text-4xl">از ورود تا معامله</h2>
          </motion.div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <motion.div key={s.title} {...fadeUp} transition={{ delay: i * 0.1 }} className="relative">
                <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-brand-500/20 text-sm font-bold text-brand-200 ring-1 ring-brand-400/30">
                  {s.day}
                </span>
                <h3 className="font-bold text-white">{s.title}</h3>
                <p className="mt-2 text-sm leading-7 text-white/45">{s.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Bot parity */}
      <section className="py-16 sm:py-20">
        <div className="mx-auto max-w-6xl">
          <div className="glass overflow-hidden p-5 sm:p-8 lg:p-12">
            <div className="grid items-center gap-8 lg:grid-cols-2 lg:gap-10">
              <div>
                <span className="section-badge mb-4">هماهنگ با ربات</span>
                <h2 className="text-2xl font-black text-white sm:text-3xl">
                  همان منو. همان مراحل. بدون سردرگمی.
                </h2>
                <ul className="mt-6 space-y-3 text-sm text-white/55">
                  {[
                    "روش‌های IBAN / PayPal / Wise / Revolut",
                    "معاوضه یورو با مسیر جدا",
                    "قوانین و کارمزد از منبع ربات",
                    "ویرایش و حذف آگهی (وقتی قفل نیست)",
                  ].map((line) => (
                    <li key={line} className="flex items-start gap-2">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-cyan" />
                      {line}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2">
                {[
                  { label: "درخواست خدمات", href: "/dashboard/new-advert" },
                  { label: "پیشنهادهای من", href: "/dashboard/offers" },
                  { label: "قوانین کانال", href: "/rules" },
                  { label: "نرخ کارمزد", href: "/fees" },
                ].map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="rounded-xl border border-white/[0.08] bg-ink-800/50 p-4 text-sm font-medium text-white/80 transition hover:border-brand-400/30 hover:bg-brand-500/10 hover:text-white"
                  >
                    {item.label}
                    <ArrowLeft className="mt-3 h-4 w-4 text-white/30" />
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="pb-20 sm:pb-28">
        <div className="mx-auto max-w-3xl">
          <motion.div {...fadeUp} className="mb-10 text-center">
            <h2 className="text-2xl font-black text-white sm:text-3xl">سوالات متداول</h2>
          </motion.div>
          <div className="space-y-2">
            {FAQ.map((item, i) => (
              <div key={i} className="bento-card overflow-hidden">
                <button
                  type="button"
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  className="flex w-full items-start justify-between gap-3 p-4 text-start sm:items-center sm:gap-4 sm:p-5"
                >
                  <span className="min-w-0 flex-1 font-semibold leading-7 text-white">{item.q}</span>
                  <ChevronDown
                    className={clsx(
                      "h-5 w-5 shrink-0 text-white/40 transition",
                      openFaq === i && "rotate-180",
                    )}
                  />
                </button>
                {openFaq === i && (
                  <motion.p
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    className="border-t border-white/[0.06] px-5 pb-5 pt-3 text-sm leading-8 text-white/55"
                  >
                    {item.a}
                  </motion.p>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="pb-8">
        <div className="cta-band mx-auto max-w-6xl px-4 py-10 text-center sm:px-6 sm:py-16">
          <h2 className="text-2xl font-black text-white sm:text-3xl">
            آماده‌اید اولین آگهی را ثبت کنید؟
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-white/50">
            چند دقیقه تا انتشار در کانال — با همان کیفیت ربات.
          </p>
          <div className="mt-6 flex flex-col items-stretch gap-2.5 sm:mt-8 sm:flex-row sm:flex-wrap sm:justify-center sm:gap-3">
            <Link href={user ? "/dashboard/new-advert" : "/auth"} className="btn-primary px-8 py-3.5 sm:w-auto">
              {user ? "آگهی جدید" : "ورود و شروع"}
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <Link href="/rules" className="btn-ghost px-6 py-3.5 sm:w-auto">
              مطالعه قوانین
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
