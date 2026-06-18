"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { Loader2, RefreshCw, X } from "lucide-react";
import { apiFetch, apiUpload } from "@/lib/api";
import { ltrCell } from "@/lib/format";

type DealAction = {
  label: string;
  callback: string;
  kind?: string;
  sub?: string;
  receipt_index?: number;
};

type DealPanel = {
  offer_id: number;
  advert_id: number;
  seq: number;
  gate_status: string;
  admin_html: string;
  actions: DealAction[];
  bank_cards: { id: string; title: string }[];
  unconfirmed_eur_receipt_index: number | null;
};

type OutboundItem = {
  id: number;
  party: string;
  tag: string;
  msg_type: string;
  body_html?: string;
  caption_html?: string;
  created_at?: number;
};

type FormMode =
  | null
  | "cards"
  | "account_buyer"
  | "account_seller"
  | "rcpt_buyer"
  | "rcpt_seller"
  | "stom";

export function AdminDealPanel({
  token,
  offerId,
  onClose,
  onMessage,
  onError,
}: {
  token: string;
  offerId: number;
  onClose: () => void;
  onMessage: (msg: string) => void;
  onError: (err: string) => void;
}) {
  const [panel, setPanel] = useState<DealPanel | null>(null);
  const [outbound, setOutbound] = useState<OutboundItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [formMode, setFormMode] = useState<FormMode>(null);
  const [textInput, setTextInput] = useState("");
  const [photoFile, setPhotoFile] = useState<File | null>(null);

  const loadPanel = useCallback(async () => {
    if (!token || !offerId) return;
    setBusy(true);
    try {
      const d = await apiFetch<{ panel: DealPanel }>(
        `/api/admin/deal-gates/${offerId}/panel`,
        {},
        token,
      );
      setPanel(d.panel);
    } catch (e) {
      onError(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }, [token, offerId, onError]);

  const loadOutbound = useCallback(async () => {
    if (!token || !offerId) return;
    try {
      const d = await apiFetch<{ items: OutboundItem[] }>(
        `/api/admin/deal-gates/${offerId}/outbound-log`,
        {},
        token,
      );
      setOutbound(d.items);
    } catch {
      setOutbound([]);
    }
  }, [token, offerId]);

  useEffect(() => {
    loadPanel();
    setFormMode(null);
    setTextInput("");
    setPhotoFile(null);
    setOutbound(null);
  }, [loadPanel, offerId]);

  async function runAction(path: string, body?: unknown) {
    setBusy(true);
    onError("");
    try {
      const opts: RequestInit = body
        ? { method: "POST", body: JSON.stringify(body) }
        : { method: "POST" };
      const d = await apiFetch<{ panel: DealPanel; message?: string }>(
        `/api/admin/deal-gates/${offerId}${path}`,
        opts,
        token,
      );
      setPanel(d.panel);
      if (d.message) onMessage(d.message);
      setFormMode(null);
      setTextInput("");
      setPhotoFile(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  async function submitFormUpload(path: string, party?: string) {
    if (!photoFile && !textInput.trim()) {
      onError("متن یا عکس وارد کنید.");
      return;
    }
    setBusy(true);
    onError("");
    try {
      if (photoFile) {
        const form = new FormData();
        form.append("file", photoFile);
        if (party) form.append("party", party);
        if (textInput.trim()) form.append("caption", textInput.trim());
        const d = await apiUpload<{ panel: DealPanel }>(
          `/api/admin/deal-gates/${offerId}${path}`,
          form,
          token,
        );
        setPanel(d.panel);
        onMessage("✅ ثبت شد.");
      } else if (formMode === "account_buyer" || formMode === "account_seller") {
        await runAction("/account", {
          party: formMode === "account_buyer" ? "buyer" : "seller",
          text: textInput.trim(),
        });
        return;
      } else if (formMode === "rcpt_buyer" || formMode === "rcpt_seller") {
        await runAction("/proxy-receipt", {
          party: formMode === "rcpt_buyer" ? "buyer" : "seller",
          text: textInput.trim(),
        });
        return;
      } else if (formMode === "stom") {
        await runAction("/seller-toman-receipt", { text: textInput.trim() });
        return;
      }
      setFormMode(null);
      setTextInput("");
      setPhotoFile(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "خطا");
    } finally {
      setBusy(false);
    }
  }

  function handleAction(act: DealAction) {
    const kind = act.kind;
    const sub = act.sub;
    if (kind === "pxy") {
      if (sub === "byes") return runAction("/proxy-yes", { party: "buyer" });
      if (sub === "syes") return runAction("/proxy-yes", { party: "seller" });
      if (sub === "bacc") {
        setFormMode("account_buyer");
        return;
      }
      if (sub === "sacc") {
        setFormMode("account_seller");
        return;
      }
      if (sub === "brcpt") {
        setFormMode("rcpt_buyer");
        return;
      }
      if (sub === "srcpt") {
        setFormMode("rcpt_seller");
        return;
      }
    }
    if (kind === "pay" && sub === "menu") {
      setFormMode("cards");
      return;
    }
    if (kind === "pay" && sub && sub !== "back") {
      return runAction("/send-toman-card", { card_id: sub });
    }
    if (kind === "tomset") return runAction("/toman-settled");
    if (kind === "buyeur") return runAction("/send-buyer-eur-account");
    if (kind === "eurcfm" && act.receipt_index !== undefined) {
      return runAction("/euro-settled", { receipt_index: act.receipt_index });
    }
    if (kind === "stom" && sub === "go") {
      setFormMode("stom");
      return;
    }
    if (kind === "outlog") {
      loadOutbound();
      return runAction("/replay-outbound");
    }
  }

  const formTitles: Record<Exclude<FormMode, null>, string> = {
    cards: "انتخاب کارت واریز",
    account_buyer: "ثبت حساب خریدار",
    account_seller: "ثبت حساب فروشنده",
    rcpt_buyer: "فیش تومان خریدار",
    rcpt_seller: "فیش یورو فروشنده",
    stom: "فیش تومان برای فروشنده",
  };

  const uploadPath =
    formMode === "account_buyer" || formMode === "account_seller"
      ? "/account/photo"
      : formMode === "rcpt_buyer" || formMode === "rcpt_seller"
        ? "/proxy-receipt/photo"
        : formMode === "stom"
          ? "/seller-toman-receipt/photo"
          : null;

  const uploadParty =
    formMode === "account_buyer" || formMode === "rcpt_buyer"
      ? "buyer"
      : formMode === "account_seller" || formMode === "rcpt_seller"
        ? "seller"
        : undefined;

  return (
    <div className="rounded-xl border border-emerald-500/30 bg-ink-900/80 p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <p className="font-semibold text-emerald-100">
            معامله <span className={ltrCell}>offer #{offerId}</span>
            {panel ? (
              <>
                {" "}
                · آگهی <span className={ltrCell}>#{panel.advert_id}</span> · پیشنهاد{" "}
                <span className={ltrCell}>#{panel.seq}</span>
              </>
            ) : null}
          </p>
          {panel && (
            <p className="mt-1 text-xs text-white/45">
              وضعیت: <strong>{panel.gate_status}</strong>
            </p>
          )}
        </div>
        <button type="button" className="btn-ghost p-1" onClick={onClose} aria-label="بستن">
          <X className="h-4 w-4" />
        </button>
      </div>

      {panel?.admin_html && (
        <div
          className="mb-4 max-h-72 overflow-auto rounded-lg bg-ink-950/60 p-3 text-sm leading-relaxed text-white/80 [&_b]:text-white [&_pre]:mt-1 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-black/30 [&_pre]:p-2 [&_pre]:text-xs"
          dir="rtl"
          dangerouslySetInnerHTML={{ __html: panel.admin_html }}
        />
      )}

      {formMode && formMode !== "cards" && (
        <div className="mb-4 rounded-lg border border-white/10 p-3">
          <p className="mb-2 text-sm font-medium text-white/80">{formTitles[formMode]}</p>
          <textarea
            className="input-field mb-2 min-h-[80px]"
            placeholder="متن حساب یا فیش…"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
          />
          {uploadPath && (
            <input
              type="file"
              accept="image/*"
              className="mb-2 block w-full text-xs text-white/50"
              onChange={(e) => setPhotoFile(e.target.files?.[0] ?? null)}
            />
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-primary text-sm"
              disabled={busy}
              onClick={() =>
                uploadPath
                  ? submitFormUpload(uploadPath, uploadParty)
                  : submitFormUpload("")
              }
            >
              ثبت
            </button>
            <button
              type="button"
              className="btn-ghost text-sm"
              disabled={busy}
              onClick={() => {
                setFormMode(null);
                setTextInput("");
                setPhotoFile(null);
              }}
            >
              انصراف
            </button>
          </div>
        </div>
      )}

      {formMode === "cards" && panel && (
        <div className="mb-4 rounded-lg border border-white/10 p-3">
          <p className="mb-2 text-sm font-medium">انتخاب کارت بانکی</p>
          {panel.bank_cards.length === 0 ? (
            <p className="text-xs text-amber-200">کارت در تنظیمات نیست.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {panel.bank_cards.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="btn-ghost text-sm"
                  disabled={busy}
                  onClick={() => runAction("/send-toman-card", { card_id: c.id })}
                >
                  {c.title}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            className="btn-ghost mt-2 text-xs"
            onClick={() => setFormMode(null)}
          >
            انصراف
          </button>
        </div>
      )}

      {panel && panel.actions.length > 0 && !formMode && (
        <div className="panel-actions mb-4">
          {panel.actions.map((act) => (
            <button
              key={act.callback}
              type="button"
              className={clsx(
                "btn-ghost text-sm",
                act.kind === "tomset" || act.kind === "eurcfm"
                  ? "border-emerald-500/40 text-emerald-100"
                  : "",
              )}
              disabled={busy}
              onClick={() => handleAction(act)}
            >
              {act.label}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-t border-white/10 pt-3">
        <button
          type="button"
          className="btn-ghost gap-1 text-xs"
          disabled={busy}
          onClick={() => runAction("/resync")}
        >
          <RefreshCw className="h-3 w-3" /> همگام با تلگرام
        </button>
        <button
          type="button"
          className="btn-ghost text-xs"
          disabled={busy}
          onClick={() => {
            loadOutbound();
            runAction("/replay-outbound").catch(() => {});
          }}
        >
          لاگ پیام‌های طرفین
        </button>
      </div>

      {outbound && outbound.length > 0 && (
        <ul className="mt-3 max-h-40 space-y-2 overflow-auto text-xs text-white/55">
          {outbound.map((item) => (
            <li key={item.id} className="rounded border border-white/5 p-2">
              <span className="text-white/70">{item.tag}</span> · {item.party} ·{" "}
              {item.msg_type}
            </li>
          ))}
        </ul>
      )}

      {busy && (
        <p className="mt-2 flex items-center gap-2 text-xs text-white/40">
          <Loader2 className="h-3 w-3 animate-spin" /> در حال انجام…
        </p>
      )}
    </div>
  );
}
