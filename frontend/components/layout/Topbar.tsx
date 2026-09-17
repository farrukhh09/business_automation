"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { IconLogout, IconMenu } from "@/components/ui/icons";
import { useAuth } from "@/lib/auth";
import { APP_NAME } from "@/lib/config";
import { USER_ROLE_LABELS, USER_ROLE_TONES } from "@/lib/labels";

export interface TopbarProps {
  onMenuClick: () => void;
  menuOpen?: boolean;
}

export function Topbar({ onMenuClick, menuOpen = false }: TopbarProps) {
  const { user, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  // logout() always ends signed out; the panel layout then redirects to /login.
  const handleLogout = async () => {
    setLoggingOut(true);
    await logout();
  };

  const displayName = user?.full_name || user?.username || "";
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  return (
    <header className="no-print sticky top-0 z-20 flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-8">
      <button
        type="button"
        onClick={onMenuClick}
        className="-ml-2 rounded-md p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-600 lg:hidden"
        aria-label="Открыть меню"
        aria-expanded={menuOpen}
        aria-haspopup="dialog"
      >
        <IconMenu className="size-6" />
      </button>

      <span className="truncate text-sm font-semibold text-slate-900 lg:hidden">{APP_NAME}</span>

      <div className="ml-auto flex items-center gap-3">
        {user ? (
          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="max-w-48 truncate text-sm font-medium text-slate-900">{displayName}</p>
              <p className="text-xs text-slate-500">@{user.username}</p>
            </div>
            <Badge tone={USER_ROLE_TONES[user.role]} className="hidden md:inline-flex">
              {USER_ROLE_LABELS[user.role]}
            </Badge>
            <span
              aria-hidden
              className="flex size-9 items-center justify-center rounded-full bg-slate-100 text-sm font-semibold text-slate-600 sm:hidden"
            >
              {initials || "?"}
            </span>
          </div>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          onClick={handleLogout}
          loading={loggingOut}
          leftIcon={<IconLogout className="size-4" />}
          aria-label="Выйти из системы"
        >
          <span className="hidden sm:inline">Выйти</span>
        </Button>
      </div>
    </header>
  );
}
