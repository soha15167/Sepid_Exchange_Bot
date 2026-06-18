"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { apiFetch, type Advert } from "@/lib/api";
import { useAuth } from "@/lib/auth";

import { PaymentMethodPicker } from "@/components/PaymentMethodPicker";
import { wizardTextareaEnter } from "@/lib/useWizardEnterKey";

export default function EditAdvertPage({ params }: { params: { id: string } }) {
  const { token, user, loading } = useAuth();
  const router = useRouter();
  const [isExchange, setIsExchange] = useState(false);
  const [form, setForm] = useState({
    euro_amount: "",
    rate_toman: "",
    description: "",
    methods: [] as string[],
    account_country: "",
    city_ir: "",
    city_int: "",
    instant_transfer: "unknown",
    operation: "فروش",
  });
  const [locked, setLocked] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!token) return;
    apiFetch<Advert>(`/api/adverts/${params.id}`, {}, token).then((ad) => {
      if (!ad.is_mine) {
        router.replace("/dashboard");
        return;
      }
      setIsExchange(!!ad.is_exchange);
      setLocked(!!ad.locked);
      setForm({
        operation: ad.operation || "فروش",
        euro_amount: String(ad.euro_amount || ""),
        rate_toman: String(ad.rate_toman || ""),
        description: ad.description || "",
        methods: ad.methods || [],
        account_country: ad.account_country || "",
        city_ir: ad.city_ir || "",
        city_int: ad.city_int || "",
        instant_transfer:
          ad.instant_transfer === "دارم"
            ? "have"
            : ad.instant_transfer === "ندارم"
              ? "dont_have"
              : "unknown",
      });
    });
  }, [params.id, token, router]);

  useEffect(() => {
    if (!loading && !user) router.replace("/auth");
  }, [loading, user, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || locked) return;
    setErr("");
    try {
      const body: Record<string, unknown> = {
        euro_amount: Number(form.euro_amount.replace(/\D/g, "")),
        description: form.description,
        account_country: form.account_country,
      };
      if (!isExchange) {
        body.rate_toman = Number(form.rate_toman.replace(/\D/g, ""));
        body.methods = form.methods;
        body.instant_transfer = form.operation === "فروش" ? form.instant_transfer : null;
      } else {
        body.city_ir = form.city_ir;
        if (form.city_int.trim()) body.city_int = form.city_int;
      }
      await apiFetch(
        `/api/adverts/${params.id}`,
        {
          method: "PATCH",
          body: JSON.stringify(body),
        },
        token,
      );
      router.push("/dashboard");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "خطا");
    }
  }

  if (loading || !user) return <p className="text-white/50">...</p>;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Link href="/dashboard" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        داشبورد
      </Link>

      <form onSubmit={submit} className="glass space-y-5 p-6 sm:p-8">
        <h1 className="text-2xl font-bold">
          ویرایش آگهی #{params.id}
          {isExchange && <span className="ms-2 text-sm font-normal text-cyan-200">(معاوضه)</span>}
        </h1>
        {locked && (
          <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-200">
            این آگهی پیشنهاد فعال دارد و قابل ویرایش نیست.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label-text">مقدار یورو</label>
            <input
              className="input-field"
              disabled={locked}
              value={form.euro_amount}
              onChange={(e) => setForm({ ...form, euro_amount: e.target.value })}
              required
            />
          </div>
          {!isExchange && (
            <div>
              <label className="label-text">نرخ (تومان)</label>
              <input
                className="input-field"
                disabled={locked}
                value={form.rate_toman}
                onChange={(e) => setForm({ ...form, rate_toman: e.target.value })}
                required
              />
            </div>
          )}
        </div>

        <div>
          <label className="label-text">کشور حساب</label>
          <input
            className="input-field"
            disabled={locked}
            value={form.account_country}
            onChange={(e) => setForm({ ...form, account_country: e.target.value })}
            required
          />
        </div>

        {isExchange && (
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="label-text">شهر ایران</label>
              <input
                className="input-field"
                disabled={locked}
                value={form.city_ir}
                onChange={(e) => setForm({ ...form, city_ir: e.target.value })}
                required
              />
            </div>
            <div>
              <label className="label-text">شهر خارج (در صورت حضوری)</label>
              <input
                className="input-field"
                disabled={locked}
                value={form.city_int}
                onChange={(e) => setForm({ ...form, city_int: e.target.value })}
              />
            </div>
          </div>
        )}

        {!isExchange && form.operation === "فروش" && (
          <div>
            <label className="label-text">واریز آنی</label>
            <select
              className="input-field"
              disabled={locked}
              value={form.instant_transfer}
              onChange={(e) => setForm({ ...form, instant_transfer: e.target.value })}
            >
              <option value="have">دارم</option>
              <option value="dont_have">ندارم</option>
              <option value="unknown">اطلاعی ندارم</option>
            </select>
          </div>
        )}

        {!isExchange && (
          <div>
            <label className="label-text">
              {form.operation === "خرید" ? "روش‌های دریافت" : "روش‌های پرداخت"}
            </label>
            <PaymentMethodPicker
              operation={form.operation as "خرید" | "فروش"}
              selected={form.methods}
              onChange={(m) => setForm({ ...form, methods: m })}
              disabled={locked}
            />
          </div>
        )}

        <div>
          <label className="label-text">توضیحات</label>
          <textarea
            className="input-field min-h-[120px]"
            disabled={locked}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            onKeyDown={(e) =>
              wizardTextareaEnter(e, () => {
                if (!locked) e.currentTarget.form?.requestSubmit();
              })
            }
            required
          />
        </div>

        {err && <p className="text-sm text-red-300">{err}</p>}
        <button type="submit" disabled={locked} className="btn-primary w-full disabled:opacity-50">
          ذخیره تغییرات
        </button>
      </form>
    </div>
  );
}
