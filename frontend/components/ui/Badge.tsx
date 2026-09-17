import clsx from "clsx";
import type { ComponentProps } from "react";

import { badgeToneClass, labelOf, toneOf, type BadgeTone } from "@/lib/labels";

export interface BadgeProps extends ComponentProps<"span"> {
  tone?: BadgeTone;
  /** Coloured dot before the text. */
  dot?: boolean;
}

export function Badge({ tone = "gray", dot = false, className, children, ...props }: BadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
        badgeToneClass(tone),
        className,
      )}
      {...props}
    >
      {dot ? <span aria-hidden className="size-1.5 rounded-full bg-current opacity-70" /> : null}
      {children}
    </span>
  );
}

export interface EnumBadgeProps<T extends string> extends Omit<BadgeProps, "tone" | "children"> {
  value: T | null | undefined;
  labels: Record<T, string>;
  tones?: Record<T, BadgeTone>;
}

/** `<EnumBadge value={order.status} labels={ORDER_STATUS_LABELS} tones={ORDER_STATUS_TONES} />` */
export function EnumBadge<T extends string>({ value, labels, tones, ...props }: EnumBadgeProps<T>) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <Badge tone={tones ? toneOf(tones, value) : "gray"} {...props}>
      {labelOf(labels, value)}
    </Badge>
  );
}
