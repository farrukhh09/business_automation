import Link from "next/link";

import { buttonClasses } from "@/components/ui/Button";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-4 text-center">
      <p className="text-sm font-semibold text-brand-600">404</p>
      <h1 className="mt-2 text-2xl font-semibold text-slate-900">Страница не найдена</h1>
      <p className="mt-2 text-sm text-slate-500">Проверьте адрес или вернитесь на главную.</p>
      <Link href="/" className={buttonClasses({ className: "mt-6" })}>
        На главную
      </Link>
    </main>
  );
}
