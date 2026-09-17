import clsx from "clsx";
import type { ComponentProps } from "react";

import { formatAmount, formatMoney } from "@/lib/format";

export type MoneyTextTone = "default" | "muted" | "success" | "danger" | "auto";

export interface MoneyTextProps extends Omit<ComponentProps<"span">, "children"> {
  /** Amount (`Money` number or numeric string). `null`/`undefined` → `empty`. */
  value: number | string | null | undefined;
  /** Colour: `auto` = red for negative values. Default `default`. */
  tone?: MoneyTextTone;
  /** Append "сомони" (default true). */
  currency?: boolean;
  /** Semibold text. */
  strong?: boolean;
  /** Placeholder for missing values (default "—"). */
  empty?: string;
}

const TONES: Record<Exclude<MoneyTextTone, "auto">, string> = {
  default: "text-slate-900",
  muted: "text-slate-500",
  success: "text-emerald-700",
  danger: "text-red-600",
};

/** `12 500 сомони` with tabular digits, never wrapped. */
export function MoneyText({
  value,
  tone = "default",
  currency = true,
  strong = false,
  empty = "—",
  className,
  ...props
}: MoneyTextProps) {
  const numeric = typeof value === "string" ? Number(value.replace(",", ".")) : value;
  const resolvedTone =
    tone === "auto" ? (typeof numeric === "number" && numeric < 0 ? "danger" : "default") : tone;
  const text = currency ? formatMoney(value, empty) : formatAmount(value, empty);
  return (
    <span
      className={clsx(
        "whitespace-nowrap tabular-nums",
        TONES[resolvedTone],
        strong && "font-semibold",
        className,
      )}
      {...props}
    >
      {text}
    </span>
  );
}
