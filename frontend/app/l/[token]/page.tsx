import type { Metadata } from "next";

import { LocationConfirmView } from "@/components/location/LocationConfirmView";

export const metadata: Metadata = {
  title: "Отметьте точку доставки",
  referrer: "no-referrer",
};

/** PUBLIC page (no auth, mobile-first): the customer pins the delivery point. */
export default async function LocationPickPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return (
    <main className="min-h-dvh bg-slate-50 px-4 py-6 sm:py-10">
      <div className="mx-auto flex w-full max-w-md flex-col gap-4">
        <header className="text-center">
          <h1 className="text-xl font-semibold text-slate-900">Отметьте точку доставки</h1>
          <p className="mt-1 text-sm text-slate-500">Поставьте метку на карте там, куда нужно доставить заказ.</p>
        </header>
        <LocationConfirmView token={token} />
      </div>
    </main>
  );
}
