"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, FileText } from "lucide-react";
import { apiFetch } from "@/lib/api";

type Props = {
  apiPath: string;
  backHref?: string;
};

export function InfoDocPage({ apiPath, backHref = "/" }: Props) {
  const [doc, setDoc] = useState<{ title: string; text: string } | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiFetch<{ title: string; text: string }>(apiPath, {})
      .then(setDoc)
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [apiPath]);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link href={backHref} className="btn-ghost inline-flex gap-2 py-2 text-sm">
        <ArrowRight className="h-4 w-4" />
        بازگشت
      </Link>

      {loading && (
        <div className="bento-card h-64 animate-pulse bg-white/[0.02]" />
      )}

      {err && (
        <div className="bento-card border-red-400/20 p-6 text-center">
          <p className="text-sm text-red-300">{err}</p>
          <button type="button" onClick={() => window.location.reload()} className="btn-ghost mt-4 text-sm">
            تلاش مجدد
          </button>
        </div>
      )}

      {doc && !loading && (
        <article className="bento-card overflow-hidden">
          <div className="border-b border-white/[0.06] bg-brand-500/5 px-4 py-6 sm:px-10 sm:py-8">
            <span className="section-badge mb-4">
              <FileText className="h-3.5 w-3.5 text-accent-cyan" />
              متن رسمی کانال
            </span>
            <h1 className="text-2xl font-black text-white sm:text-3xl">{doc.title}</h1>
          </div>
          <div className="prose-doc break-anywhere px-4 py-6 sm:px-10 sm:py-10">
            {doc.text.split(/\n\n+/).map((p, i) => (
              <p key={i}>{p.trim()}</p>
            ))}
          </div>
        </article>
      )}
    </div>
  );
}
