import clsx from "clsx";

export interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  /** Accessible/visible label. `null` → decorative spinner (e.g. inside a busy button). */
  label?: string | null;
  /** Show the label next to the spinner (default: screen-reader only). */
  showLabel?: boolean;
  className?: string;
}

const SIZES = { sm: "size-4", md: "size-6", lg: "size-10" } as const;

export function Spinner({ size = "md", label = "Загрузка…", showLabel = false, className }: SpinnerProps) {
  const icon = (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      className={clsx("animate-spin", SIZES[size], className ?? "text-brand-600")}
    >
      <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21.5 12A9.5 9.5 0 0 0 12 2.5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
  if (label === null) return icon;
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-slate-500">
      {icon}
      <span className={showLabel ? undefined : "sr-only"}>{label}</span>
    </span>
  );
}

/** Full-area loading placeholder. */
export function PageSpinner({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div className="flex min-h-[40vh] w-full items-center justify-center">
      <Spinner size="lg" label={label} />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("animate-pulse rounded-md bg-slate-200/70", className ?? "h-4 w-full")} />;
}
