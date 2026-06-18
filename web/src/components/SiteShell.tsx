"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, Sparkles, X } from "lucide-react";
import { useState } from "react";
import clsx from "clsx";
import { useAuth } from "@/lib/auth";

const links = [
  { href: "/", label: "خانه" },
  { href: "/adverts", label: "آگهی‌ها" },
  { href: "/dashboard", label: "داشبورد" },
  { href: "/rules", label: "قوانین" },
  { href: "/fees", label: "کارمزد" },
  { href: "/admin", label: "ادمین", admin: true },
];

const footerLinks = [
  { href: "/adverts", label: "آگهی‌ها" },
  { href: "/rules", label: "قوانین" },
  { href: "/fees", label: "کارمزد" },
  { href: "/dashboard", label: "داشبورد" },
];

export function SiteShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isHome = pathname === "/";
  const { user, logout, loading } = useAuth();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative min-h-screen overflow-x-clip">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="aurora-orb -top-24 end-[-20%] h-56 w-56 animate-aurora bg-brand-600/30 sm:-top-32 sm:end-[-10%] sm:h-[420px] sm:w-[420px]" />
        <div
          className="aurora-orb bottom-[-20%] start-[-15%] h-64 w-64 animate-aurora bg-accent-violet/20 sm:bottom-[-15%] sm:start-[-5%] sm:h-[500px] sm:w-[500px]"
          style={{ animationDelay: "-4s" }}
        />
        <div
          className="aurora-orb top-[40%] start-[20%] h-40 w-40 animate-pulse-glow bg-accent-cyan/15 sm:start-[30%] sm:h-64 sm:w-64"
          style={{ animationDelay: "-2s" }}
        />
      </div>

      <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-ink-950/75 pt-[env(safe-area-inset-top)] backdrop-blur-2xl backdrop-saturate-150">
        <div className="mx-auto flex min-w-0 max-w-6xl items-center justify-between gap-2 px-3 py-3 sm:gap-4 sm:px-4 sm:py-3.5 lg:px-6">
          <Link href="/" className="group flex min-w-0 shrink items-center gap-2 sm:gap-3">
            <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500/40 to-accent-violet/25 text-brand-100 shadow-glow-sm ring-1 ring-white/10 transition duration-300 group-hover:shadow-glow group-hover:ring-brand-400/30 sm:h-10 sm:w-10">
              <Sparkles className="h-4 w-4 transition duration-300 group-hover:rotate-12 sm:h-5 sm:w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-bold tracking-tight text-white">Sepid Exchange</p>
              <p className="hidden text-[11px] text-white/40 min-[380px]:block">صرافی یورو · وب و تلگرام</p>
            </div>
          </Link>

          <nav className="hidden items-center gap-0.5 lg:flex">
            {links
              .filter((l) => !l.admin || user?.is_admin)
              .map((l) => {
                const active =
                  pathname === l.href || (l.href !== "/" && pathname.startsWith(l.href));
                return (
                  <Link
                    key={l.href}
                    href={l.href}
                    className={clsx(
                      "rounded-xl px-3.5 py-2 text-sm font-medium transition duration-200",
                      active
                        ? "bg-brand-500/15 text-white shadow-glow-sm ring-1 ring-brand-400/25"
                        : "text-white/50 hover:bg-white/[0.05] hover:text-white/90",
                    )}
                  >
                    {l.label}
                  </Link>
                );
              })}
          </nav>

          <div className="hidden items-center gap-2 md:flex">
            {!loading && user ? (
              <>
                <Link
                  href="/dashboard"
                  className="rounded-lg bg-white/[0.04] px-3 py-1.5 text-sm text-white/75 ring-1 ring-white/10 transition hover:bg-white/[0.07]"
                >
                  {user.display_name}
                </Link>
                <button type="button" onClick={logout} className="btn-ghost py-2 text-xs">
                  خروج
                </button>
              </>
            ) : (
              <Link href="/auth" className="btn-primary py-2.5 text-xs px-4">
                ورود
              </Link>
            )}
          </div>

          <button
            type="button"
            className="btn-ghost shrink-0 p-2 lg:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label="منو"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        {open && (
          <div className="border-t border-white/[0.06] px-4 py-4 lg:hidden">
            <div className="flex flex-col gap-1">
              {links
                .filter((l) => !l.admin || user?.is_admin)
                .map((l) => (
                  <Link
                    key={l.href}
                    href={l.href}
                    onClick={() => setOpen(false)}
                    className="rounded-xl px-4 py-3 text-sm text-white/80 hover:bg-white/[0.05]"
                  >
                    {l.label}
                  </Link>
                ))}
              {!loading && user ? (
                <button type="button" onClick={logout} className="btn-ghost mt-2">
                  خروج
                </button>
              ) : (
                <Link href="/auth" className="btn-primary mt-2" onClick={() => setOpen(false)}>
                  ورود
                </Link>
              )}
            </div>
          </div>
        )}
      </header>

      <main
        className={clsx(
          "relative z-10 min-w-0",
          isHome ? "px-3 sm:px-4 lg:px-6" : "mx-auto max-w-6xl px-3 py-5 sm:px-4 sm:py-8 lg:px-6 lg:py-10",
        )}
      >
        {children}
      </main>

      <footer className="relative z-10 mt-10 border-t border-white/[0.06] bg-ink-950/50 sm:mt-16">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 px-3 py-8 sm:flex-row sm:px-4 sm:py-10 lg:px-6">
          <div className="text-center sm:text-start">
            <p className="text-sm font-semibold text-white/80">Sepid Exchange</p>
            <p className="mt-1 text-xs text-white/35">آگهی در کانال · پیشنهاد از وب و تلگرام</p>
          </div>
          <nav className="flex flex-wrap justify-center gap-x-6 gap-y-2">
            {footerLinks.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="text-xs text-white/40 transition hover:text-white/70"
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
}
