"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { isNavItemActive, NAV_ITEMS } from "@/components/layout/navigation";
import { Drawer } from "@/components/ui/Drawer";
import { IconClose, IconProducts } from "@/components/ui/icons";
import { useAuth } from "@/lib/auth";
import { APP_NAME } from "@/lib/config";

function Brand() {
  return (
    <Link
      href="/"
      className="flex items-center gap-2.5 rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
    >
      <span aria-hidden className="flex size-9 items-center justify-center rounded-lg bg-brand-600 text-white">
        <IconProducts className="size-5" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-slate-900">{APP_NAME}</span>
        <span className="block text-xs text-slate-500">Админ-панель</span>
      </span>
    </Link>
  );
}

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { hasRole } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.roles || hasRole(...item.roles));

  return (
    <nav aria-label="Главное меню" className="px-3 py-4">
      <ul className="flex flex-col gap-0.5">
        {items.map((item) => {
          const active = isNavItemActive(item.href, pathname);
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
                  active ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                )}
              >
                <Icon className={clsx("size-5 shrink-0", active ? "text-brand-600" : "text-slate-400")} />
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export interface SidebarProps {
  mobileOpen: boolean;
  onMobileClose: () => void;
}

/** Fixed sidebar on ≥1024px; drawer below (07 §5). */
export function Sidebar({ mobileOpen, onMobileClose }: SidebarProps) {
  return (
    <>
      <aside className="no-print hidden border-r border-slate-200 bg-white lg:fixed lg:inset-y-0 lg:left-0 lg:z-30 lg:flex lg:w-64 lg:flex-col">
        <div className="flex h-16 shrink-0 items-center border-b border-slate-100 px-5">
          <Brand />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <SidebarNav />
        </div>
      </aside>

      <Drawer
        open={mobileOpen}
        onClose={onMobileClose}
        side="left"
        ariaLabel="Главное меню"
        hideHeader
        widthClassName="w-72 max-w-[85vw]"
        containerClassName="lg:hidden"
      >
        <div className="flex h-16 items-center justify-between gap-2 border-b border-slate-100 px-4">
          <Brand />
          <button
            type="button"
            onClick={onMobileClose}
            className="rounded-md p-2 text-slate-500 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-brand-600"
            aria-label="Закрыть меню"
          >
            <IconClose className="size-5" />
          </button>
        </div>
        <SidebarNav onNavigate={onMobileClose} />
      </Drawer>
    </>
  );
}
