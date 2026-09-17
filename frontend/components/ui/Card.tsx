import clsx from "clsx";
import type { ComponentProps, ReactNode } from "react";

export interface CardProps extends Omit<ComponentProps<"section">, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  /** Buttons/links in the card header. */
  actions?: ReactNode;
  footer?: ReactNode;
  /** Padding for the body (default true). */
  padded?: boolean;
  bodyClassName?: string;
}

export function Card({
  title,
  description,
  actions,
  footer,
  padded = true,
  className,
  bodyClassName,
  children,
  ...props
}: CardProps) {
  const hasHeader = Boolean(title || description || actions);
  return (
    <section className={clsx("rounded-xl border border-slate-200 bg-white shadow-xs", className)} {...props}>
      {hasHeader ? (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
          <div className="min-w-0">
            {title ? <h2 className="text-base font-semibold text-slate-900">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-sm text-slate-500">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={clsx(padded && "p-4 sm:p-5", bodyClassName)}>{children}</div>
      {footer ? <footer className="border-t border-slate-100 px-4 py-3 sm:px-5">{footer}</footer> : null}
    </section>
  );
}
