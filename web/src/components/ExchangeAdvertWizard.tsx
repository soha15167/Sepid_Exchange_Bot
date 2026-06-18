"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowLeft, ArrowRight, Send } from "lucide-react";
import { apiFetch, fmtNum } from "@/lib/api";
import { EXCHANGE_DELIVERY_OPTIONS, EXCHANGE_FLOW_HINTS } from "@/lib/flowHints";
import { useWizardEnterKey, wizardTextareaEnter } from "@/lib/useWizardEnterKey";
import { WizardStepHint } from "@/components/WizardStepHint";
import { InstantTransferPicker } from "@/components/InstantTransferPicker";
import { ChannelMembershipBanner } from "@/components/ChannelMembershipBanner";

type StepId =
  | "delivery"
  | "instant"
  | "amount"
  | "country"
  | "city_int"
  | "city_ir"
  | "description"
  | "preview";

type Preview = {
  owner_name?: string;
  side_label?: string;
  exchange_method?: string;
  delivery_label?: string;
  euro_amount?: number;
  fee_eur?: string;
  account_country?: string;
  city_ir?: string;
  city_int?: string;
  instant_transfer?: string;
  description?: string;
};

type Props = {
  side: "خرید" | "فروش";
  token: string | null;
  onDone: (msg: string) => void;
};

export function ExchangeAdvertWizard({ side, token, onDone }: Props) {
  const [step, setStep] = useState<StepId>("delivery");
  const [delivery, setDelivery] = useState<"transfer" | "in_person" | "">("");
  const [instant, setInstant] = useState("unknown");
  const [amount, setAmount] = useState("");
  const [country, setCountry] = useState("");
  const [cityInt, setCityInt] = useState("");
  const [cityIr, setCityIr] = useState("");
  const [description, setDescription] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const steps = useMemo((): StepId[] => {
    const s: StepId[] = ["delivery"];
    if (side === "فروش" && delivery === "transfer") s.push("instant");
    s.push("amount", "country");
    if (delivery === "in_person") s.push("city_int");
    s.push("city_ir", "description", "preview");
    return s;
  }, [side, delivery]);

  const stepIndex = steps.indexOf(step);
  const progress = stepIndex >= 0 ? ((stepIndex + 1) / steps.length) * 100 : 0;

  const deliveryHint = side === "خرید" ? EXCHANGE_FLOW_HINTS.delivery_buy : EXCHANGE_FLOW_HINTS.delivery_sell;
  const deliveryOptions = side === "خرید" ? EXCHANGE_DELIVERY_OPTIONS.buy : EXCHANGE_DELIVERY_OPTIONS.sell;

  function goNext() {
    setErr("");
    if (step === "delivery" && !delivery) {
      setErr("روش دریافت/تحویل را انتخاب کنید.");
      return;
    }
    if (step === "amount" && !Number(amount.replace(/\D/g, ""))) {
      setErr("❌ لطفاً عدد صحیح وارد کنید.");
      return;
    }
    if (step === "country" && country.trim().length < 2) {
      setErr("❌ لطفاً نام کشور را وارد کنید.");
      return;
    }
    if (step === "city_int" && cityInt.trim().length < 2) {
      setErr("❌ لطفاً نام شهر خارج را وارد کنید.");
      return;
    }
    if (step === "city_ir" && cityIr.trim().length < 2) {
      setErr("❌ لطفاً نام شهر ایران را وارد کنید.");
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

  function buildBody() {
    return {
      side,
      delivery,
      euro_amount: Number(amount.replace(/\D/g, "")),
      account_country: country.trim(),
      city_ir: cityIr.trim(),
      city_int: delivery === "in_person" ? cityInt.trim() : null,
      description: description.trim(),
      instant_transfer: side === "فروش" && delivery === "transfer" ? instant : null,
    };
  }

  async function loadPreview() {
    if (!token) return;
    setBusy(true);
    try {
      const res = await apiFetch<{ preview: Preview }>(
        "/api/adverts/exchange/preview",
        { method: "POST", body: JSON.stringify(buildBody()) },
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

  async function submit() {
    if (!token) return;
    setBusy(true);
    try {
      const res = await apiFetch<{ advert: { id: number } }>(
        "/api/adverts/exchange",
        { method: "POST", body: JSON.stringify(buildBody()) },
        token,
      );
      onDone(`آگهی معاوضه #${res.advert.id} منتشر شد.`);
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
          <div className="h-full bg-violet-500 transition-all" style={{ width: `${progress}%` }} />
        </div>
        <p className="text-xs text-white/40">
          معاوضه یورو — {side === "خرید" ? "خرید" : "فروش"} · مرحله {stepIndex + 1} از {steps.length}
        </p>
      </div>

      {step === "delivery" && (
        <div className="space-y-3">
          <WizardStepHint title={deliveryHint.title} body={deliveryHint.body} />
          {deliveryOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setDelivery(o.value)}
              className={clsx(
                "block w-full rounded-xl border px-4 py-3 text-start text-sm transition",
                delivery === o.value
                  ? "border-violet-400/40 bg-violet-500/20 text-violet-100"
                  : "border-white/10 text-white/70 hover:bg-white/5",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}

      {step === "instant" && (
        <InstantTransferPicker value={instant} onChange={setInstant} />
      )}

      {step === "amount" && (
        <div>
          <WizardStepHint
            title={EXCHANGE_FLOW_HINTS.amount.title}
            body={EXCHANGE_FLOW_HINTS.amount.body}
            example={EXCHANGE_FLOW_HINTS.amount.example}
          />
          <input className="input-field" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </div>
      )}

      {step === "country" && (
        <div>
          <WizardStepHint
            title={EXCHANGE_FLOW_HINTS.country.title}
            body={EXCHANGE_FLOW_HINTS.country.body}
          />
          <input className="input-field" value={country} onChange={(e) => setCountry(e.target.value)} />
        </div>
      )}

      {step === "city_int" && (
        <div>
          <WizardStepHint
            title={EXCHANGE_FLOW_HINTS.cityInt.title}
            body={EXCHANGE_FLOW_HINTS.cityInt.body}
          />
          <input className="input-field" value={cityInt} onChange={(e) => setCityInt(e.target.value)} />
        </div>
      )}

      {step === "city_ir" && (
        <div>
          <WizardStepHint
            title={EXCHANGE_FLOW_HINTS.cityIr.title}
            body={EXCHANGE_FLOW_HINTS.cityIr.body}
          />
          <input className="input-field" value={cityIr} onChange={(e) => setCityIr(e.target.value)} />
        </div>
      )}

      {step === "description" && (
        <div>
          <WizardStepHint
            title={EXCHANGE_FLOW_HINTS.description.title}
            body={EXCHANGE_FLOW_HINTS.description.body}
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
            title={EXCHANGE_FLOW_HINTS.preview.title}
            body={EXCHANGE_FLOW_HINTS.preview.body}
          />
          <div className="rounded-xl border border-violet-400/20 bg-violet-500/5 p-5 text-sm leading-7">
            <p>👤 {preview.owner_name}</p>
            <p>🏷️ {preview.side_label} · معاوضه یورو</p>
            <p>
              📦 {preview.delivery_label}: {preview.exchange_method}
            </p>
            <p>💶 {fmtNum(preview.euro_amount)} یورو</p>
            <p>🧾 کارمزد: {preview.fee_eur}</p>
            <p>🗺️ {preview.account_country}</p>
            {preview.city_int && <p>🌆 {preview.city_int}</p>}
            <p>🏙️ {preview.city_ir}</p>
            {preview.instant_transfer && <p>⚡ {preview.instant_transfer}</p>}
            <p className="mt-2">📄 {preview.description}</p>
          </div>
        </div>
      )}

      {err && <p className="text-sm text-red-300">{err}</p>}

      <div className="wizard-actions">
        {step !== "delivery" && step !== "preview" && (
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
