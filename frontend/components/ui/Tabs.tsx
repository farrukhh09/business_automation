"use client";

import clsx from "clsx";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export interface TabItem<T extends string = string> {
  value: T;
  label: ReactNode;
  /** Optional panel content rendered below the tab list. */
  content?: ReactNode;
  disabled?: boolean;
  /** Small counter badge. */
  count?: number;
}

export interface TabsProps<T extends string = string> {
  items: readonly TabItem<T>[];
  /** Controlled value. */
  value?: T;
  defaultValue?: T;
  onValueChange?: (value: T) => void;
  ariaLabel?: string;
  variant?: "underline" | "pills";
  className?: string;
  panelClassName?: string;
}

/** WAI-ARIA tabs with roving tabindex and arrow/Home/End keyboard navigation. */
export function Tabs<T extends string = string>({
  items,
  value,
  defaultValue,
  onValueChange,
  ariaLabel,
  variant = "underline",
  className,
  panelClassName,
}: TabsProps<T>) {
  const baseId = useId();
  const [internal, setInternal] = useState<T | undefined>(defaultValue ?? items[0]?.value);
  const current = value ?? internal;
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const select = (next: T) => {
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  };

  const enabledIndexes = items.map((item, index) => (item.disabled ? -1 : index)).filter((index) => index >= 0);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const position = enabledIndexes.indexOf(index);
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight") nextIndex = enabledIndexes[(position + 1) % enabledIndexes.length];
    else if (event.key === "ArrowLeft")
      nextIndex = enabledIndexes[(position - 1 + enabledIndexes.length) % enabledIndexes.length];
    else if (event.key === "Home") nextIndex = enabledIndexes[0];
    else if (event.key === "End") nextIndex = enabledIndexes[enabledIndexes.length - 1];
    if (nextIndex === undefined) return;
    event.preventDefault();
    tabRefs.current[nextIndex]?.focus();
    select(items[nextIndex].value);
  };

  const hasPanels = items.some((item) => item.content !== undefined);
  const activeItem = items.find((item) => item.value === current);

  return (
    <div className={className}>
      <div
        role="tablist"
        aria-label={ariaLabel}
        className={clsx(
          "flex max-w-full gap-1 overflow-x-auto",
          variant === "underline" ? "border-b border-slate-200" : "rounded-lg bg-slate-100 p-1",
        )}
      >
        {items.map((item, index) => {
          const selected = item.value === current;
          return (
            <button
              key={item.value}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              id={`${baseId}-tab-${item.value}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={hasPanels ? `${baseId}-panel-${item.value}` : undefined}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              onClick={() => select(item.value)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={clsx(
                "inline-flex shrink-0 items-center gap-2 text-sm font-medium whitespace-nowrap transition-colors",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-50",
                variant === "underline"
                  ? clsx(
                      "-mb-px border-b-2 px-3 py-2.5",
                      selected
                        ? "border-brand-600 text-brand-700"
                        : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700",
                    )
                  : clsx(
                      "rounded-md px-3 py-1.5",
                      selected ? "bg-white text-slate-900 shadow-xs" : "text-slate-600 hover:text-slate-900",
                    ),
              )}
            >
              {item.label}
              {item.count !== undefined ? (
                <span
                  className={clsx(
                    "rounded-full px-1.5 py-0.5 text-xs",
                    selected ? "bg-brand-100 text-brand-700" : "bg-slate-200 text-slate-600",
                  )}
                >
                  {item.count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      {hasPanels && activeItem ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${activeItem.value}`}
          aria-labelledby={`${baseId}-tab-${activeItem.value}`}
          tabIndex={0}
          className={clsx("pt-4 focus-visible:outline-none", panelClassName)}
        >
          {activeItem.content}
        </div>
      ) : null}
    </div>
  );
}
