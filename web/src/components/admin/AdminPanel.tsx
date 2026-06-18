"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import {
  ArrowRight,
  Bot,
  Loader2,
  RefreshCw,
  Shield,
  Trash2,
  Users,
} from "lucide-react";
import { apiFetch, fmtNum } from "@/lib/api";
import { formatEmail, formatId, formatPhone, ltrCell } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/ui/PageHeader";
import { AdminDealPanel } from "@/components/admin/AdminDealPanel";

type View =
  | "menu"
  | "stats"
  | "users"
  | "search_user"
  | "edit_user"
  | "delete_user"
  | "restrict"
  | "add_user"
  | "adverts"
  | "search_advert"
  | "edit_advert"
  | "delete_advert"
  | "offers"
  | "negotiations"
  | "deal_gates"
  | "proxy_offer"
  | "broadcast_rate"
  | "restart_bot"
  | "bot";

type MenuItem = { id: string; label: string; web: boolean; hint?: string; href?: string };

type UserRow = {
  telegram_id: number;
  display_name?: string;
  username?: string;
  phone_number?: string;
  email?: string;
  is_restricted?: boolean;
};

export function AdminPanel() {
  const { token, user } = useAuth();
  const [view, setView] = useState<View>("menu");
  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [adverts, setAdverts] = useState<
    { id: number; owner_name?: string; operation?: string; euro_amount?: number; rate_toman?: number }[]
  >([]);
  const [userPage, setUserPage] = useState(0);
  const [userPages, setUserPages] = useState(1);
  const [advPage, setAdvPage] = useState(0);
  const [advPages, setAdvPages] = useState(1);
  const [query, setQuery] = useState("");
  const [targetId, setTargetId] = useState("");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [offers, setOffers] = useState<Record<string, unknown>[]>([]);
  const [negotiations, setNegotiations] = useState<Record<string, unknown> | null>(null);
  const [gates, setGates] = useState<Record<string, unknown>[]>([]);
  const [addUserForm, setAddUserForm] = useState({
    telegram_id: "",
    full_name: "",
    last_name: "",
    display_name: "",
    email: "",
    address: "",
    phone_number: "",
    otp_code: "",
  });
  const [proxyForm, setProxyForm] = useState({
    advert_id: "",
    alias: "",
    rate_toman: "",
    description: "",
  });
  const [userEditForm, setUserEditForm] = useState({
    display_name: "",
    email: "",
    phone_number: "",
  });
  const [advertEditForm, setAdvertEditForm] = useState({
    description: "",
    rate_toman: "",
    fee_override_eur: "",
  });
  const [gateLookupId, setGateLookupId] = useState("");
  const [selectedDealOfferId, setSelectedDealOfferId] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const loadMenu = useCallback(async () => {
    if (!token) return;
    const d = await apiFetch<{ items: MenuItem[] }>("/api/admin/menu", {}, token);
    setMenu(d.items);
  }, [token]);

  const loadStats = useCallback(async () => {
    if (!token) return;
    setStats(await apiFetch("/api/admin/stats", {}, token));
  }, [token]);

  useEffect(() => {
    loadMenu().catch(() => {});
    loadStats().catch(() => {});
  }, [loadMenu, loadStats]);

  useEffect(() => {
    if (!token || view !== "users") return;
    apiFetch<{ items: UserRow[]; pages: number }>(`/api/admin/users?page=${userPage}`, {}, token)
      .then((d) => {
        setUsers(d.items);
        setUserPages(d.pages);
      })
      .catch((e) => setErr(e.message));
  }, [token, view, userPage]);

  useEffect(() => {
    if (!token || view !== "adverts") return;
    apiFetch<{ items: typeof adverts; pages: number }>(`/api/admin/adverts?page=${advPage}`, {}, token)
      .then((d) => {
        setAdverts(d.items);
        setAdvPages(d.pages);
      })
      .catch((e) => setErr(e.message));
  }, [token, view, advPage]);

  function openMenuItem(item: MenuItem) {
    setErr("");
    setMsg("");
    if (item.href) return;
    if (!item.web) {
      setMsg(item.hint || "این عملیات فعلاً فقط در ربات تلگرام در دسترس است.");
      return;
    }
    const map: Record<string, View> = {
      users: "users",
      search_user: "search_user",
      edit_user: "edit_user",
      delete_user: "delete_user",
      restrict: "restrict",
      add_user: "add_user",
      adverts: "adverts",
      search_advert: "search_advert",
      edit_advert: "edit_advert",
      delete_advert: "delete_advert",
      offers: "offers",
      negotiations: "negotiations",
      deal_gates: "deal_gates",
      proxy_offer: "proxy_offer",
      broadcast_rate: "broadcast_rate",
      restart_bot: "restart_bot",
      bot_off: "bot",
      bot_on: "bot",
    };
    const v = map[item.id];
    if (v) setView(v);
    if (item.id === "bot_off") toggleBot(false);
    if (item.id === "bot_on") toggleBot(true);
  }

  async function toggleBot(enabled: boolean) {
    if (!token) return;
    setBusy(true);
    setErr("");
    try {
      const r = await apiFetch<{ enabled?: boolean }>(
        "/api/admin/bot/toggle",
        { method: "POST", body: JSON.stringify({ enabled, notify_telegram: true }) },
        token,
      );
      setMsg(enabled ? "✅ ربات فعال شد." : "⛔️ ربات غیرفعال شد.");
      await loadStats();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function searchUsers() {
    if (!token || !query.trim()) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ items: UserRow[] }>(
        `/api/admin/users/search?q=${encodeURIComponent(query.trim())}`,
        {},
        token,
      );
      setUsers(d.items);
      setView("users");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function loadUserDetail(id: string) {
    if (!token) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ user: Record<string, unknown> }>(`/api/admin/users/${id}`, {}, token);
      setDetail(d.user);
      setUserEditForm({
        display_name: String(d.user.display_name || ""),
        email: String(d.user.email || ""),
        phone_number: String(d.user.phone_number || ""),
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function loadAdvertDetail(id: string) {
    if (!token) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ advert: Record<string, unknown> }>(`/api/admin/adverts/${id}`, {}, token);
      setDetail(d.advert);
      setAdvertEditForm({
        description: String(d.advert.description || ""),
        rate_toman: String(d.advert.rate_toman || ""),
        fee_override_eur: d.advert.fee_override_eur != null ? String(d.advert.fee_override_eur) : "",
      });
      setView("edit_advert");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function loadOffers(advertId: string) {
    if (!token) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ items: Record<string, unknown>[] }>(
        `/api/admin/adverts/${advertId}/offers`,
        {},
        token,
      );
      setOffers(d.items);
      setTargetId(advertId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function loadNegotiations(advertId: string) {
    if (!token) return;
    setBusy(true);
    try {
      setNegotiations(await apiFetch(`/api/admin/adverts/${advertId}/negotiations`, {}, token));
      setTargetId(advertId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function loadDealGates() {
    if (!token) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ items: Record<string, unknown>[] }>("/api/admin/deal-gates", {}, token);
      setGates(d.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (view === "deal_gates") loadDealGates();
  }, [view, token]);

  async function deleteUser() {
    if (!token || !targetId || !confirm(`کاربر ${targetId} حذف شود؟`)) return;
    setBusy(true);
    try {
      await apiFetch(`/api/admin/users/${targetId}`, { method: "DELETE" }, token);
      setMsg("✅ کاربر حذف شد.");
      setTargetId("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function deleteAdvert() {
    if (!token || !targetId || !confirm(`آگهی #${targetId} حذف شود؟`)) return;
    setBusy(true);
    try {
      await apiFetch(`/api/admin/adverts/${targetId}`, { method: "DELETE" }, token);
      setMsg("✅ آگهی حذف شد.");
      setTargetId("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function setRestriction(restricted: boolean) {
    if (!token || !targetId) return;
    setBusy(true);
    try {
      await apiFetch(
        `/api/admin/users/${targetId}/restrict`,
        { method: "POST", body: JSON.stringify({ restricted }) },
        token,
      );
      setMsg(restricted ? "⛔️ کاربر محدود شد." : "✅ محدودیت برداشته شد.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function deleteOffer(offerId: number) {
    if (!token || !confirm(`پیشنهاد #${offerId} حذف شود؟`)) return;
    try {
      await apiFetch(`/api/admin/offers/${offerId}`, { method: "DELETE" }, token);
      setMsg("✅ پیشنهاد حذف شد.");
      if (targetId) loadOffers(targetId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    }
  }

  async function submitAddUser(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const r = await apiFetch<{
        ok?: boolean;
        needs_otp?: boolean;
        dev_code?: string;
        message?: string;
        user_id?: number;
      }>(
        "/api/admin/users/create",
        {
          method: "POST",
          body: JSON.stringify({
            telegram_id: Number(addUserForm.telegram_id.replace(/\D/g, "")),
            full_name: addUserForm.full_name,
            last_name: addUserForm.last_name,
            display_name: addUserForm.display_name,
            email: addUserForm.email,
            address: addUserForm.address,
            phone_number: addUserForm.phone_number,
            otp_code: addUserForm.otp_code.trim() || null,
          }),
        },
        token,
      );
      if (r.ok) {
        setMsg(`✅ کاربر #${r.user_id} اضافه شد.`);
        setAddUserForm({
          telegram_id: "",
          full_name: "",
          last_name: "",
          display_name: "",
          email: "",
          address: "",
          phone_number: "",
          otp_code: "",
        });
      } else if (r.needs_otp) {
        setMsg(
          `${r.message || "کد OTP لازم است."}${r.dev_code ? ` (dev: ${r.dev_code})` : ""}`,
        );
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function submitProxyOffer(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setErr("");
    try {
      await apiFetch(
        "/api/admin/proxy-offer",
        {
          method: "POST",
          body: JSON.stringify({
            advert_id: Number(proxyForm.advert_id.replace(/\D/g, "")),
            alias: proxyForm.alias,
            rate_toman: Number(proxyForm.rate_toman.replace(/\D/g, "")),
            description: proxyForm.description,
          }),
        },
        token,
      );
      setMsg("✅ پیشنهاد نمایشی ثبت شد.");
      setProxyForm({ advert_id: "", alias: "", rate_toman: "", description: "" });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function runBonbastBroadcast() {
    if (!token || !confirm("نرخ بن‌بast در کانال منتشر شود؟")) return;
    setBusy(true);
    setErr("");
    try {
      await apiFetch("/api/admin/broadcast/bonbast", { method: "POST" }, token);
      setMsg("✅ نرخ بن‌بast منتشر شد.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function runBotRestart() {
    if (!token || !confirm("سرویس ربات ری‌استارت شود؟")) return;
    setBusy(true);
    setErr("");
    try {
      const r = await apiFetch<{ message?: string }>("/api/admin/bot/restart", { method: "POST" }, token);
      setMsg(r.message || "✅ ری‌استارت زمان‌بندی شد.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function saveUserEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !targetId) return;
    setBusy(true);
    setErr("");
    try {
      await apiFetch(
        `/api/admin/users/${targetId}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            display_name: userEditForm.display_name,
            email: userEditForm.email,
            phone_number: userEditForm.phone_number,
          }),
        },
        token,
      );
      setMsg("✅ کاربر به‌روز شد.");
      loadUserDetail(targetId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function saveAdvertEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !detail?.id) return;
    setBusy(true);
    setErr("");
    try {
      const aid = String(detail.id);
      if (advertEditForm.description.trim()) {
        await apiFetch(
          `/api/admin/adverts/${aid}/field`,
          { method: "PATCH", body: JSON.stringify({ field: "description", value: advertEditForm.description.trim() }) },
          token,
        );
      }
      if (advertEditForm.rate_toman.replace(/\D/g, "")) {
        await apiFetch(
          `/api/admin/adverts/${aid}/field`,
          {
            method: "PATCH",
            body: JSON.stringify({ field: "rate_toman", value: advertEditForm.rate_toman.replace(/\D/g, "") }),
          },
          token,
        );
      }
      const feeRaw = advertEditForm.fee_override_eur.trim();
      await apiFetch(
        `/api/admin/adverts/${aid}/fee`,
        {
          method: "PATCH",
          body: JSON.stringify({
            fee_override_eur: feeRaw ? Number(feeRaw) : null,
          }),
        },
        token,
      );
      setMsg("✅ آگهی به‌روز شد.");
      loadAdvertDetail(aid);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function lookupDealGate() {
    if (!token || !gateLookupId.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const q = gateLookupId.trim();
      const isOffer = q.startsWith("o") || q.includes("offer");
      const num = q.replace(/\D/g, "");
      const url = isOffer
        ? `/api/admin/deal-gates/lookup?offer_id=${num}`
        : `/api/admin/deal-gates/lookup?advert_id=${num}`;
      const d = await apiFetch<{ gate: { offer_id: number } }>(url, {}, token);
      setSelectedDealOfferId(Number(d.gate.offer_id));
    } catch (e) {
      setSelectedDealOfferId(null);
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  if (!user?.is_admin) {
    return <p className="text-red-300">دسترسی ادمین ندارید.</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        badge="پنل مدیریت"
        badgeIcon={Shield}
        title="ادمین Sepid Exchange"
        subtitle="همان منوی ربات — کاربران، آگهی‌ها، پیشنهادها، معاملات و وضعیت ربات"
      >
        <button type="button" onClick={() => setView("menu")} className="btn-ghost gap-2 text-sm">
          <ArrowRight className="h-4 w-4" />
          منو
        </button>
      </PageHeader>

      {stats && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="bento-card p-4">
            <p className="text-xs text-white/40">کاربران</p>
            <p className="mt-1 text-2xl font-black">{stats.users_total as number}</p>
          </div>
          <div className="bento-card p-4">
            <p className="text-xs text-white/40">آگهی‌ها</p>
            <p className="mt-1 text-2xl font-black">{stats.adverts_total as number}</p>
          </div>
          <div className="bento-card p-4">
            <p className="text-xs text-white/40">ربات</p>
            <p className="mt-1 text-lg font-bold">{stats.bot_enabled ? "🟢 فعال" : "🔴 غیرفعال"}</p>
          </div>
        </div>
      )}

      {(msg || err) && (
        <div
          className={clsx(
            "rounded-xl border px-4 py-3 text-sm",
            err ? "border-red-400/20 bg-red-500/10 text-red-200" : "border-brand-400/20 bg-brand-500/10 text-brand-100",
          )}
        >
          {err || msg}
        </div>
      )}

      {view === "menu" && (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {menu.map((item) =>
            item.href ? (
              <Link
                key={item.id}
                href={item.href}
                className="bento-card block p-4 text-sm font-medium transition hover:border-brand-400/30"
              >
                {item.label}
              </Link>
            ) : (
              <button
                key={item.id}
                type="button"
                disabled={!item.web && !!item.hint}
                onClick={() => openMenuItem(item)}
                className={clsx(
                  "bento-card p-4 text-start text-sm font-medium transition",
                  item.web ? "hover:border-brand-400/30" : "opacity-50",
                )}
              >
                {item.label}
                {item.hint && <span className="mt-1 block text-[10px] text-white/35">{item.hint}</span>}
              </button>
            ),
          )}
        </div>
      )}

      {view === "users" && (
        <AdminTable
          title="لیست کاربران"
          loading={busy}
          onRefresh={() => setUserPage((p) => p)}
          pagination={{ page: userPage, pages: userPages, setPage: setUserPage }}
          headers={["شناسه", "نام", "تلفن", "ایمیل", ""]}
          rows={users.map((u) => [
            <span key="id" className={ltrCell}>{formatId(u.telegram_id)}</span>,
            u.display_name || u.username || "—",
            <span key="ph" className={ltrCell}>{formatPhone(u.phone_number)}</span>,
            <span key="em" className={ltrCell}>{formatEmail(u.email)}</span>,
            <button
              key="btn"
              type="button"
              className="text-xs text-brand-300"
              onClick={() => {
                setTargetId(String(u.telegram_id));
                loadUserDetail(String(u.telegram_id));
                setView("edit_user");
              }}
            >
              جزئیات
            </button>,
          ])}
        />
      )}

      {view === "search_user" && (
        <ActionCard title="جستجوی کاربر">
          <input className="input-field" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="آیدی، نام، @username، موبایل..." />
          <button type="button" disabled={busy} onClick={searchUsers} className="btn-primary mt-3">
            جستجو
          </button>
        </ActionCard>
      )}

      {view === "edit_user" && (
        <ActionCard title="ویرایش / مشاهده کاربر">
          <input className="input-field" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="telegram_id" />
          <button type="button" className="btn-ghost mt-2" onClick={() => loadUserDetail(targetId)}>
            بارگذاری
          </button>
          {detail && (
            <>
              <dl className="mt-4 space-y-2 text-sm">
                <Row l="شناسه" v={formatId(detail.telegram_id as number)} ltr />
                <Row l="محدود" v={detail.is_restricted ? "بله" : "خیر"} />
              </dl>
              <form onSubmit={saveUserEdit} className="mt-4 space-y-3">
                <input
                  className="input-field"
                  placeholder="نام نمایشی"
                  value={userEditForm.display_name}
                  onChange={(e) => setUserEditForm({ ...userEditForm, display_name: e.target.value })}
                />
                <input
                  className="input-field"
                  dir="ltr"
                  placeholder="email"
                  value={userEditForm.email}
                  onChange={(e) => setUserEditForm({ ...userEditForm, email: e.target.value })}
                />
                <input
                  className="input-field ltr-phone"
                  dir="ltr"
                  placeholder="+989..."
                  value={userEditForm.phone_number}
                  onChange={(e) => setUserEditForm({ ...userEditForm, phone_number: e.target.value })}
                />
                <button type="submit" disabled={busy} className="btn-primary">
                  ذخیره تغییرات
                </button>
              </form>
            </>
          )}
        </ActionCard>
      )}

      {view === "delete_user" && (
        <ActionCard title="حذف کاربر">
          <input className="input-field" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="telegram_id" />
          <button type="button" disabled={busy} onClick={deleteUser} className="btn-ghost mt-3 text-red-300">
            <Trash2 className="inline h-4 w-4" /> حذف
          </button>
        </ActionCard>
      )}

      {view === "restrict" && (
        <ActionCard title="محدودیت دسترسی">
          <input className="input-field" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="telegram_id" />
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button type="button" onClick={() => setRestriction(true)} className="btn-ghost w-full text-amber-200 sm:w-auto">
              محدود کن
            </button>
            <button type="button" onClick={() => setRestriction(false)} className="btn-primary w-full sm:w-auto">
              برداشتن محدودیت
            </button>
          </div>
        </ActionCard>
      )}

      {view === "add_user" && (
        <ActionCard title="افزودن کاربر">
          <form onSubmit={submitAddUser} className="grid gap-3 sm:grid-cols-2">
            <input className="input-field sm:col-span-2" dir="ltr" placeholder="آیدی تلگرام" value={addUserForm.telegram_id} onChange={(e) => setAddUserForm({ ...addUserForm, telegram_id: e.target.value })} required />
            <input className="input-field" placeholder="نام" value={addUserForm.full_name} onChange={(e) => setAddUserForm({ ...addUserForm, full_name: e.target.value })} required />
            <input className="input-field" placeholder="نام خانوادگی" value={addUserForm.last_name} onChange={(e) => setAddUserForm({ ...addUserForm, last_name: e.target.value })} required />
            <input className="input-field sm:col-span-2" placeholder="نام نمایشی آگهی" value={addUserForm.display_name} onChange={(e) => setAddUserForm({ ...addUserForm, display_name: e.target.value })} required />
            <input className="input-field sm:col-span-2" dir="ltr" placeholder="email" value={addUserForm.email} onChange={(e) => setAddUserForm({ ...addUserForm, email: e.target.value })} required />
            <input className="input-field sm:col-span-2" placeholder="آدرس" value={addUserForm.address} onChange={(e) => setAddUserForm({ ...addUserForm, address: e.target.value })} required />
            <input className="input-field sm:col-span-2 ltr-phone" dir="ltr" placeholder="+98912..." value={addUserForm.phone_number} onChange={(e) => setAddUserForm({ ...addUserForm, phone_number: e.target.value })} required />
            <input className="input-field sm:col-span-2" dir="ltr" placeholder="کد OTP (اگر لازم شد)" value={addUserForm.otp_code} onChange={(e) => setAddUserForm({ ...addUserForm, otp_code: e.target.value })} />
            <button type="submit" disabled={busy} className="btn-primary sm:col-span-2">ثبت کاربر</button>
          </form>
        </ActionCard>
      )}

      {view === "proxy_offer" && (
        <ActionCard title="پیشنهاد نمایشی">
          <form onSubmit={submitProxyOffer} className="space-y-3">
            <input className="input-field" dir="ltr" placeholder="شماره آگهی (rowid)" value={proxyForm.advert_id} onChange={(e) => setProxyForm({ ...proxyForm, advert_id: e.target.value })} required />
            <input className="input-field" placeholder="نام نمایشی برای صاحب آگهی" value={proxyForm.alias} onChange={(e) => setProxyForm({ ...proxyForm, alias: e.target.value })} required />
            <input className="input-field" dir="ltr" placeholder="نرخ تومان" value={proxyForm.rate_toman} onChange={(e) => setProxyForm({ ...proxyForm, rate_toman: e.target.value })} required />
            <textarea className="input-field min-h-[100px]" placeholder="توضیحات پیشنهاد" value={proxyForm.description} onChange={(e) => setProxyForm({ ...proxyForm, description: e.target.value })} required />
            <button type="submit" disabled={busy} className="btn-primary w-full">ثبت پیشنهاد نمایشی</button>
          </form>
        </ActionCard>
      )}

      {view === "broadcast_rate" && (
        <ActionCard title="نرخ بن‌بast کانال">
          <p className="mb-4 text-sm text-white/55">دریافت نرخ از bonbast.com و انتشار در کانال (مثل ربات).</p>
          <button type="button" disabled={busy} onClick={runBonbastBroadcast} className="btn-primary">
            انتشار الان
          </button>
        </ActionCard>
      )}

      {view === "restart_bot" && (
        <ActionCard title="ری‌استارت سرویس ربات">
          <p className="mb-4 text-sm text-white/55">
            همان <code className="text-brand-200">BOT_RESTART_COMMAND</code> در .env — ممکن است چند ثانیه قطعی باشد.
          </p>
          <button type="button" disabled={busy} onClick={runBotRestart} className="btn-ghost text-amber-200">
            اجرای ری‌استارت
          </button>
        </ActionCard>
      )}

      {view === "adverts" && (
        <AdminTable
          title="لیست آگهی‌ها"
          loading={busy}
          pagination={{ page: advPage, pages: advPages, setPage: setAdvPage }}
          headers={["#", "صاحب", "نوع", "یورو", "نرخ", ""]}
          rows={adverts.map((a) => [
            a.id,
            a.owner_name || "—",
            a.operation,
            fmtNum(a.euro_amount),
            fmtNum(a.rate_toman),
            <button
              key="b"
              type="button"
              className="text-xs text-brand-300"
              onClick={() => {
                setTargetId(String(a.id));
                loadAdvertDetail(String(a.id));
                setView("edit_advert");
              }}
            >
              جزئیات
            </button>,
          ])}
        />
      )}

      {view === "search_advert" && (
        <ActionCard title="جستجوی آگهی">
          <input className="input-field" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="شماره آگهی (#)" />
          <button type="button" className="btn-primary mt-3" onClick={() => loadAdvertDetail(targetId)}>
            نمایش
          </button>
        </ActionCard>
      )}

      {view === "edit_advert" && detail && (
        <ActionCard title={`آگهی #${detail.id}`}>
          <dl className="space-y-2 text-sm">
            <Row l="صاحب" v={String(detail.owner_name || "—")} />
            <Row l="نوع" v={String(detail.operation || "—")} />
            <Row l="یورو" v={fmtNum(detail.euro_amount as number)} ltr />
            <Row l="کارمزد" v={String(detail.fee_display || "—")} />
          </dl>
          <form onSubmit={saveAdvertEdit} className="mt-4 space-y-3">
            <textarea
              className="input-field min-h-[80px]"
              placeholder="توضیحات"
              value={advertEditForm.description}
              onChange={(e) => setAdvertEditForm({ ...advertEditForm, description: e.target.value })}
            />
            <input
              className="input-field"
              dir="ltr"
              placeholder="نرخ تومان"
              value={advertEditForm.rate_toman}
              onChange={(e) => setAdvertEditForm({ ...advertEditForm, rate_toman: e.target.value })}
            />
            <input
              className="input-field"
              dir="ltr"
              placeholder="کارمزد override (€) — خالی = پیش‌فرض"
              value={advertEditForm.fee_override_eur}
              onChange={(e) => setAdvertEditForm({ ...advertEditForm, fee_override_eur: e.target.value })}
            />
            <button type="submit" disabled={busy} className="btn-primary">
              ذخیره آگهی
            </button>
          </form>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" className="btn-ghost text-sm" onClick={() => loadOffers(String(detail.id))}>
              پیشنهادها
            </button>
            <button type="button" className="btn-ghost text-sm" onClick={() => loadNegotiations(String(detail.id))}>
              مذاکرات
            </button>
          </div>
        </ActionCard>
      )}

      {view === "delete_advert" && (
        <ActionCard title="حذف آگهی">
          <input className="input-field" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="advert id" />
          <button type="button" onClick={deleteAdvert} className="btn-ghost mt-3 text-red-300">
            حذف آگهی
          </button>
        </ActionCard>
      )}

      {view === "offers" && (
        <ActionCard title="مدیریت پیشنهادها">
          <input className="input-field mb-3" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="شماره آگهی" />
          <button type="button" className="btn-primary" onClick={() => loadOffers(targetId)}>
            بارگذاری
          </button>
          {offers.length > 0 && (
            <ul className="mt-4 space-y-2 text-sm">
              {offers.map((o) => (
                <li key={String(o.id)} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/10 p-3">
                  <span>
                    #{String(o.seq)} · {String(o.status_fa)} · نرخ {fmtNum(o.rate_toman as number)}
                  </span>
                  <button type="button" onClick={() => deleteOffer(Number(o.id))} className="text-xs text-red-300">
                    حذف
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ActionCard>
      )}

      {view === "negotiations" && (
        <ActionCard title="مذاکرات آگهی">
          <input className="input-field mb-3" dir="ltr" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="شماره آگهی" />
          <button type="button" className="btn-primary" onClick={() => loadNegotiations(targetId)}>
            بارگذاری
          </button>
          {negotiations && (
            <div className="mt-4 max-h-96 space-y-4 overflow-y-auto text-sm">
              {(negotiations.sections as Record<string, unknown>[])?.map((s) => (
                <div key={String(s.offer_id)} className="rounded-lg border border-white/10 p-3">
                  <p className="font-semibold">
                    پیشنهاد #{String(s.seq)} · {String(s.status_fa)}
                  </p>
                  <ul className="mt-2 space-y-1 text-white/60">
                    {(s.lines as { role: string; text: string }[])?.map((ln, i) => (
                      <li key={i}>
                        <strong>{ln.role}:</strong> {ln.text}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </ActionCard>
      )}

      {view === "deal_gates" && (
        <ActionCard title="مدیریت معاملات (Deal Gate)">
          <div className="mb-4 flex flex-col gap-2 sm:flex-row">
            <input
              className="input-field"
              dir="ltr"
              placeholder="offer_id یا advert_id"
              value={gateLookupId}
              onChange={(e) => setGateLookupId(e.target.value)}
            />
            <button type="button" className="btn-primary" onClick={lookupDealGate} disabled={busy}>
              جستجو
            </button>
          </div>

          {selectedDealOfferId && token && (
            <div className="mb-4">
              <AdminDealPanel
                token={token}
                offerId={selectedDealOfferId}
                onClose={() => setSelectedDealOfferId(null)}
                onMessage={(m) => setMsg(m)}
                onError={(e) => setErr(e)}
              />
            </div>
          )}

          <button type="button" className="btn-ghost mb-3 gap-2 text-sm" onClick={loadDealGates}>
            <RefreshCw className="h-4 w-4" /> بروزرسانی لیست
          </button>
          {gates.length === 0 ? (
            <p className="text-white/40">معاملهٔ فعالی نیست.</p>
          ) : (
            <ul className="card-stack space-y-2 text-sm">
              {gates.map((g) => {
                const oid = Number(g.offer_id);
                const active = selectedDealOfferId === oid;
                return (
                  <li key={String(g.offer_id)}>
                    <button
                      type="button"
                      className={clsx(
                        "w-full rounded-lg border p-3 text-right transition",
                        active
                          ? "border-emerald-500/50 bg-emerald-950/30"
                          : "border-white/10 hover:border-white/20",
                      )}
                      onClick={() => setSelectedDealOfferId(oid)}
                    >
                      <span className={ltrCell}>offer #{String(g.offer_id)}</span>
                      {g.advert_rowid != null ? (
                        <>
                          {" "}
                          · آگهی <span className={ltrCell}>#{String(g.advert_rowid)}</span>
                        </>
                      ) : null}{" "}
                      · وضعیت: <strong>{String(g.gate_status)}</strong>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="mt-4 text-xs text-white/35">
            همان دکمه‌های پیام ادمین در تلگرام — هر عمل همزمان پیام تلگرام را به‌روز می‌کند.
          </p>
        </ActionCard>
      )}

      {view === "bot" && (
        <ActionCard title="وضعیت ربات">
          <div className="flex flex-wrap gap-3">
            <button type="button" disabled={busy} onClick={() => toggleBot(true)} className="btn-primary gap-2">
              <Bot className="h-4 w-4" /> فعال کردن
            </button>
            <button type="button" disabled={busy} onClick={() => toggleBot(false)} className="btn-ghost gap-2 text-amber-200">
              غیرفعال کردن
            </button>
          </div>
        </ActionCard>
      )}

      {busy && (
        <p className="flex items-center gap-2 text-sm text-white/40">
          <Loader2 className="h-4 w-4 animate-spin" /> ...
        </p>
      )}
    </div>
  );
}

function ActionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bento-card p-4 sm:p-6">
      <h2 className="mb-4 text-lg font-bold sm:text-xl">{title}</h2>
      {children}
    </div>
  );
}

function Row({ l, v, ltr: isLtr }: { l: string; v: string; ltr?: boolean }) {
  return (
    <div className="flex flex-col gap-1 border-b border-white/5 pb-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <dt className="shrink-0 text-white/45">{l}</dt>
      <dd className={clsx("min-w-0 break-anywhere font-medium sm:text-end", isLtr && ltrCell)}>{v}</dd>
    </div>
  );
}

function AdminTable({
  title,
  headers,
  rows,
  pagination,
  loading,
  onRefresh,
}: {
  title: string;
  headers: string[];
  rows: React.ReactNode[][];
  pagination?: { page: number; pages: number; setPage: (fn: (p: number) => number) => void };
  loading?: boolean;
  onRefresh?: () => void;
}) {
  return (
    <div className="bento-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex min-w-0 items-center gap-2 text-base font-bold sm:text-lg">
          <Users className="h-4 w-4 shrink-0 text-brand-300" />
          <span className="truncate">{title}</span>
        </h2>
        {onRefresh && (
          <button type="button" onClick={onRefresh} className="btn-ghost shrink-0 p-2">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        )}
      </div>

      <div className="space-y-3 lg:hidden">
        {rows.map((cells, i) => (
          <div key={i} className="rounded-xl border border-white/10 bg-ink-950/40 p-3">
            {cells.map((c, j) =>
              headers[j] ? (
                <div key={j} className="flex flex-col gap-0.5 border-b border-white/5 py-2 last:border-0 sm:flex-row sm:justify-between sm:gap-3">
                  <span className="text-xs text-white/45">{headers[j]}</span>
                  <span className="min-w-0 break-anywhere text-sm">{c}</span>
                </div>
              ) : (
                <div key={j} className="pt-2">
                  {c}
                </div>
              ),
            )}
          </div>
        ))}
      </div>

      <div className="table-scroll hidden lg:block">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-white/10 text-white/45">
              {headers.map((h) => (
                <th key={h} className="py-2 text-start font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((cells, i) => (
              <tr key={i} className="border-b border-white/5">
                {cells.map((c, j) => (
                  <td key={j} className="max-w-[200px] break-anywhere py-2.5">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pagination && pagination.pages > 1 && (
        <div className="mt-3 flex justify-center gap-3">
          <button
            type="button"
            disabled={pagination.page <= 0}
            onClick={() => pagination.setPage((p) => Math.max(0, p - 1))}
            className="btn-ghost py-1 text-xs"
          >
            قبلی
          </button>
          <span className="text-xs text-white/40">
            {pagination.page + 1} / {pagination.pages}
          </span>
          <button
            type="button"
            disabled={pagination.page >= pagination.pages - 1}
            onClick={() => pagination.setPage((p) => p + 1)}
            className="btn-ghost py-1 text-xs"
          >
            بعدی
          </button>
        </div>
      )}
    </div>
  );
}
