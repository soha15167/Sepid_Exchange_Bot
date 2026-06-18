"use client";

import { useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Send } from "lucide-react";
import { apiFetch, fmtNum } from "@/lib/api";
import { EURO_FLOW_HINTS } from "@/lib/flowHints";
import { useWizardEnterKey, wizardTextareaEnter } from "@/lib/useWizardEnterKey";
import { PaymentMethodPicker } from "@/components/PaymentMethodPicker";
import { WizardStepHint } from "@/components/WizardStepHint";
import { InstantTransferPicker } from "@/components/InstantTransferPicker";
import { ChannelMembershipBanner } from "@/components/ChannelMembershipBanner";

type StepId =
  | "methods"
  | "country"
  | "instant"
  | "amount"
  | "rate"
  | "description"
  | "preview";

type Preview = {
  owner_name?: string;
  advert_type?: string;
  methods_label?: string;
  methods_display?: string;
  euro_amount?: number;
  rate_toman?: number;
  total_toman?: number;
  fee_eur?: string;
  country_label?: string;
  instant_transfer?: string;
  description?: string;
};

type Props = {
  operation: "خرید" | "فروش";
  token: string | null;
  onDone: (msg: string) => void;
};

export function EuroAdvertWizard({ operation, token, onDone }: Props) {
  const [step, setStep] = useState<StepId>("methods");
  const [methods, setMethods] = useState<string[]>([]);
  const [country, setCountry] = useState("");
  const [instant, setInstant] = useState("unknown");
  const [amount, setAmount] = useState("");
  const [rate, setRate] = useState("");
  const [description, setDescription] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const steps = useMemo((): StepId[] => {
    const s: StepId[] = ["methods", "country"];
    if (operation === "فروش") s.push("instant");
    s.push("amount", "rate", "description", "preview");
    return s;
  }, [operation]);

  const stepIndex = steps.indexOf(step);
  const progress = ((stepIndex + 1) / steps.length) * 100;

  function goNext() {
    setErr("");
    if (step === "methods" && methods.length === 0) {
      setErr("حداقل یک روش انتخاب کنید: IBAN / PayPal / Wise / Revolut.");
      return;
    }
    if (step === "country" && country.trim().length < 2) {
      setErr("❌ لطفاً نام کشور را وارد کنید.");
      return;
    }
    if (step === "amount" && !Number(amount.replace(/\D/g, ""))) {
      setErr("❌ لطفاً فقط عدد صحیح وارد کنید. مثال: 1200");
      return;
    }
    if (step === "rate" && !Number(rate.replace(/\D/g, ""))) {
      setErr("❌ لطفاً فقط عدد صحیح وارد کنید. مثال: 98000");
      return;
    }
    if (step === "description" && description.trim().length < 2) {
      setErr("توضیحات را وارد کنید (یا بنویسید: ندارم).");
      return;
    }
    const i = steps.indexOf(step);
    if (i < steps.length - 1) {
      const next = steps[i + 1];
      if (next === "preview") loadPreview();
      else setStep(next);
    }
  }

  function goBack() {
    const i = steps.indexOf(step);
    if (i > 0) setStep(steps[i - 1]);
  }

  async function loadPreview() {
    if (!token) return;
    setBusy(true);
    setErr("");
    try {
      const body = buildBody();
      const res = await apiFetch<{ preview: Preview }>(
        "/api/adverts/preview",
        { method: "POST", body: JSON.stringify(body) },
        token,
      );
      setPreview(res.preview);
      setStep("preview");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  function buildBody() {
    return {
      operation,
      euro_amount: Number(amount.replace(/\D/g, "")),
      rate_toman: Number(rate.replace(/\D/g, "")),
      description: description.trim(),
      methods,
      account_country: country.trim(),
      instant_transfer: operation === "فروش" ? instant : null,
    };
  }

  async function submit() {
    if (!token) return;
    setBusy(true);
    setErr("");
    try {
      const res = await apiFetch<{ advert: { id: number } }>(
        "/api/adverts",
        { method: "POST", body: JSON.stringify(buildBody()) },
        token,
      );
      onDone(`آگهی #${res.advert.id} در کانال منتشر شد.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  function handleEnter() {
    if (busy) return;
    if (step === "preview") submit();
    else goNext();
  }

  useWizardEnterKey(handleEnter, { disabled: busy });

  return (
    <div className="glass space-y-5 p-4 sm:space-y-6 sm:p-6 lg:p-8">
      <ChannelMembershipBanner token={token} />
      <div>
        <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-white/10">
          <div className="h-full bg-brand-500 transition-all" style={{ width: `${progress}%` }} />
        </div>
        <p className="text-xs text-white/40">
          مرحله {stepIndex + 1} از {steps.length} — {operation === "خرید" ? "خرید یورو" : "فروش یورو"}
        </p>
      </div>

      {step === "methods" && (
        <PaymentMethodPicker operation={operation} selected={methods} onChange={setMethods} />
      )}

      {step === "country" && (
        <div>
          <WizardStepHint
            title={EURO_FLOW_HINTS.country.title}
            body={EURO_FLOW_HINTS.country.body}
            example={EURO_FLOW_HINTS.country.example}
          />
          <input
            className="input-field"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="مثال: آلمان"
          />
        </div>
      )}

      {step === "instant" && (
        <InstantTransferPicker value={instant} onChange={setInstant} />
      )}

      {step === "amount" && (
        <div>
          <WizardStepHint
            title={EURO_FLOW_HINTS.amount.title}
            body={EURO_FLOW_HINTS.amount.body}
            example={EURO_FLOW_HINTS.amount.example}
          />
          <input
            className="input-field"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="1200"
          />
        </div>
      )}

      {step === "rate" && (
        <div>
          <WizardStepHint
            title={EURO_FLOW_HINTS.rate.title}
            body={EURO_FLOW_HINTS.rate.body}
            example={EURO_FLOW_HINTS.rate.example}
          />
          <input
            className="input-field"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            placeholder="190000"
          />
        </div>
      )}

      {step === "description" && (
        <div>
          <WizardStepHint
            title={EURO_FLOW_HINTS.description.title}
            body={EURO_FLOW_HINTS.description.body}
            example={EURO_FLOW_HINTS.description.example}
          />
          <textarea
            className="input-field min-h-[120px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => wizardTextareaEnter(e, handleEnter)}
            placeholder="ندارم"
          />
        </div>
      )}

      {step === "preview" && preview && (
        <div>
          <WizardStepHint
            title={EURO_FLOW_HINTS.preview.title}
            body={EURO_FLOW_HINTS.preview.body}
          />
          <div className="rounded-xl border border-brand-400/20 bg-brand-500/5 p-5 text-sm leading-7">
            <p>👤 {preview.owner_name}</p>
            <p>🏷️ {preview.advert_type}</p>
            <p>
              💳 {preview.methods_label}: {preview.methods_display}
            </p>
            <p>💶 {fmtNum(preview.euro_amount)} یورو</p>
            <p>💰 {fmtNum(preview.rate_toman)} تومان</p>
            <p>🧾 کارمزد: {preview.fee_eur}</p>
            {preview.country_label && <p>{preview.country_label}</p>}
            {preview.instant_transfer && <p>⚡ واریز آنی: {preview.instant_transfer}</p>}
            <p className="mt-2">📄 {preview.description}</p>
          </div>
        </div>
      )}

      {err && <p className="text-sm text-red-300">{err}</p>}

      <div className="wizard-actions">
        {step !== "methods" && step !== "preview" && (
          <button type="button" onClick={goBack} className="btn-ghost inline-flex gap-2">
            <ArrowRight className="h-4 w-4" />
            قبلی
          </button>
        )}
        {step !== "preview" ? (
          <button type="button" onClick={goNext} disabled={busy} className="btn-primary inline-flex gap-2">
            بعدی
            <ArrowLeft className="h-4 w-4" />
          </button>
        ) : (
          <button type="button" onClick={submit} disabled={busy} className="btn-primary inline-flex gap-2">
            <Send className="h-4 w-4" />
            ✅ تایید آگهی
          </button>
        )}
      </div>
    </div>
  );
}
