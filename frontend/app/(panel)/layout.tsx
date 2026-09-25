"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ShadowModeBanner } from "@/components/layout/ShadowModeBanner";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/lib/auth";

/** Tailwind `lg` breakpoint: fixed sidebar from here on, drawer below (07 §5). */
const DESKTOP_QUERY = "(min-width: 1024px)";

/** Protected panel layout: Sidebar + Topbar; unauthenticated users go to /login. */
export default function PanelLayout({ children }: { children: ReactNode }) {
  const { status, signOutReason } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  // The mobile menu is open only for the path it was opened on: any navigation closes it.
  const [menuPath, setMenuPath] = useState<string | null>(null);
  const menuOpen = menuPath !== null && menuPath === pathname;

  useEffect(() => {
    if (status !== "unauthenticated") return;
    // After an explicit logout go to a clean /login; after an expired session come back here.
    const current = `${pathname ?? "/"}${window.location.search}`;
    const next = signOutReason !== "logout" && current !== "/" ? `?next=${encodeURIComponent(current)}` : "";
    router.replace(`/login${next}`);
  }, [status, signOutReason, pathname, router]);

  // The drawer is hidden with `lg:hidden`; close it when the viewport grows so its
  // scroll lock and focus trap do not stay active on desktop.
  useEffect(() => {
    const media = window.matchMedia(DESKTOP_QUERY);
    const onChange = (event: MediaQueryListEvent) => {
      if (event.matches) setMenuPath(null);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner size="lg" label={status === "loading" ? "Проверка авторизации…" : "Переход на страницу входа…"} />
      </div>
    );
  }

  return (
    <div className="min-h-dvh">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Перейти к содержимому
      </a>
      <Sidebar mobileOpen={menuOpen} onMobileClose={() => setMenuPath(null)} />
      <div className="flex min-h-dvh min-w-0 flex-col lg:pl-64">
        <Topbar onMenuClick={() => setMenuPath(pathname ?? "/")} menuOpen={menuOpen} />
        <ShadowModeBanner />
        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 focus:outline-none sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
