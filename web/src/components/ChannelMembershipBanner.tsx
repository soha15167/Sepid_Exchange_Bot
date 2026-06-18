"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api";

type MembershipStatus = {
  allowed: boolean;
  reason?: string | null;
  message?: string | null;
  channel_url?: string | null;
};

type Props = {
  token: string | null;
};

export function ChannelMembershipBanner({ token }: Props) {
  const [status, setStatus] = useState<MembershipStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    try {
      const d = await apiFetch<MembershipStatus>("/api/adverts/meta/channel-membership", {}, token);
      setStatus(d);
    } catch {
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  if (!status || status.allowed) return null;

  return (
    <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-4 text-sm text-amber-100">
      <p>{status.message || "برای ثبت آگهی باید عضو کانال باشید."}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {status.reason === "web_only" && (
          <Link href="/dashboard/profile" className="btn-ghost inline-flex py-1.5 text-xs text-amber-100">
            راهنمای اتصال تلگرام
          </Link>
        )}
        {status.channel_url && (
          <a
            href={status.channel_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-ghost inline-flex gap-1 py-1.5 text-xs text-amber-100"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            عضویت در کانال
          </a>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={load}
          className="btn-ghost inline-flex gap-1 py-1.5 text-xs text-amber-100"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
          عضو شدم — بررسی مجدد
        </button>
      </div>
    </div>
  );
}
