import clsx from "clsx";

import { IconAlert } from "@/components/ui/icons";
import { labelOf, ORDER_MISSING_FIELD_LABELS } from "@/lib/labels";

export interface MissingFieldsPanelProps {
  missing: readonly string[];
  title?: string;
  className?: string;
}

/** Warning panel listing `OrderDetail.missing_fields` (03-business-rules.md §1.3), Russian labels. Renders nothing when empty. */
export function MissingFieldsPanel({
  missing,
  title = "Не хватает данных для подтверждения заказа",
  className,
}: MissingFieldsPanelProps) {
  if (!missing || missing.length === 0) return null;
  return (
    <div
      role="status"
      className={clsx("flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4", className)}
    >
      <span
        aria-hidden
        className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600"
      >
        <IconAlert className="size-5" />
      </span>
      <div className="min-w-0">
        <p className="font-medium text-amber-900">{title}</p>
        <ul className="mt-1 list-inside list-disc text-sm text-amber-800">
          {missing.map((field) => (
            <li key={field}>{labelOf(ORDER_MISSING_FIELD_LABELS, field, field)}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
