import clsx from "clsx";
import type { LucideIcon } from "lucide-react";

type Props = {
  badge?: string;
  badgeIcon?: LucideIcon;
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
  className?: string;
};

export function PageHeader({ badge, badgeIcon: BadgeIcon, title, subtitle, children, className }: Props) {
  return (
    <header className={clsx("mb-8 space-y-4", className)}>
      {badge && (
        <span className="section-badge inline-flex items-center gap-2">
          {BadgeIcon && <BadgeIcon className="h-3.5 w-3.5 text-accent-cyan" />}
          {badge}
        </span>
      )}
      <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-xl font-black tracking-tight text-white sm:text-2xl lg:text-3xl">{title}</h1>
          {subtitle && <p className="mt-2 text-sm leading-7 text-white/50 sm:text-base">{subtitle}</p>}
        </div>
        {children && <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap">{children}</div>}
      </div>
    </header>
  );
}
