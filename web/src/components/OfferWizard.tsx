"use client";

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowLeft, ArrowRight, Check, Send } from "lucide-react";
import { apiFetch, fmtNum } from "@/lib/api";
import { useWizardEnterKey, wizardTextareaEnter } from "@/lib/useWizardEnterKey";

export type OfferFlowConfig = {
  advert_id: number;
  advert_euro_amount: number;
  advert_rate_toman: number;
  skips_toman_rate: boolean;
  requires_account_country: boolean;
  account_country_kind: "bank" | "recipient" | null;
  account_country_label: string;
  account_country_hint: string;
  is_exchange: boolean;
  blocked_reason: string | null;
  has_pending: boolean;
};

type StepId = "gate" | "euro" | "rate" | "country" | "description" | "preview";

type Props = {
  advertId: number;
  token: string | null;
  onDone: (msg: string) => void;
  onNeedAuth: () => void;
};

export function OfferWizard({ advertId, token, onDone, onNeedAuth }: Props) {
  const [cfg, setCfg] = useState<OfferFlowConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [mode, setMode] = useState<"agree" | "custom" | null>(null);
  const [step, setStep] = useState<StepId>("gate");
  const [euroAmount, setEuroAmount] = useState("");
  const [rate, setRate] = useState("");
  const [country, setCountry] = useState("");
  const [description, setDescription] = useState("");

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    apiFetch<OfferFlowConfig>(`/api/adverts/${advertId}/offer-flow`, {}, token)
      .then(setCfg)
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [advertId, token]);

  const steps = useMemo((): StepId[] => {
    if (!cfg || !mode) return ["gate"];
    const s: StepId[] = ["gate"];
    if (mode === "custom") s.push("euro");
    if (!cfg.skips_toman_rate) s.push("rate");
    if (cfg.requires_account_country) s.push("country");
    s.push("description", "preview");
    return s;
  }, [cfg, mode]);

  const stepIndex = steps.indexOf(step);
  const progress = steps.length > 1 ? ((stepIndex + 1) / steps.length) * 100 : 0;

  function goNext() {
    const i = steps.indexOf(step);
    if (i >= 0 && i < steps.length - 1) setStep(steps[i + 1]);
  }

  function goBack() {
    const i = steps.indexOf(step);
    if (i > 0) setStep(steps[i - 1]);
  }

  function pickMode(m: "agree" | "custom") {
    setMode(m);
    if (m === "agree") {
      if (!cfg?.skips_toman_rate) setStep("rate");
      else if (cfg?.requires_account_country) setStep("country");
      else setStep("description");
    } else {
      setStep("euro");
    }
  }

  async function submit() {
    if (!token || !cfg || !mode) {
      onNeedAuth();
      return;
    }
    setSubmitting(true);
    setErr("");
    try {
      const pe =
        mode === "custom"
          ? Number(String(euroAmount).replace(/\D/g, "")) || undefined
          : undefined;
      const body = {
        mode,
        rate_toman: cfg.skips_toman_rate ? 0 : Number(String(rate).replace(/\D/g, "")),
        description: description.trim(),
        proposed_euro_amount: pe,
        proposer_account_country: cfg.requires_account_country ? country.trim() : null,
      };
      const res = await apiFetch<{ seq: number; offer_id: number }>(
        `/api/adverts/${advertId}/offers`,
        { method: "POST", body: JSON.stringify(body) },
        token,
      );
      onDone(`پیشنهاد #${res.seq} ثبت شد — صاحب آگهی مطلع می‌شود.`);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setSubmitting(false);
    }
  }

  function handleEnter() {
    if (submitting || loading || !cfg) return;
    if (step === "preview") {
      submit();
      return;
    }
    if (step === "gate") return;
    if (step === "euro" && !euroAmount.trim()) return;
    if (step === "rate" && !String(rate).replace(/\D/g, "")) return;
    if (step === "country" && country.trim().length < 2) return;
    if (step === "description" && description.trim().length < 2) return;
    goNext();
  }

  useWizardEnterKey(handleEnter, { disabled: submitting || loading || !cfg });

  if (!token) {
    return (
      <div className="glass p-6 text-center">
        <p className="text-white/70">برای پیشنهاد دادن وارد حساب شوید.</p>
        <button type="button" onClick={onNeedAuth} className="btn-primary mt-4">
          ورود
        </button>
      </div>
    );
  }

  if (loading) return <div className="h-48 animate-pulse rounded-2xl bg-white/5" />;
  if (!cfg) return <p className="text-red-300">{err || "خطا"}</p>;

  if (cfg.blocked_reason) {
    return (
      <div className="rounded-xl border border-amber-400/20 bg-amber-500/10 p-5 text-amber-100">
        {cfg.blocked_reason}
      </div>
    );
  }

  if (cfg.has_pending) {
    return (
      <div className="glass p-6 text-center">
        <p className="text-brand-100">شما یک پیشنهاد در انتظار روی این آگهی دارید.</p>
        <p className="mt-2 text-sm text-white/50">
          تا پاسخ صاحب آگهی، پیشنهاد جدید ثبت نمی‌شود. وضعیت را در «پیشنهادهای من» ببینید.
        </p>
        <a href="/dashboard/offers" className="btn-primary mt-4 inline-block text-sm">
          پیشنهادهای من
        </a>
      </div>
    );
  }

  const effectiveEuro =
    mode === "custom"
      ? Number(String(euroAmount).replace(/\D/g, "")) || cfg.advert_euro_amount
      : cfg.advert_euro_amount;

  return (
    <div className="glass overflow-hidden">
      <div className="border-b border-white/5 bg-brand-500/5 px-5 py-4">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <Send className="h-5 w-5 text-brand-400" />
          ثبت پیشنهاد
        </h2>
        <p className="mt-1 text-xs text-white/45">همان مراحل ربات — مقدار، نرخ، کشور، توضیحات</p>
        {mode && (
          <div className="mt-4 h-1 overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-brand-500 transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </div>

      <div className="space-y-5 p-5 sm:p-6">
        {step === "gate" && (
          <>
            <p className="text-sm leading-7 text-white/70">
              آگهی:{" "}
              <strong>{fmtNum(cfg.advert_euro_amount)} €</strong>
              {!cfg.skips_toman_rate && (
                <>
                  {" "}
                  · نرخ <strong>{fmtNum(cfg.advert_rate_toman)}</strong> تومان
                </>
              )}
            </p>
            <p className="text-sm text-white/50">آیا با شرایط و مقدار آگهی موافقید؟</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => pickMode("agree")}
                className="rounded-xl border border-brand-400/30 bg-brand-500/10 p-4 text-start transition hover:bg-brand-500/20"
              >
                <p className="font-semibold text-brand-100">✅ با شرایط و مقدار موافقم</p>
                <p className="mt-1 text-xs text-white/50">مستقیم نرخ و توضیحات</p>
              </button>
              <button
                type="button"
                onClick={() => pickMode("custom")}
                className="rounded-xl border border-white/15 bg-white/5 p-4 text-start transition hover:bg-white/10"
              >
                <p className="font-semibold">💶 مقدار / شرایط جدید</p>
                <p className="mt-1 text-xs text-white/50">اول مقدار یورو، بعد نرخ</p>
              </button>
            </div>
          </>
        )}

        {step === "euro" && (
          <>
            <label className="label-text">مقدار یورو پیشنهادی</label>
            <p className="mb-2 text-xs text-white/45">
              مقدار در آگهی: {fmtNum(cfg.advert_euro_amount)} € — مثال: 900
            </p>
            <input
              className="input-field"
              dir="ltr"
              inputMode="numeric"
              placeholder={String(cfg.advert_euro_amount)}
              value={euroAmount}
              onChange={(e) => setEuroAmount(e.target.value)}
            />
            <WizardNav onBack={goBack} onNext={goNext} nextDisabled={!euroAmount.trim()} />
          </>
        )}

        {step === "rate" && (
          <>
            <label className="label-text">نرخ پیشنهادی (تومان)</label>
            <p className="mb-2 text-xs text-white/45">
              نرخ آگهی: {fmtNum(cfg.advert_rate_toman)} — فقط عدد، مثال: 210000
            </p>
            <input
              className="input-field"
              dir="ltr"
              inputMode="numeric"
              placeholder={String(cfg.advert_rate_toman || "")}
              value={rate}
              onChange={(e) => setRate(e.target.value)}
            />
            <WizardNav
              onBack={goBack}
              onNext={goNext}
              nextDisabled={!String(rate).replace(/\D/g, "")}
            />
          </>
        )}

        {step === "country" && (
          <>
            <label className="label-text">{cfg.account_country_label || "کشور حساب"}</label>
            <p className="mb-2 text-xs text-white/45">{cfg.account_country_hint}</p>
            <input
              className="input-field"
              placeholder="مثال: آلمان"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
            />
            <WizardNav onBack={goBack} onNext={goNext} nextDisabled={country.trim().length < 2} />
          </>
        )}

        {step === "description" && (
          <>
            <label className="label-text">توضیحات پیشنهاد</label>
            <p className="mb-2 text-xs text-white/45">
              {cfg.is_exchange
                ? "شرایط معاوضه، زمان هماهنگی و …"
                : "شرایط پرداخت، زمان و …"}
            </p>
            <textarea
              className="input-field min-h-[120px]"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onKeyDown={(e) => wizardTextareaEnter(e, handleEnter)}
            />
            <WizardNav
              onBack={goBack}
              onNext={goNext}
              nextDisabled={description.trim().length < 2}
              nextLabel="پیش‌نمایش"
            />
          </>
        )}

        {step === "preview" && mode && (
          <>
            <div className="rounded-xl border border-white/10 bg-ink-900/50 p-4 text-sm leading-8">
              <p className="mb-3 font-bold text-brand-200">
                {mode === "custom" ? "پیش‌نمایش (مقدار/شرایط جدید)" : "پیش‌نمایش پیشنهاد شما"}
              </p>
              <p>
                🆔 آگهی: <strong>{cfg.advert_id}</strong>
              </p>
              {mode === "custom" && (
                <p>
                  💶 مقدار پیشنهادی: <strong>{fmtNum(effectiveEuro)}</strong> یورو
                  {effectiveEuro !== cfg.advert_euro_amount && (
                    <span className="text-brand-300"> (متفاوت از آگهی)</span>
                  )}
                </p>
              )}
              {!cfg.skips_toman_rate && (
                <p>
                  💰 نرخ: <strong>{fmtNum(Number(String(rate).replace(/\D/g, "")))}</strong> تومان
                </p>
              )}
              {cfg.is_exchange && (
                <p className="text-white/60">💱 معاوضه یورو (بدون نرخ تومان)</p>
              )}
              {country && (
                <p>
                  🌍 {cfg.account_country_label}: <strong>{country}</strong>
                </p>
              )}
              <p className="mt-2 whitespace-pre-wrap">
                📝 {description}
              </p>
            </div>
            {err && <p className="text-sm text-red-300">{err}</p>}
            <div className="wizard-actions">
              <button type="button" onClick={goBack} className="btn-ghost gap-2">
                <ArrowRight className="h-4 w-4" />
                ویرایش
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={submit}
                className="btn-primary gap-2"
              >
                <Check className="h-4 w-4" />
                {submitting ? "در حال ارسال…" : "تأیید و ارسال"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function WizardNav({
  onBack,
  onNext,
  nextDisabled,
  nextLabel = "ادامه",
}: {
  onBack: () => void;
  onNext: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
}) {
  return (
    <div className="wizard-actions mt-4">
      <button type="button" onClick={onBack} className="btn-ghost gap-2">
        <ArrowRight className="h-4 w-4" />
        قبلی
      </button>
      <button
        type="button"
        disabled={nextDisabled}
        onClick={onNext}
        className={clsx("btn-primary gap-2", nextDisabled && "opacity-40")}
      >
        {nextLabel}
        <ArrowLeft className="h-4 w-4" />
      </button>
    </div>
  );
}
