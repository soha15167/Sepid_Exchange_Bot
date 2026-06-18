export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string | { msg?: string }[] }).detail;
    let msg = "خطا در ارتباط با سرور";
    if (typeof detail === "string") {
      msg = detail === "Not Found" ? "سرویس در دسترس نیست — API را بررسی کنید." : detail;
    } else if (Array.isArray(detail) && detail[0]?.msg) {
      msg = detail[0].msg;
    }
    throw new Error(msg);
  }
  return data as T;
}

export async function apiUpload<T>(
  path: string,
  form: FormData,
  token?: string | null,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method: "POST", body: form, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail;
    throw new Error(typeof detail === "string" ? detail : "خطا در آپلود");
  }
  return data as T;
}

export type Advert = {
  id: number;
  owner_id?: number;
  owner_name?: string;
  operation?: string;
  advert_type?: string;
  is_exchange?: boolean;
  euro_amount?: number;
  rate_toman?: number | null;
  fee_eur?: string;
  description?: string;
  methods?: string[];
  methods_label?: string;
  methods_display?: string;
  account_country?: string | null;
  country_label?: string | null;
  instant_transfer?: string | null;
  city_ir?: string | null;
  city_int?: string | null;
  euro_exchange?: number;
  status?: string;
  created_at?: string;
  channel_link?: string | null;
  channel_message_id?: number;
  locked?: boolean;
  is_mine?: boolean;
  public_offers?: PublicOffer[];
  offers_completed?: boolean;
};

export type PublicOffer = {
  seq: number;
  rate_toman?: number | null;
  proposed_euro_amount?: number | null;
  proposer_label: string;
  status: "pending" | "accepted" | "rejected" | string;
  skips_toman_rate?: boolean;
};

export type Offer = {
  id: number;
  advert_id: number;
  seq?: number;
  rate_toman?: number;
  description?: string;
  status?: string;
  proposed_euro_amount?: number;
  proposer_account_country?: string;
  proposer_id?: number;
  proposer_name?: string;
  owner_id?: number;
  advert_operation?: string;
  advert_euro_amount?: number;
  skips_toman_rate?: boolean;
  has_deal_gate?: boolean;
};

export type DealStatus = {
  offer_id: number;
  advert_id: number;
  offer_status: string;
  role: string;
  party_role?: string;
  my_response?: string | null;
  can_respond?: boolean;
  can_submit_account?: boolean;
  can_submit_receipt?: boolean;
  receipt_kind?: "toman" | "euro" | null;
  needs_telegram_handoff?: boolean;
  bot_link?: string | null;
  gate: {
    active: boolean;
    status?: string;
    status_label?: string;
    buyer_confirmed?: boolean;
    seller_confirmed?: boolean;
    buyer_account_sent?: boolean;
    seller_account_sent?: boolean;
  };
  telegram_required?: boolean;
  telegram_hint?: string | null;
};

export function fmtNum(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("fa-IR");
}

export function advertBadgeColor(type?: string): string {
  if (type?.includes("خرید")) return "from-indigo-500/25 to-indigo-600/5 text-indigo-200 border-indigo-400/35";
  if (type?.includes("فروش")) return "from-violet-500/25 to-violet-600/5 text-violet-200 border-violet-400/35";
  if (type?.includes("معاوضه")) return "from-cyan-500/20 to-cyan-600/5 text-cyan-200 border-cyan-400/35";
  return "from-white/10 to-white/5 text-white/80 border-white/15";
}
