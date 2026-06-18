"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, ExternalLink, KeyRound } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { formatPhone, ltrCell, ltrPhone } from "@/lib/format";
import { useAuth } from "@/lib/auth";

type SelfProfile = {
  telegram_id: number;
  display_name?: string;
  full_name?: string;
  last_name?: string;
  username?: string | null;
  email?: string | null;
  phone_number?: string | null;
  address?: string | null;
  has_telegram?: boolean;
  is_web_only?: boolean;
  has_password?: boolean;
  bot_link?: string;
  channel_link?: string;
  can_publish_adverts?: boolean;
};

export default function ProfilePage() {
  const { user, token, loading, refreshMe } = useAuth();
  const router = useRouter();
  const [profile, setProfile] = useState<SelfProfile | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [pwd, setPwd] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace("/auth");
  }, [loading, user, router]);

  useEffect(() => {
    if (!token) return;
    apiFetch<{ user: SelfProfile }>("/api/auth/me", {}, token)
      .then((d) => {
        setProfile(d.user);
        setDisplayName(d.user.display_name || "");
      })
      .catch(() => {});
  }, [token]);

  async function saveDisplayName(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const d = await apiFetch<{ user: SelfProfile }>(
        "/api/auth/me",
        { method: "PATCH", body: JSON.stringify({ display_name: displayName.trim() }) },
        token,
      );
      setProfile(d.user);
      await refreshMe();
      setMsg("✅ نام نمایشی ذخیره شد.");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await apiFetch(
        "/api/auth/me/password",
        {
          method: "POST",
          body: JSON.stringify({
            current_password: pwd.current,
            new_password: pwd.next,
          }),
        },
        token,
      );
      setPwd({ current: "", next: "" });
      setMsg("✅ رمز عبور تغییر کرد.");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  if (loading || !user) return <p className="text-white/50">...</p>;

  const p = profile || user;

  return (
    <div className="mx-auto w-full min-w-0 max-w-xl space-y-6">
      <Link href="/dashboard" className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        داشبورد
      </Link>

      {(msg || err) && (
        <div className={`rounded-xl border px-4 py-3 text-sm ${err ? "border-red-400/20 bg-red-500/10 text-red-200" : "border-brand-400/20 bg-brand-500/10 text-brand-100"}`}>
          {err || msg}
        </div>
      )}

      <div className="glass space-y-4 p-4 sm:p-6 lg:p-8">
        <h1 className="text-2xl font-bold">👤 مشخصات کاربر</h1>
        <dl className="space-y-3 text-sm">
          <Row label="🆔 آیدی عددی" value={String(p.telegram_id)} ltr />
          <Row label="👨‍💼 نام" value={`${p.full_name || "—"} ${p.last_name || ""}`.trim()} />
          <Row label="🏷️ نام نمایشی در آگهی" value={p.display_name || "—"} />
          <Row label="📧 ایمیل" value={p.email || "—"} ltr />
          <Row label="📱 شماره" value={p.phone_number || "—"} ltr phone />
          <Row label="🏠 آدرس" value={(profile?.address as string) || "—"} />
          <Row label="🔗 تلگرام" value={p.username ? `@${p.username}` : p.has_telegram ? "متصل" : "—"} ltr />
          <Row
            label="نوع حساب"
            value={
              p.has_telegram
                ? "متصل به تلگرام"
                : p.is_web_only
                  ? "فقط وب — برای ثبت آگهی باید در ربات ثبت‌نام کنید"
                  : "—"
            }
          />
        </dl>
      </div>

      {p.is_web_only && (
        <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-4 text-sm text-amber-100">
          <p className="font-medium">اتصال به تلگرام برای انتشار آگهی</p>
          <ol className="mt-2 list-decimal space-y-1 ps-5 text-xs leading-6 text-amber-100/90">
            <li>در ربات با همان شماره موبایل ثبت‌نام کنید.</li>
            <li>عضو کانال شوید.</li>
            <li>از وب با همان شماره وارد شوید (حساب ربات متصل می‌شود).</li>
          </ol>
          <div className="mt-3 flex flex-wrap gap-2">
            {profile?.bot_link && (
              <a href={profile.bot_link} target="_blank" rel="noopener noreferrer" className="btn-ghost inline-flex gap-1 py-1.5 text-xs">
                باز کردن ربات
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
            {profile?.channel_link && (
              <a href={profile.channel_link} target="_blank" rel="noopener noreferrer" className="btn-ghost inline-flex gap-1 py-1.5 text-xs">
                عضویت کانال
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        </div>
      )}

      <form onSubmit={saveDisplayName} className="glass space-y-3 p-4 sm:p-6">
        <h2 className="font-bold">ویرایش نام نمایشی</h2>
        <input
          className="input-field"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          minLength={2}
          required
        />
        <button type="submit" disabled={busy} className="btn-primary w-full sm:w-auto">
          ذخیره
        </button>
      </form>

      {profile?.has_password && (
        <form onSubmit={changePassword} className="glass space-y-3 p-4 sm:p-6">
          <h2 className="flex items-center gap-2 font-bold">
            <KeyRound className="h-4 w-4 text-brand-300" />
            تغییر رمز عبور
          </h2>
          <input
            type="password"
            className="input-field"
            placeholder="رمز فعلی"
            value={pwd.current}
            onChange={(e) => setPwd({ ...pwd, current: e.target.value })}
            required
          />
          <input
            type="password"
            className="input-field"
            placeholder="رمز جدید (حداقل ۶ کاراکتر)"
            value={pwd.next}
            onChange={(e) => setPwd({ ...pwd, next: e.target.value })}
            minLength={6}
            required
          />
          <button type="submit" disabled={busy} className="btn-ghost w-full sm:w-auto">
            تغییر رمز
          </button>
        </form>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  ltr,
  phone,
}: {
  label: string;
  value: string;
  ltr?: boolean;
  phone?: boolean;
}) {
  const text = phone ? formatPhone(value) : value;
  return (
    <div className="flex flex-col gap-1 border-b border-white/5 pb-3 sm:flex-row sm:justify-between sm:gap-2">
      <dt className="text-white/50">{label}</dt>
      <dd
        className={
          ltr
            ? `min-w-0 break-anywhere font-medium text-white/90 ${phone ? ltrPhone : ltrCell}`
            : "min-w-0 break-anywhere font-medium text-white/90"
        }
      >
        {text}
      </dd>
    </div>
  );
}
