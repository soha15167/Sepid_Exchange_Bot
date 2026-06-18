"use client";

import { useCallback, useEffect, useState } from "react";
import { Send } from "lucide-react";
import { apiFetch } from "@/lib/api";

type Line = { role: string; text: string };

type Props = {
  offerId: number;
  token: string | null;
  enabled?: boolean;
};

export function NegotiationPanel({ offerId, token, enabled = true }: Props) {
  const [lines, setLines] = useState<Line[]>([]);
  const [canPost, setCanPost] = useState(false);
  const [hint, setHint] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    if (!token || !enabled) return;
    apiFetch<{ lines: Line[]; can_post?: boolean; post_hint?: string | null }>(
      `/api/offers/${offerId}/negotiation`,
      {},
      token,
    )
      .then((d) => {
        setLines(d.lines || []);
        setCanPost(Boolean(d.can_post));
        setHint(d.post_hint || "");
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "خطا"));
  }, [offerId, token, enabled]);

  useEffect(() => {
    load();
  }, [load]);

  async function send() {
    if (!token || !draft.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      const d = await apiFetch<{ lines: Line[] }>(
        `/api/offers/${offerId}/negotiation`,
        { method: "POST", body: JSON.stringify({ text: draft.trim() }) },
        token,
      );
      setLines(d.lines || []);
      setDraft("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;
  if (err && lines.length === 0 && !canPost) {
    return <p className="mt-3 text-xs text-red-300">{err}</p>;
  }
  if (lines.length === 0 && !canPost && !hint) return null;

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-ink-950/40 p-4 text-sm">
      <p className="mb-2 font-medium text-white/80">مذاکره</p>
      {lines.length === 0 ? (
        <p className="text-xs text-white/45">هنوز پیامی در مذاکره ثبت نشده.</p>
      ) : (
        <ul className="max-h-48 space-y-2 overflow-y-auto text-xs text-white/70">
          {lines.map((ln, i) => (
            <li key={i} className="break-anywhere rounded-lg bg-white/5 p-2">
              <strong className="text-brand-200">{ln.role}:</strong> {ln.text}
            </li>
          ))}
        </ul>
      )}
      {canPost && (
        <div className="mt-3 space-y-2">
          <textarea
            className="input-field min-h-[72px] text-xs"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="پیام مذاکره (بدون شماره، آیدی یا لینک)"
            maxLength={2000}
          />
          <button
            type="button"
            disabled={busy || draft.trim().length < 1}
            onClick={send}
            className="btn-primary inline-flex w-full items-center justify-center gap-2 py-2 text-xs disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
            ارسال پیام
          </button>
        </div>
      )}
      {hint && !canPost && <p className="mt-2 text-[11px] text-white/40">{hint}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}

