const PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
const BIDI_MARKS = /^[\u200f\u200e\u202a\u202b\u202c]+/;

function asciiDigits(raw: string): string {
  let s = raw.trim();
  for (let i = 0; i < 10; i++) {
    s = s.split(PERSIAN_DIGITS[i]).join(String(i));
    s = s.split(ARABIC_DIGITS[i]).join(String(i));
  }
  return s.replace(/\D/g, "");
}

/** Phone login/register must start with + (same rule as Telegram bot). */
export function phoneStartsWithPlus(raw: string): boolean {
  const s = (raw || "").trim().replace(BIDI_MARKS, "");
  return s.startsWith("+");
}

/** Strip spaces/dashes from +phone; lowercase emails. */
export function normalizeLoginInput(login: string): string {
  const s = login.trim();
  if (!s) return s;
  if (s.includes("@")) return s.toLowerCase();
  return s.replace(/[\s\-().]/g, "");
}

/** Server-side lookup still accepts legacy formats — client sends + only. */
export function normalizePhoneForLookup(raw: string): string {
  const s = (raw || "").trim().replace(BIDI_MARKS, "");
  if (!s) return s;
  if (s.startsWith("+")) return normalizeLoginInput(s);
  const digits = asciiDigits(s);
  if (!digits) return s;
  if (digits.startsWith("00")) return `+${digits.slice(2)}`;
  if (digits.length === 11 && digits.startsWith("09")) return `+98${digits.slice(1)}`;
  if (digits.length === 10 && digits.startsWith("9")) return `+98${digits}`;
  if (digits.startsWith("98") && digits.length >= 12) return `+${digits}`;
  if (digits.startsWith("0") && digits.length >= 10) return `+98${digits.slice(1)}`;
  return `+${digits}`;
}

/** Display phone with + on the left in RTL pages. */
export function formatPhone(value: string | null | undefined): string {
  const raw = (value || "").trim();
  if (!raw) return "—";
  let n = raw.replace(/[\s\-()]/g, "");
  if (!n.startsWith("+")) {
    n = normalizePhoneForLookup(n);
  }
  return n || "—";
}

export function formatEmail(value: string | null | undefined): string {
  const v = (value || "").trim();
  return v || "—";
}

export function formatId(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  return String(value);
}

export const ltrCell = "font-mono text-start [direction:ltr] [unicode-bidi:isolate]";
export const ltrPhone = "ltr-phone";
