"use client";

import type { ReactNode } from "react";

import { ToastProvider } from "@/components/ui/Toast";
import { AuthProvider } from "@/lib/auth";
import { QueryProvider } from "@/lib/query";

/** Client-side providers: React Query → Toasts → Auth (auth clears the query cache on logout). */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <ToastProvider>
        <AuthProvider>{children}</AuthProvider>
      </ToastProvider>
    </QueryProvider>
  );
}
