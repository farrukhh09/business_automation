import clsx from "clsx";
import { Suspense, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/Spinner";

export interface TableSkeletonProps {
  rows?: number;
  columns?: number;
  /** Also render stacked-card placeholders below `md` (default true). */
  responsive?: boolean;
  className?: string;
}

/** Placeholder for tables (desktop rows) and stacked cards (mobile). */
export function TableSkeleton({ rows = 5, columns = 6, responsive = true, className }: TableSkeletonProps) {
  const rowItems = Array.from({ length: rows }, (_, index) => index);
  const columnItems = Array.from({ length: columns }, (_, index) => index);
  return (
    <div aria-busy="true" aria-label="Загрузка…" role="status" className={className}>
      <div className={clsx(responsive && "hidden md:block")}>
        <div className="flex gap-4 border-b border-slate-100 bg-slate-50 px-4 py-3">
          {columnItems.map((column) => (
            <Skeleton key={column} className="h-3 flex-1" />
          ))}
        </div>
        {rowItems.map((row) => (
          <div key={row} className="flex gap-4 border-b border-slate-100 px-4 py-3.5 last:border-b-0">
            {columnItems.map((column) => (
              <Skeleton key={column} className={clsx("h-4 flex-1", column === 0 && "max-w-16")} />
            ))}
          </div>
        ))}
      </div>
      {responsive ? (
        <ul className="divide-y divide-slate-100 md:hidden">
          {rowItems.map((row) => (
            <li key={row} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex justify-between gap-3">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-5 w-20" />
              </div>
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Generic page placeholder: header line, filter bar and a table card. */
export function PageSkeleton() {
  return (
    <div aria-busy="true" role="status" aria-label="Загрузка…" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-72 max-w-full" />
      </div>
      <Skeleton className="h-20 w-full rounded-xl" />
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <TableSkeleton rows={6} />
      </div>
    </div>
  );
}

export interface PageSuspenseProps {
  children: ReactNode;
  /** Default: <PageSkeleton />. */
  fallback?: ReactNode;
}

/**
 * `<Suspense>` for client content that calls `useSearchParams()` (useUrlState / usePeriodParams).
 * Required in statically rendered pages, otherwise `next build` fails. Usable from server `page.tsx`.
 */
export function PageSuspense({ children, fallback }: PageSuspenseProps) {
  return <Suspense fallback={fallback ?? <PageSkeleton />}>{children}</Suspense>;
}
