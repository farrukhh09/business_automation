import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/app/login/LoginForm";
import { PageSpinner } from "@/components/ui/Spinner";

export const metadata: Metadata = {
  title: "Вход",
};

export default function LoginPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-gradient-to-b from-brand-50 to-slate-50 px-4 py-10">
      <Suspense fallback={<PageSpinner />}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
