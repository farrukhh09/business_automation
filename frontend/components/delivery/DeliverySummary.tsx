import { StatCard } from "@/components/ui/StatCard";
import { IconAlert, IconDelivery, IconMapPin } from "@/components/ui/icons";
import type { DeliveryListItem } from "@/types/api";

export interface DeliverySummaryProps {
  deliveries: readonly DeliveryListItem[] | undefined;
  loading?: boolean;
}

function hasCoordinates(delivery: DeliveryListItem): boolean {
  return delivery.latitude !== null && delivery.longitude !== null;
}

/** Four stat cards for the delivery list on a date: total, geocoded, needs attention, dispatched. */
export function DeliverySummary({ deliveries, loading = false }: DeliverySummaryProps) {
  const total = deliveries?.length ?? 0;
  const withCoords = deliveries?.filter(hasCoordinates).length ?? 0;
  const needsAttention = deliveries?.filter((delivery) => !hasCoordinates(delivery)).length ?? 0;
  const dispatched = deliveries?.filter((delivery) => delivery.status === "DISPATCHED").length ?? 0;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label="Всего доставок" value={total} icon={<IconDelivery className="size-5" />} loading={loading} />
      <StatCard
        label="С координатами"
        value={withCoords}
        tone="green"
        icon={<IconMapPin className="size-5" />}
        loading={loading}
      />
      <StatCard
        label="Требуют уточнения"
        value={needsAttention}
        tone={needsAttention > 0 ? "amber" : "default"}
        icon={<IconAlert className="size-5" />}
        loading={loading}
      />
      <StatCard
        label="Отправлено (Maxim)"
        value={dispatched}
        tone="blue"
        icon={<IconDelivery className="size-5" />}
        loading={loading}
      />
    </div>
  );
}
