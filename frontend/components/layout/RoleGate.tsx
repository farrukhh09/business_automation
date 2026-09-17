"use client";

import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { IconAlert } from "@/components/ui/icons";
import { useAuth } from "@/lib/auth";
import type { UserRole } from "@/types/api";

export interface RoleGateProps {
  /** Roles allowed to see `children`. */
  roles: UserRole[];
  children: ReactNode;
  /** Rendered for other roles (default: nothing). */
  fallback?: ReactNode;
}

/**
 * Hides UI the current user may not use (`<RoleGate roles={["ADMIN"]}>`).
 * UX only — the backend enforces roles anyway.
 */
export function RoleGate({ roles, children, fallback = null }: RoleGateProps) {
  const { user } = useAuth();
  if (!user || !roles.includes(user.role)) return <>{fallback}</>;
  return <>{children}</>;
}

export function AccessDenied({ description }: { description?: ReactNode }) {
  return (
    <EmptyState
      icon={<IconAlert className="size-6" />}
      title="Недостаточно прав"
      description={description ?? "Этот раздел доступен только администратору."}
    />
  );
}
