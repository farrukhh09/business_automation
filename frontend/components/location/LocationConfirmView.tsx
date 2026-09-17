"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { MapView, type MapMarker } from "@/components/shared/MapView";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { IconMapPin } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { queryKeys } from "@/lib/query";
import { publicLocationApi } from "@/services/api";
import { getErrorMessage, isApiError } from "@/services/http";

export interface LocationConfirmViewProps {
  token: string;
}

const MARKER_ID = "point";

type GeoState = "idle" | "locating" | "denied" | "unavailable";

/**
 * PUBLIC "Отметьте точку доставки" page: no auth, mobile-first. Loads the location request by
 * token, lets the customer drag a pin (or use their GPS position) and submits the chosen point.
 */
export function LocationConfirmView({ token }: LocationConfirmViewProps) {
  const query = useQuery({
    queryKey: queryKeys.publicLocation.byToken(token),
    queryFn: () => publicLocationApi.get(token),
    retry: false,
  });

  const [point, setPoint] = useState<{ lat: number; lng: number } | null>(null);
  const [geoState, setGeoState] = useState<GeoState>("idle");
  const [submitted, setSubmitted] = useState(false);

  // Seed the point once from the loaded location (then leave it to the customer) — adjusted
  // during render instead of an effect (react.dev/learn/you-might-not-need-an-effect).
  const [seededForToken, setSeededForToken] = useState<string | null>(null);
  if (query.data && seededForToken !== token) {
    setSeededForToken(token);
    if (
      query.data.latitude !== null &&
      query.data.latitude !== undefined &&
      query.data.longitude !== null &&
      query.data.longitude !== undefined
    ) {
      setPoint({ lat: query.data.latitude, lng: query.data.longitude });
    }
  }

  const submitMutation = useMutation({
    mutationFn: (value: { lat: number; lng: number }) =>
      publicLocationApi.submit(token, { latitude: value.lat, longitude: value.lng }),
    onSuccess: () => setSubmitted(true),
  });

  const locateMe = () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGeoState("unavailable");
      return;
    }
    setGeoState("locating");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setPoint({ lat: position.coords.latitude, lng: position.coords.longitude });
        setGeoState("idle");
      },
      (geoError) => {
        setGeoState(geoError.code === geoError.PERMISSION_DENIED ? "denied" : "unavailable");
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  };

  if (query.isLoading) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-10">
          <Spinner size="lg" label="Загрузка…" />
        </div>
      </Card>
    );
  }

  if (query.isError) {
    const expired = isApiError(query.error) && query.error.status === 410;
    return (
      <Card>
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <IconMapPin className="size-8 text-slate-300" />
          <h2 className="text-lg font-semibold text-slate-900">
            {expired ? "Ссылка больше не действует" : "Не удалось открыть страницу"}
          </h2>
          <p className="text-sm text-slate-500">
            {expired
              ? "Срок действия ссылки истёк. Напишите нам в Instagram, чтобы получить новую."
              : getErrorMessage(query.error, "Проверьте ссылку и повторите попытку.")}
          </p>
        </div>
      </Card>
    );
  }

  const data = query.data;
  if (!data) return null;

  if (data.expired) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <IconMapPin className="size-8 text-slate-300" />
          <h2 className="text-lg font-semibold text-slate-900">Ссылка больше не действует</h2>
          <p className="text-sm text-slate-500">Срок действия ссылки истёк. Напишите нам в Instagram, чтобы получить новую.</p>
        </div>
      </Card>
    );
  }

  if (submitted) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <div className="flex size-12 items-center justify-center rounded-full bg-emerald-50 text-emerald-600">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-6" aria-hidden>
              <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-slate-900">Спасибо! Точка сохранена</h2>
          <p className="text-sm text-slate-500">Можно вернуться в Instagram.</p>
        </div>
      </Card>
    );
  }

  const markers: MapMarker[] = point ? [{ id: MARKER_ID, lat: point.lat, lng: point.lng, tone: "selected" }] : [];

  return (
    <div className="flex flex-col gap-4">
      <Card bodyClassName="flex flex-col gap-3 p-4">
        <div>
          <p className="text-sm font-medium text-slate-900">{data.business_name}</p>
          {data.address_raw ? <p className="mt-0.5 text-sm text-slate-500">{data.address_raw}</p> : null}
          {data.used ? (
            <p className="mt-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
              Точка уже была подтверждена ранее. Вы можете уточнить её ещё раз.
            </p>
          ) : null}
        </div>

        <MapView
          center={point ?? data.suggested ?? data.city_center}
          zoom={point ? 16 : data.suggested ? 15 : 13}
          markers={markers}
          onMapClick={(lat, lng) => setPoint({ lat, lng })}
          draggableMarkerId={point ? MARKER_ID : null}
          onMarkerDrag={(_id, lat, lng) => setPoint({ lat, lng })}
          height={320}
        />

        <Button variant="outline" fullWidth onClick={locateMe} loading={geoState === "locating"}>
          Моё местоположение
        </Button>
        {geoState === "denied" ? (
          <p className="text-xs text-amber-700">
            Доступ к геолокации запрещён. Поставьте точку на карте вручную.
          </p>
        ) : null}
        {geoState === "unavailable" ? (
          <p className="text-xs text-amber-700">
            Не удалось определить местоположение. Поставьте точку на карте вручную.
          </p>
        ) : null}

        {submitMutation.isError ? (
          <p className="text-sm text-red-600">{getErrorMessage(submitMutation.error)}</p>
        ) : null}

        <Button
          size="lg"
          fullWidth
          disabled={!point}
          loading={submitMutation.isPending}
          onClick={() => point && submitMutation.mutate(point)}
        >
          Подтвердить точку
        </Button>
        {/* TODO: проверить носителем таджикского языка */}
        <p className="text-center text-xs text-slate-400">Нуқтаи расониданро дар харита қайд кунед</p>
      </Card>
    </div>
  );
}
