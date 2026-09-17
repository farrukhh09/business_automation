import clsx from "clsx";
import Link from "next/link";
import type { MouseEvent, ReactNode } from "react";

import { customerDisplayName } from "@/lib/format";

/** Minimal customer shape (OrderCustomerRef, ConversationCustomerRef, CustomerListItem …). */
export interface CustomerRef {
  id: number;
  name?: string | null;
  username?: string | null;
  phone?: string | null;
}

export interface CustomerLinkProps {
  /** `null`/`undefined` → "—". */
  customer: CustomerRef | null | undefined;
  /** Show `@username` next to the name (when both exist). */
  showUsername?: boolean;
  /** Custom link text (default: name → @username → phone → "Клиент #id"). */
  children?: ReactNode;
  className?: string;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}

/** Link to `/customers/[id]`. Safe inside clickable rows (OrderTable ignores clicks on links). */
export function CustomerLink({ customer, showUsername = false, children, className, onClick }: CustomerLinkProps) {
  if (!customer) return <span className="text-slate-400">—</span>;
  const withUsername = showUsername && customer.name && customer.username;
  return (
    <span className={clsx("inline-flex min-w-0 max-w-full items-baseline gap-1.5", className)}>
      <Link
        href={`/customers/${customer.id}`}
        onClick={onClick}
        className="truncate font-medium text-slate-900 hover:text-brand-700 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
      >
        {children ?? customerDisplayName(customer)}
      </Link>
      {withUsername ? <span className="truncate text-xs text-slate-500">@{customer.username}</span> : null}
    </span>
  );
}

export interface PhoneLinkProps {
  phone: string | null | undefined;
  className?: string;
}

/** `tel:` link (tap-to-call on phones); `null` → "—". */
export function PhoneLink({ phone, className }: PhoneLinkProps) {
  if (!phone) return <span className="text-slate-400">—</span>;
  return (
    <a
      href={`tel:${phone.replace(/[^\d+]/g, "")}`}
      className={clsx(
        "whitespace-nowrap text-slate-700 tabular-nums hover:text-brand-700 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
        className,
      )}
    >
      {phone}
    </a>
  );
}
