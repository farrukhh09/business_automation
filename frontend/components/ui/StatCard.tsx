import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/Spinner";

export type StatCardTone = "default" | "brand" | "green" | "amber" | "red" | "blue";

const ICON_TONES: Record<StatCardTone, string> = {
  default: "bg-slate-100 text-slate-600",
  brand: "bg-brand-50 text-brand-600",
  green: "bg-emerald-50 text-emerald-600",
  amber: "bg-amber-50 text-amber-600",
  red: "bg-red-50 text-red-600",
  blue: "bg-sky-50 text-sky-600",
};

export interface StatCardProps {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: StatCardTone;
  loading?: boolean;
  /** Makes the whole card a link. */
  href?: string;
  className?: string;
}

export function StatCard({ label, value, hint, icon, tone = "default", loading = false, href, className }: StatCardProps) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        {icon ? (
          <span aria-hidden className={clsx("flex size-9 shrink-0 items-center justify-center rounded-lg", ICON_TONES[tone])}>
            {icon}
          </span>
        ) : null}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">
        {loading ? <Skeleton className="h-8 w-24" /> : value}
      </div>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </>
  );

  const classes = clsx(
    "block rounded-xl border border-slate-200 bg-white p-4 shadow-xs",
    href && "transition hover:border-brand-300 hover:shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
    className,
  );

  if (href) {
    return (
      <Link href={href} className={classes}>
        {body}
      </Link>
    );
  }
  return <div className={classes}>{body}</div>;
}
