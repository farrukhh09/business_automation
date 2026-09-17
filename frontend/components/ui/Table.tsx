import clsx from "clsx";
import type { ComponentProps, KeyboardEvent, ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";

/* ------------------------------------------------------------------ */
/* Primitives                                                          */
/* ------------------------------------------------------------------ */

export interface TableProps extends ComponentProps<"table"> {
  /** Accessible caption (visually hidden). */
  caption?: string;
  containerClassName?: string;
}

/** Table inside a horizontally scrollable container (07 §5). */
export function Table({ caption, className, containerClassName, children, ...props }: TableProps) {
  return (
    <div className={clsx("w-full overflow-x-auto", containerClassName)}>
      <table className={clsx("min-w-full divide-y divide-slate-200 text-sm", className)} {...props}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        {children}
      </table>
    </div>
  );
}

export function THead({ className, ...props }: ComponentProps<"thead">) {
  return <thead className={clsx("bg-slate-50", className)} {...props} />;
}

export function TBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody className={clsx("divide-y divide-slate-100 bg-white", className)} {...props} />;
}

export function TR({ className, ...props }: ComponentProps<"tr">) {
  return <tr className={clsx("align-top", className)} {...props} />;
}

type Align = "left" | "center" | "right";
const ALIGN: Record<Align, string> = { left: "text-left", center: "text-center", right: "text-right" };

export function TH({ className, align = "left", scope = "col", ...props }: ComponentProps<"th"> & { align?: Align }) {
  return (
    <th
      scope={scope}
      className={clsx(
        "px-3 py-2.5 text-xs font-semibold tracking-wide whitespace-nowrap text-slate-500 uppercase first:pl-4 last:pr-4",
        ALIGN[align],
        className,
      )}
      {...props}
    />
  );
}

export function TD({ className, align = "left", ...props }: ComponentProps<"td"> & { align?: Align }) {
  return (
    <td className={clsx("px-3 py-3 text-slate-700 first:pl-4 last:pr-4", ALIGN[align], className)} {...props} />
  );
}

/* ------------------------------------------------------------------ */
/* DataTable                                                           */
/* ------------------------------------------------------------------ */

export interface DataTableColumn<T> {
  key: string;
  header: ReactNode;
  cell: (row: T, index: number) => ReactNode;
  align?: Align;
  className?: string;
  headerClassName?: string;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: readonly T[] | undefined;
  rowKey: (row: T, index: number) => string | number;
  caption?: string;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
  /** Makes rows clickable (and keyboard-activatable). */
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  className?: string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  error,
  onRetry,
  emptyTitle = "Нет данных",
  emptyDescription,
  emptyAction,
  onRowClick,
  rowClassName,
  className,
}: DataTableProps<T>) {
  if (error) {
    return <ErrorState error={error} onRetry={onRetry} className={className} />;
  }

  if (loading && !rows?.length) {
    return (
      <div className={clsx("flex items-center justify-center py-12", className)}>
        <Spinner label="Загрузка…" />
      </div>
    );
  }

  if (!rows || rows.length === 0) {
    return (
      <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} className={className} />
    );
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (!onRowClick) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onRowClick(row);
    }
  };

  return (
    <Table caption={caption} containerClassName={className} aria-busy={loading || undefined}>
      <THead>
        <tr>
          {columns.map((column) => (
            <TH key={column.key} align={column.align} className={column.headerClassName}>
              {column.header}
            </TH>
          ))}
        </tr>
      </THead>
      <TBody>
        {rows.map((row, index) => (
          <TR
            key={rowKey(row, index)}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            onKeyDown={onRowClick ? (event) => handleKeyDown(event, row) : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            className={clsx(
              onRowClick &&
                "cursor-pointer hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-600",
              rowClassName?.(row),
            )}
          >
            {columns.map((column) => (
              <TD key={column.key} align={column.align} className={column.className}>
                {column.cell(row, index)}
              </TD>
            ))}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
