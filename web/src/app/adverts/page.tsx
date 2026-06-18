"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Plus, Radio, RefreshCw } from "lucide-react";
import { apiFetch, type Advert } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { AdvertChannelCard } from "@/components/AdvertChannelCard";
import { PageHeader } from "@/components/ui/PageHeader";

export default function AdvertsPage() {
  const { token, user } = useAuth();
  const [items, setItems] = useState<Advert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [pages, setPages] = useState(1);

  function load(p = page) {
    setLoading(true);
    setError("");
    apiFetch<{ items: Advert[]; pages: number }>(`/api/adverts?page=${p}&limit=20`, {}, token)
      .then((d) => {
        setItems(d.items);
        setPages(d.pages);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load(page);
  }, [token, page]);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        badge="فید زنده"
        badgeIcon={Radio}
        title="آگهی‌های فعال"
        subtitle="همان جزئیات کانال Sepid Exchange — با امکان ثبت پیشنهاد از وب"
      >
        <div className="flex w-full gap-2 sm:w-auto">
          <button
            type="button"
            onClick={() => load(page)}
            className="btn-ghost shrink-0 p-2.5"
            aria-label="بروزرسانی"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          {user && (
            <Link href="/dashboard/new-advert" className="btn-primary inline-flex min-w-0 flex-1 gap-2 py-2.5 text-sm sm:flex-none">
              <Plus className="h-4 w-4" />
              آگهی جدید
            </Link>
          )}
        </div>
      </PageHeader>

      {loading && items.length === 0 && (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-64 animate-pulse rounded-2xl bg-white/[0.04]" />
          ))}
        </div>
      )}
      {error && (
        <p className="rounded-xl border border-red-400/20 bg-red-500/10 p-4 text-sm text-red-200">{error}</p>
      )}

      <div className="space-y-5">
        {items.map((ad, i) => (
          <motion.div
            key={ad.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.04, duration: 0.35 }}
          >
            <AdvertChannelCard ad={ad} index={i} />
          </motion.div>
        ))}
      </div>

      {!loading && items.length === 0 && !error && (
        <div className="bento-card py-16 text-center">
          <p className="text-white/40">آگهی فعالی یافت نشد.</p>
          {user && (
            <Link href="/dashboard/new-advert" className="btn-primary mt-4 inline-flex text-sm">
              اولین آگهی را ثبت کنید
            </Link>
          )}
        </div>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3 pt-4">
          <button
            type="button"
            disabled={page <= 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="btn-ghost py-2 text-sm disabled:opacity-30"
          >
            قبلی
          </button>
          <span className="text-sm text-white/50">
            {page + 1} / {pages}
          </span>
          <button
            type="button"
            disabled={page >= pages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="btn-ghost py-2 text-sm disabled:opacity-30"
          >
            بعدی
          </button>
        </div>
      )}
    </div>
  );
}
