"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { normalizeLoginInput, phoneStartsWithPlus, formatPhone, ltrPhone } from "@/lib/format";

type Step = "login" | "password" | "otp_login" | "register" | "link";

export default function AuthPage() {
  const { user, loading: authLoading, setSession } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState<Step>("login");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [otp, setOtp] = useState("");
  const [devOtp, setDevOtp] = useState("");
  const [phoneMasked, setPhoneMasked] = useState("");
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [reg, setReg] = useState({
    full_name: "",
    last_name: "",
    display_name: "",
    email: "",
    address: "",
    phone_number: "",
    password: "",
    accept_terms: false,
  });

  useEffect(() => {
    if (!authLoading && user) router.replace("/dashboard");
  }, [authLoading, user, router]);

  async function handleLookup(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const trimmed = login.trim();
    if (trimmed && !trimmed.includes("@") && !phoneStartsWithPlus(trimmed)) {
      setError("شماره موبایل باید با + شروع شود. مثال: +989121234567");
      return;
    }
    setLoading(true);
    const loginNorm = normalizeLoginInput(trimmed);
    if (loginNorm !== login) setLogin(loginNorm);
    try {
      const data = await apiFetch<{
        status: string;
        profile?: Record<string, unknown>;
        message: string;
      }>("/api/auth/lookup", { method: "POST", body: JSON.stringify({ login: loginNorm }) });

      setProfile(data.profile || null);

      if (data.status === "existing_web_user") {
        setStep("password");
      } else if (data.status === "link_telegram_user") {
        await sendOtp("link", loginNorm);
        setStep("link");
      } else {
        await sendOtp("register", loginNorm);
        setReg((r) => ({ ...r, phone_number: loginNorm.startsWith("+") ? loginNorm : r.phone_number }));
        setStep("register");
      }
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  async function sendOtp(purpose: "login" | "register" | "link", loginOverride?: string) {
    setError("");
    setLoading(true);
    const loginNorm = normalizeLoginInput(loginOverride ?? login);
    try {
      const data = await apiFetch<{
        challenge_id: string;
        dev_otp?: string;
        phone_masked?: string;
      }>("/api/auth/otp/send", { method: "POST", body: JSON.stringify({ login: loginNorm, purpose }) });
      setChallengeId(data.challenge_id);
      if (data.dev_otp) setDevOtp(data.dev_otp);
      if (data.phone_masked) setPhoneMasked(String(data.phone_masked));
      if (purpose === "login") setStep("otp_login");
      else if (purpose === "link") setStep("link");
      else setStep("register");
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  async function handlePasswordLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await apiFetch<{ access_token: string; user: Parameters<typeof setSession>[1] }>(
        "/api/auth/login",
        { method: "POST", body: JSON.stringify({ login, password }) },
      );
      setSession(data.access_token, data.user);
      router.push("/dashboard");
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  async function handleOtpLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await apiFetch<{ access_token: string; user: Parameters<typeof setSession>[1] }>(
        "/api/auth/login-otp",
        { method: "POST", body: JSON.stringify({ challenge_id: challengeId, otp_code: otp }) },
      );
      setSession(data.access_token, data.user);
      router.push("/dashboard");
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  async function handleLink(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await apiFetch<{ access_token: string; user: Parameters<typeof setSession>[1] }>(
        "/api/auth/link-password",
        {
          method: "POST",
          body: JSON.stringify({ challenge_id: challengeId, otp_code: otp, password }),
        },
      );
      setSession(data.access_token, data.user);
      router.push("/dashboard");
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const phone = (reg.phone_number || login).trim();
    if (phone && !phoneStartsWithPlus(phone)) {
      setError("شماره موبایل باید با + شروع شود. مثال: +989121234567");
      return;
    }
    setLoading(true);
    try {
      const data = await apiFetch<{ access_token: string; user: Parameters<typeof setSession>[1] }>(
        "/api/auth/register-after-otp",
        {
          method: "POST",
          body: JSON.stringify({
            challenge_id: challengeId,
            otp_code: otp,
            ...reg,
            phone_number: normalizeLoginInput(reg.phone_number || login),
          }),
        },
      );
      setSession(data.access_token, data.user);
      router.push("/dashboard");
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setLoading(false);
    }
  }

  if (authLoading || user) {
    return <p className="text-center text-white/50">...</p>;
  }

  const pageTitle =
    step === "register" ? "ثبت‌نام" : step === "link" ? "فعال‌سازی حساب وب" : "ورود";

  const pageHint =
    step === "register"
      ? "کاربر جدید — کد پیامکی و تکمیل مشخصات"
      : step === "link"
        ? "حساب تلگرام — OTP و انتخاب رمز وب"
        : step === "otp_login"
          ? "کد پیامکی ارسال‌شده را وارد کنید"
          : step === "password"
            ? "با رمز عبور یا کد پیامکی وارد شوید"
            : "شماره با +989... یا ایمیل ثبت‌شده";

  return (
    <div className="mx-auto w-full max-w-lg min-w-0">
      <div className="bento-card overflow-hidden p-4 sm:p-6 lg:p-8">
        <span className="section-badge mb-4">Sepid Exchange</span>
        <h1 className="text-2xl font-black text-white">{pageTitle}</h1>
        <p className="mt-2 text-sm leading-7 text-white/50">{pageHint}</p>

        {step === "login" && (
          <form onSubmit={handleLookup} className="mt-8 space-y-4">
            <div>
              <label className="label-text">موبایل یا ایمیل</label>
              <input
                className="input-field"
                dir="ltr"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                placeholder="+989121234567"
                required
              />
              <p className="mt-1.5 text-xs text-white/35">
                شماره موبایل حتماً با <span className={ltrPhone}>+</span> شروع شود — مثل ربات تلگرام.
              </p>
            </div>
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button type="submit" disabled={loading} className="btn-primary w-full">
              {loading ? "..." : "ادامه"}
            </button>
          </form>
        )}

        {step === "password" && (
          <div className="mt-8 space-y-4">
            <form onSubmit={handlePasswordLogin} className="space-y-4">
              <div>
                <label className="label-text">موبایل یا ایمیل</label>
                <input className="input-field bg-white/5" dir="ltr" value={login} readOnly />
              </div>
              <div>
                <label className="label-text">رمز عبور</label>
                <input
                  type="password"
                  className="input-field"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              {error && <p className="text-sm text-red-300">{error}</p>}
              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? "..." : "ورود با رمز عبور"}
              </button>
            </form>

            <div className="relative py-2 text-center text-xs text-white/35">
              <span className="bg-ink-900/80 px-3">یا</span>
            </div>

            <button
              type="button"
              disabled={loading}
              onClick={() => sendOtp("login")}
              className="btn-ghost w-full"
            >
              ورود با کد پیامکی (SMS)
            </button>

            <button
              type="button"
              className="w-full text-xs text-white/40 underline"
              onClick={() => {
                setStep("login");
                setPassword("");
                setError("");
              }}
            >
              تغییر موبایل یا ایمیل
            </button>
          </div>
        )}

        {step === "otp_login" && (
          <form onSubmit={handleOtpLogin} className="mt-8 space-y-4">
            <div>
              <label className="label-text">موبایل یا ایمیل</label>
              <input className="input-field bg-white/5" dir="ltr" value={login} readOnly />
            </div>
            {phoneMasked && (
              <p className="text-xs text-white/50">کد به شماره {phoneMasked} ارسال شد.</p>
            )}
            <div>
              <label className="label-text">کد پیامکی</label>
              <input
                className="input-field"
                dir="ltr"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                placeholder="123456"
                required
                autoFocus
              />
              {devOtp && <p className="mt-1 text-xs text-amber-200">dev: {devOtp}</p>}
            </div>
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button type="submit" disabled={loading} className="btn-primary w-full">
              {loading ? "..." : "ورود"}
            </button>
            <button
              type="button"
              className="btn-ghost w-full text-sm"
              disabled={loading}
              onClick={() => sendOtp("login")}
            >
              ارسال مجدد کد
            </button>
            <button
              type="button"
              className="w-full text-xs text-white/40 underline"
              onClick={() => {
                setStep("password");
                setOtp("");
                setError("");
              }}
            >
              ورود با رمز عبور
            </button>
          </form>
        )}

        {step === "link" && profile && (
          <form onSubmit={handleLink} className="mt-8 space-y-4">
            <div className="rounded-xl border border-brand-400/20 bg-brand-500/10 p-4 text-sm">
              <p className="font-semibold text-brand-100">حساب تلگرام شما</p>
              <p className="mt-2 text-white/70">{(profile.display_name as string) || "کاربر"}</p>
              <p className="text-white/50">
                <span className={ltrPhone}>{formatPhone(profile.phone_number as string)}</span>
              </p>
            </div>
            <div>
              <label className="label-text">کد OTP</label>
              <input className="input-field" value={otp} onChange={(e) => setOtp(e.target.value)} required />
              {devOtp && <p className="mt-1 text-xs text-amber-200">dev: {devOtp}</p>}
            </div>
            <div>
              <label className="label-text">رمز عبور جدید (وب)</label>
              <input
                type="password"
                className="input-field"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={6}
                required
              />
            </div>
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button type="submit" className="btn-primary w-full">
              فعال‌سازی حساب وب
            </button>
          </form>
        )}

        {step === "register" && (
          <form onSubmit={handleRegister} className="mt-8 space-y-4">
            <div>
              <label className="label-text">کد OTP</label>
              <input className="input-field" value={otp} onChange={(e) => setOtp(e.target.value)} required />
              {devOtp && <p className="mt-1 text-xs text-amber-200">dev: {devOtp}</p>}
            </div>
            {[
              ["full_name", "نام"],
              ["last_name", "نام خانوادگی"],
              ["display_name", "نام نمایشی (یکتا)"],
              ["email", "ایمیل"],
              ["address", "آدرس"],
              ["phone_number", "موبایل (+989...)"],
            ].map(([key, label]) => (
              <div key={key}>
                <label className="label-text">{label}</label>
                <input
                  className="input-field"
                  dir={key.includes("phone") || key === "email" ? "ltr" : undefined}
                  value={(reg as Record<string, string | boolean>)[key] as string}
                  onChange={(e) => setReg({ ...reg, [key]: e.target.value })}
                  placeholder={key.includes("phone") ? "+989121234567" : undefined}
                  required={key !== "phone_number" || !login.startsWith("+")}
                />
              </div>
            ))}
            <div>
              <label className="label-text">رمز عبور</label>
              <input
                type="password"
                className="input-field"
                value={reg.password}
                onChange={(e) => setReg({ ...reg, password: e.target.value })}
                minLength={6}
                required
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-white/70">
              <input
                type="checkbox"
                checked={reg.accept_terms}
                onChange={(e) => setReg({ ...reg, accept_terms: e.target.checked })}
              />
              قوانین و مقررات را می‌پذیرم
            </label>
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button type="submit" className="btn-primary w-full">
              تکمیل ثبت‌نام
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
