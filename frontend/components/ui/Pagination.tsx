import clsx from "clsx";

import { IconChevronLeft, IconChevronRight } from "@/components/ui/icons";

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
  className?: string;
}

type PageToken = number | "gap-left" | "gap-right";

function pageTokens(page: number, pageCount: number): PageToken[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i + 1);
  const tokens: PageToken[] = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pageCount - 1, page + 1);
  if (start > 2) tokens.push("gap-left");
  for (let p = start; p <= end; p += 1) tokens.push(p);
  if (end < pageCount - 1) tokens.push("gap-right");
  tokens.push(pageCount);
  return tokens;
}

const baseButton =
  "inline-flex h-9 min-w-9 items-center justify-center rounded-md px-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-40";

export function Pagination({ page, pageSize, total, onPageChange, disabled = false, className }: PaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  return (
    <nav
      aria-label="Постраничная навигация"
      className={clsx("flex flex-col items-center justify-between gap-3 px-1 py-3 sm:flex-row", className)}
    >
      <p className="text-sm text-slate-500">
        {total === 0 ? "Нет записей" : `Показано ${from}–${to} из ${total}`}
      </p>
      {pageCount > 1 ? (
        <ul className="flex items-center gap-1">
          <li>
            <button
              type="button"
              className={clsx(baseButton, "text-slate-600 hover:bg-slate-100")}
              onClick={() => onPageChange(page - 1)}
              disabled={disabled || page <= 1}
              aria-label="Предыдущая страница"
            >
              <IconChevronLeft className="size-4" />
            </button>
          </li>
          {pageTokens(page, pageCount).map((token) =>
            typeof token === "number" ? (
              <li key={token} className={token === page ? undefined : "hidden sm:block"}>
                <button
                  type="button"
                  className={clsx(
                    baseButton,
                    token === page ? "bg-brand-600 text-white" : "text-slate-700 hover:bg-slate-100",
                  )}
                  onClick={() => onPageChange(token)}
                  disabled={disabled}
                  aria-current={token === page ? "page" : undefined}
                  aria-label={`Страница ${token}`}
                >
                  {token}
                </button>
              </li>
            ) : (
              <li key={token} aria-hidden className="hidden px-1 text-slate-400 sm:block">
                …
              </li>
            ),
          )}
          <li className="px-2 text-sm text-slate-500 sm:hidden" aria-hidden>
            {page} / {pageCount}
          </li>
          <li>
            <button
              type="button"
              className={clsx(baseButton, "text-slate-600 hover:bg-slate-100")}
              onClick={() => onPageChange(page + 1)}
              disabled={disabled || page >= pageCount}
              aria-label="Следующая страница"
            >
              <IconChevronRight className="size-4" />
            </button>
          </li>
        </ul>
      ) : null}
    </nav>
  );
}
