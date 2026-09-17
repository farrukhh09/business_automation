"use client";

import clsx from "clsx";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { IconRefresh } from "@/components/ui/icons";

export interface FilterBarProps {
  /** Filter controls (Input/Select/DateInput with `label`), optionally wrapped in <FilterBarItem>. */
  children: ReactNode;
  /** Always-visible main control (usually the search input), shown above the grid. */
  search?: ReactNode;
  /** Shows the "Сбросить" button. */
  onReset?: () => void;
  /** Number of active filters: badge on the mobile toggle; reset is disabled at 0. */
  activeCount?: number;
  /** Extra buttons on the right (export, create …). */
  actions?: ReactNode;
  /** Grid columns from `lg` (default 4). */
  columns?: 2 | 3 | 4 | 5;
  /** Collapse the filters behind a "Фильтры" toggle below `md` (default true). */
  collapsibleOnMobile?: boolean;
  /** Accessible name of the filter region (default "Фильтры"). */
  ariaLabel?: string;
  className?: string;
}

const LG_COLUMNS = {
  2: "lg:grid-cols-2",
  3: "lg:grid-cols-3",
  4: "lg:grid-cols-4",
  5: "lg:grid-cols-5",
} as const;

function FilterIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className="size-4" aria-hidden>
      <path d="M4 5h16l-6 7.5V19l-4 1.5v-8L4 5Z" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Responsive filter panel: 1 column on phones (collapsible), 2 on `sm`, `columns` on `lg`,
 * with a reset button and an active-filter counter.
 */
export function FilterBar({
  children,
  search,
  onReset,
  activeCount = 0,
  actions,
  columns = 4,
  collapsibleOnMobile = true,
  ariaLabel = "Фильтры",
  className,
}: FilterBarProps) {
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);

  const resetButton = onReset ? (
    <Button
      variant="ghost"
      size="sm"
      onClick={onReset}
      disabled={activeCount === 0}
      leftIcon={<IconRefresh className="size-4" />}
    >
      Сбросить
    </Button>
  ) : null;

  return (
    <section
      aria-label={ariaLabel}
      className={clsx("no-print rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:p-4", className)}
    >
      {search || actions || collapsibleOnMobile ? (
        <div className={clsx("flex flex-wrap items-end gap-2", (search || actions) && "mb-3", !search && !actions && "md:hidden")}>
          {search ? <div className="min-w-0 flex-1 basis-60">{search}</div> : null}
          {collapsibleOnMobile ? (
            <Button
              variant="outline"
              size="md"
              className="md:hidden"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-controls={panelId}
              leftIcon={<FilterIcon />}
            >
              Фильтры
              {activeCount > 0 ? (
                <span className="rounded-full bg-brand-600 px-1.5 py-0.5 text-xs leading-none text-white">
                  {activeCount}
                </span>
              ) : null}
            </Button>
          ) : null}
          {actions ? <div className="flex flex-wrap items-center gap-2 sm:ml-auto">{actions}</div> : null}
        </div>
      ) : null}

      <div
        id={panelId}
        className={clsx(
          collapsibleOnMobile && !expanded ? "hidden md:flex" : "flex",
          "flex-col gap-3 lg:flex-row lg:items-end",
        )}
      >
        <div className={clsx("grid min-w-0 flex-1 grid-cols-1 items-end gap-3 sm:grid-cols-2", LG_COLUMNS[columns])}>
          {children}
        </div>
        {resetButton ? <div className="flex shrink-0 justify-end lg:pb-1">{resetButton}</div> : null}
      </div>
    </section>
  );
}

export interface FilterBarItemProps {
  children: ReactNode;
  /** Span two grid columns from `sm` (e.g. a date range or multi-select chips). */
  wide?: boolean;
  /** Span the whole row. */
  full?: boolean;
  className?: string;
}

/** Grid cell inside <FilterBar>. */
export function FilterBarItem({ children, wide = false, full = false, className }: FilterBarItemProps) {
  return (
    <div className={clsx("min-w-0", wide && "sm:col-span-2", full && "col-span-full", className)}>{children}</div>
  );
}
