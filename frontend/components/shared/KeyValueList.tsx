import clsx from "clsx";
import type { ReactNode } from "react";

export interface KeyValueItem {
  /** React key (default: index). */
  key?: string;
  label: ReactNode;
  /** `null`/`undefined`/`""`/`false` → `empty` placeholder; `0` is shown. */
  value: ReactNode;
  /** Skip the row entirely (e.g. role-dependent fields). */
  hidden?: boolean;
  /** Span all columns (long text, addresses). Only for `layout="grid"`. */
  fullWidth?: boolean;
  /** Small text under the value. */
  hint?: ReactNode;
}

export interface KeyValueListProps {
  items: readonly KeyValueItem[];
  /**
   * `grid` (default): label above value in 1–3 responsive columns;
   * `rows`: label left / value right with dividers (compact detail cards).
   */
  layout?: "grid" | "rows";
  /** Columns from `sm` for `layout="grid"` (default 2). */
  columns?: 1 | 2 | 3;
  /** Placeholder for empty values (default "—"). */
  empty?: ReactNode;
  className?: string;
}

const GRID_COLUMNS = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
} as const;

function isEmptyValue(value: ReactNode): boolean {
  return value === null || value === undefined || value === "" || value === false;
}

/** Semantic `<dl>` of label/value pairs for detail views. */
export function KeyValueList({ items, layout = "grid", columns = 2, empty = "—", className }: KeyValueListProps) {
  const visible = items.filter((item) => !item.hidden);

  if (layout === "rows") {
    return (
      <dl className={clsx("divide-y divide-slate-100", className)}>
        {visible.map((item, index) => (
          <div
            key={item.key ?? index}
            className="flex flex-col gap-1 py-2.5 first:pt-0 last:pb-0 sm:flex-row sm:items-baseline sm:gap-4"
          >
            <dt className="shrink-0 text-sm text-slate-500 sm:w-44">{item.label}</dt>
            <dd className="min-w-0 flex-1 text-sm break-words text-slate-900">
              {isEmptyValue(item.value) ? <span className="text-slate-400">{empty}</span> : item.value}
              {item.hint ? <p className="mt-0.5 text-xs text-slate-500">{item.hint}</p> : null}
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <dl className={clsx("grid gap-x-6 gap-y-4", GRID_COLUMNS[columns], className)}>
      {visible.map((item, index) => (
        <div
          key={item.key ?? index}
          className={clsx(
            "min-w-0",
            item.fullWidth && (columns === 3 ? "sm:col-span-2 lg:col-span-3" : columns === 2 ? "sm:col-span-2" : ""),
          )}
        >
          <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">{item.label}</dt>
          <dd className="mt-1 text-sm break-words text-slate-900">
            {isEmptyValue(item.value) ? <span className="text-slate-400">{empty}</span> : item.value}
            {item.hint ? <p className="mt-0.5 text-xs text-slate-500">{item.hint}</p> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
