"use client";

import clsx from "clsx";
import dynamic from "next/dynamic";

import type { MapViewProps } from "@/components/shared/map-types";
import { Skeleton } from "@/components/ui/Spinner";

function MapLoading() {
  return (
    <div className="relative size-full" role="status" aria-label="Загрузка карты…">
      <Skeleton className="size-full rounded-none" />
      <span className="absolute inset-0 flex items-center justify-center text-sm text-slate-500">
        Загрузка карты…
      </span>
    </div>
  );
}

/** Leaflet touches `window` on import → client only. */
const LeafletMap = dynamic(() => import("@/components/shared/MapViewClient"), {
  ssr: false,
  loading: MapLoading,
});

/**
 * OpenStreetMap map (Leaflet), rendered only in the browser.
 *
 * ```tsx
 * <MapView
 *   center={[40.2842191, 69.6191174]}
 *   markers={[{ id: "wh", lat, lng, tone: "warehouse", label: "Склад" }, { id: 12, lat, lng, number: 1 }]}
 *   route={[[lat, lng], …]}
 *   onMapClick={(lat, lng) => …}
 *   draggableMarkerId="point"
 *   onMarkerDrag={(id, lat, lng) => …}
 *   height={420}
 * />
 * ```
 */
export function MapView(props: MapViewProps) {
  const { height = 400, className } = props;
  return (
    <div
      className={clsx("relative w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-100", className)}
      style={{ height }}
    >
      <LeafletMap {...props} />
    </div>
  );
}

export type { LatLngTuple, MapMarker, MapMarkerTone, MapViewProps } from "@/components/shared/map-types";
