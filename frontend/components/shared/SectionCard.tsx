import type { ReactNode } from "react";

import { Card, type CardProps } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Spinner";

export interface SectionCardProps extends Omit<CardProps, "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** Buttons/links in the header (right side). */
  actions?: ReactNode;
  /** Small icon before the title. */
  icon?: ReactNode;
  /** Shows `skeleton` (default: 3 text lines) instead of children. */
  loading?: boolean;
  skeleton?: ReactNode;
  /** Shows ErrorState (ApiError.detail) with a retry button instead of children. */
  error?: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  /** Shows EmptyState instead of children. */
  empty?: boolean;
  emptyTitle?: ReactNode;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
  emptyIcon?: ReactNode;
}

function DefaultSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
    </div>
  );
}

/**
 * Card with a titled header and built-in loading / error / empty states:
 * error → loading → empty → children (in this priority).
 */
export function SectionCard({
  title,
  description,
  actions,
  icon,
  loading = false,
  skeleton,
  error,
  onRetry,
  retrying,
  empty = false,
  emptyTitle = "Нет данных",
  emptyDescription,
  emptyAction,
  emptyIcon,
  padded = true,
  bodyClassName,
  children,
  ...props
}: SectionCardProps) {
  let body: ReactNode;
  let bodyPadded = padded;
  if (error) {
    body = <ErrorState error={error} onRetry={onRetry} retrying={retrying} className="py-8" />;
    bodyPadded = true;
  } else if (loading) {
    body = <div aria-busy="true">{skeleton ?? <DefaultSkeleton />}</div>;
    bodyPadded = skeleton ? padded : true;
  } else if (empty) {
    body = (
      <EmptyState
        title={emptyTitle}
        description={emptyDescription}
        action={emptyAction}
        icon={emptyIcon}
        className="py-8"
      />
    );
    bodyPadded = true;
  } else {
    body = children;
  }

  return (
    <Card
      title={
        icon ? (
          <span className="flex items-center gap-2">
            <span aria-hidden className="flex shrink-0 text-slate-400 [&>svg]:size-5">
              {icon}
            </span>
            <span className="min-w-0">{title}</span>
          </span>
        ) : (
          title
        )
      }
      description={description}
      actions={actions}
      padded={bodyPadded}
      bodyClassName={bodyClassName}
      {...props}
    >
      {body}
    </Card>
  );
}
