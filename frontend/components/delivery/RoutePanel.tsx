"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";

import { MapView, SectionCard, type MapMarker } from "@/components/shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TimeInput } from "@/components/ui/DateInput";
import { IconAlert } from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { formatDistance, formatDuration, formatTime } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { isApiError } from "@/services/http";
import { deliveriesApi } from "@/services/api";
import type { DeliveryListItem, RouteStopOut, WarehouseSettings } from "@/types/api";

export interface RoutePanelProps {
  date: string;
  deliveries: readonly DeliveryListItem[] | undefined;
  warehouse: WarehouseSettings | undefined;
}

const KHUJAND_CENTER = { lat: 40.2842191, lng: 69.6191174 };

function hasCoords(d: DeliveryListItem): d is DeliveryListItem & { latitude: number; longitude: number } {
  return d.latitude !== null && d.longitude !== null;
}

function StopRow({ stop, index }: { stop: RouteStopOut; index: number }) {
  const late = stop.lateness_min > 0;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-slate-200 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
            {index + 1}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-900">{stop.address}</p>
            {stop.recipient_name || stop.phone ? (
              <p className="text-xs text-slate-500">
                {stop.recipient_name}
                {stop.recipient_name && stop.phone ? " · " : ""}
                {stop.phone}
              </p>
            ) : null}
            {stop.courier_comment ? <p className="text-xs text-slate-500 italic">«{stop.courier_comment}»</p> : null}
            {stop.approximate ? (
              <p className="mt-1 text-xs text-amber-700">
                Точка примерная: {stop.approximate_place ?? "дом не найден на карте"} — курьеру позвонить клиенту.{" "}
                <Link href={`/orders/${stop.order_id}`} className="underline">
                  Уточнить точку
                </Link>
              </p>
            ) : null}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-sm font-semibold text-slate-900 tabular-nums">{formatTime(stop.eta)}</p>
          <p className="text-xs text-slate-400 tabular-nums">желаемое {formatTime(stop.desired_time)}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{formatDistance(stop.distance_from_prev_m)} от предыдущей</span>
        <span>·</span>
        <span>{formatDuration(stop.duration_from_prev_s)} в пути</span>
        {late ? <Badge tone="red">Опоздание на {stop.lateness_min} мин</Badge> : null}
        {stop.approximate ? <Badge tone="amber">примерная точка</Badge> : null}
      </div>
    </li>
  );
}

/** "Построить маршрут" + latest plan (09 §9): map with numbered stops, ordered list, unlocated deliveries. */
export function RoutePanel({ date, deliveries, warehouse }: RoutePanelProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [startTime, setStartTime] = useState("");
  const [warehouseError, setWarehouseError] = useState(false);

  const routeQuery = useQuery({
    queryKey: queryKeys.deliveries.route(date),
    queryFn: () => deliveriesApi.route({ date }),
  });

  const optimizeMutation = useMutation({
    mutationFn: () => deliveriesApi.optimize({ date, start_time: startTime || undefined }),
    onSuccess: (plan) => {
      setWarehouseError(false);
      queryClient.setQueryData(queryKeys.deliveries.route(date), plan);
      toast.success("Маршрут построен");
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 422 && error.code === "warehouse_not_configured") {
        setWarehouseError(true);
        return;
      }
      toast.apiError(error);
    },
  });

  const plan = routeQuery.data ?? null;
  const warehouseCenter =
    warehouse?.latitude !== undefined && warehouse?.latitude !== null && warehouse?.longitude !== null && warehouse?.longitude !== undefined
      ? { lat: warehouse.latitude, lng: warehouse.longitude }
      : plan
        ? { lat: plan.start.latitude, lng: plan.start.longitude }
        : KHUJAND_CENTER;

  const { markers, route } = useMemo(() => {
    const warehouseMarker: MapMarker | null =
      plan || (warehouse?.latitude !== undefined && warehouse?.latitude !== null && warehouse?.longitude !== undefined && warehouse?.longitude !== null)
        ? {
            id: "warehouse",
            lat: plan ? plan.start.latitude : (warehouse!.latitude as number),
            lng: plan ? plan.start.longitude : (warehouse!.longitude as number),
            tone: "warehouse",
            label: plan ? plan.start.name : warehouse!.name,
          }
        : null;

    if (plan) {
      const stopMarkers: MapMarker[] = plan.stops.map((stop) => ({
        id: stop.delivery_id,
        lat: stop.latitude,
        lng: stop.longitude,
        // An approximate point (the microdistrict, a landmark) looks like a geocoder candidate, not a house.
        tone: stop.approximate ? "candidate" : "stop",
        number: stop.sequence,
        label: stop.approximate ? `${stop.address} (примерно: ${stop.approximate_place ?? "дом не найден"})` : stop.address,
      }));
      const routePoints: [number, number][] = [
        [plan.start.latitude, plan.start.longitude],
        ...plan.stops.map((stop): [number, number] => [stop.latitude, stop.longitude]),
      ];
      return {
        markers: warehouseMarker ? [warehouseMarker, ...stopMarkers] : stopMarkers,
        route: routePoints,
      };
    }

    const located = (deliveries ?? []).filter(hasCoords);
    const stopMarkers: MapMarker[] = located.map((delivery, index) => ({
      id: delivery.id,
      lat: delivery.latitude,
      lng: delivery.longitude,
      tone: "stop",
      number: index + 1,
      label: delivery.address_formatted ?? delivery.address_raw,
    }));
    return { markers: warehouseMarker ? [warehouseMarker, ...stopMarkers] : stopMarkers, route: undefined };
  }, [plan, deliveries, warehouse]);

  return (
    <SectionCard
      title="Маршрут"
      description="Оптимальный порядок объезда точек доставки на выбранную дату"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <TimeInput
            aria-label="Время старта маршрута"
            value={startTime}
            onValueChange={setStartTime}
            className="w-28"
          />
          <Button onClick={() => optimizeMutation.mutate()} loading={optimizeMutation.isPending}>
            Построить маршрут
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {warehouseError ? (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            <IconAlert className="mt-0.5 size-4 shrink-0" />
            <span>
              Не задан склад в настройках. Укажите адрес и координаты склада, чтобы построить маршрут.{" "}
              <Link href="/settings" className="font-medium underline">
                Перейти в настройки
              </Link>
            </span>
          </div>
        ) : null}

        <MapView center={warehouseCenter} markers={markers} route={route} height={420} />

        {plan ? (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600">
              <span>
                Старт {formatTime(plan.start_time)} от «{plan.start.name}»
              </span>
              <span>Расстояние: {formatDistance(plan.total_distance_m)}</span>
              {/* total_duration_s includes waiting for the delivery windows — only the legs are "в пути" */}
              <span>
                В пути: {formatDuration(plan.stops.reduce((sum, stop) => sum + stop.duration_from_prev_s, 0))}
              </span>
              {plan.stops.length > 0 ? (
                <span>Последняя точка: {formatTime(plan.stops[plan.stops.length - 1].eta)}</span>
              ) : null}
              {plan.stops.some((stop) => stop.approximate) ? (
                <Badge tone="amber">
                  {plan.stops.filter((stop) => stop.approximate).length} из {plan.stops.length} — примерные точки
                </Badge>
              ) : null}
              {plan.distance_source === "haversine" ? (
                <Badge tone="amber">оценка по прямой — OSRM не настроен</Badge>
              ) : null}
            </div>

            {plan.stops.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {plan.stops.map((stop, index) => (
                  <StopRow key={stop.delivery_id} stop={stop} index={index} />
                ))}
              </ul>
            ) : null}

            {plan.unlocated.length > 0 ? (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3">
                <p className="mb-2 text-sm font-medium text-red-800">Без координат (не включены в маршрут)</p>
                <ul className="flex flex-col gap-1.5">
                  {plan.unlocated.map((item) => (
                    <li key={item.delivery_id} className="text-sm text-red-700">
                      <Link href={`/orders/${item.order_id}`} className="underline">
                        {item.address}
                      </Link>{" "}
                      — {item.reason}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-slate-500">
            Маршрут ещё не построен. Нажмите «Построить маршрут», чтобы получить порядок объезда точек.
          </p>
        )}
      </div>
    </SectionCard>
  );
}
