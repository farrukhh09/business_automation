"use client";

import "leaflet/dist/leaflet.css";

import { divIcon, latLngBounds, type DivIcon, type LeafletMouseEvent } from "leaflet";
import { useEffect, useMemo, useRef } from "react";
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";

import type { LatLngTuple, MapMarker, MapMarkerTone, MapViewProps } from "@/components/shared/map-types";

const OSM_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/* ------------------------------------------------------------------ */
/* Icons (DivIcon, no image assets)                                    */
/* ------------------------------------------------------------------ */

const PIN_COLORS: Record<Exclude<MapMarkerTone, "warehouse">, { background: string; border: string; text: string }> = {
  stop: { background: "#c2410c", border: "#ffffff", text: "#ffffff" },
  candidate: { background: "#ffffff", border: "#d97706", text: "#92400e" },
  selected: { background: "#0284c7", border: "#ffffff", text: "#ffffff" },
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

const iconCache = new Map<string, DivIcon>();

function markerIcon(tone: MapMarkerTone, number: string | number | undefined): DivIcon {
  const text = number === undefined || number === null ? "" : String(number).slice(0, 3);
  const cacheKey = `${tone}|${text}`;
  const cached = iconCache.get(cacheKey);
  if (cached) return cached;

  let icon: DivIcon;
  if (tone === "warehouse") {
    icon = divIcon({
      className: "bakery-map-marker",
      iconSize: [34, 34],
      iconAnchor: [17, 17],
      tooltipAnchor: [0, -18],
      html:
        '<div style="width:34px;height:34px;border-radius:9px;background:#1e293b;border:2px solid #fff;' +
        'box-shadow:0 2px 6px rgba(15,23,42,.35);display:flex;align-items:center;justify-content:center;">' +
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 10.5 12 4l9 6.5"/>' +
        '<path d="M5 9.5V20h14V9.5"/><path d="M10 20v-5h4v5"/></svg></div>',
    });
  } else {
    const colors = PIN_COLORS[tone];
    const size = tone === "selected" ? 34 : 30;
    const fontSize = text.length > 2 ? 10 : 12;
    icon = divIcon({
      className: "bakery-map-marker",
      iconSize: [size, size],
      // The rotated square's tip sits ~0.71·size below its centre.
      iconAnchor: [size / 2, Math.round(size / 2 + size * 0.71)],
      tooltipAnchor: [0, -Math.round(size * 0.6)],
      html:
        `<div style="width:${size}px;height:${size}px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);` +
        `background:${colors.background};border:2px solid ${colors.border};` +
        `${tone === "candidate" ? "border-width:3px;" : ""}` +
        'box-shadow:0 2px 6px rgba(15,23,42,.35);display:flex;align-items:center;justify-content:center;">' +
        `<span style="transform:rotate(45deg);color:${colors.text};font:600 ${fontSize}px/1 ui-sans-serif,system-ui,sans-serif;">` +
        `${escapeHtml(text)}</span></div>`,
    });
  }
  iconCache.set(cacheKey, icon);
  return icon;
}

/* ------------------------------------------------------------------ */
/* Behaviour helpers                                                   */
/* ------------------------------------------------------------------ */

function toTuple(center: MapViewProps["center"]): LatLngTuple {
  return Array.isArray(center) ? [center[0], center[1]] : [center.lat, center.lng];
}

function isFiniteLatLng(lat: number, lng: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
}

interface ViewControllerProps {
  points: readonly LatLngTuple[];
  /** Changes when the set of markers / route changes (→ refit). */
  structureKey: string;
  /** Changes when any coordinate changes (→ refit only if something is out of view). */
  coordsKey: string;
  center: LatLngTuple;
  zoom: number;
  enabled: boolean;
}

/** Fits the view to the points; keeps the user's viewport while points only move inside it. */
function ViewController({ points, structureKey, coordsKey, center, zoom, enabled }: ViewControllerProps) {
  const map = useMap();
  const lastStructureKey = useRef<string | null>(null);
  const pointsRef = useRef(points);
  const centerRef = useRef(center);
  const zoomRef = useRef(zoom);

  useEffect(() => {
    pointsRef.current = points;
    centerRef.current = center;
    zoomRef.current = zoom;
  });

  useEffect(() => {
    if (!enabled) return;
    const currentPoints = pointsRef.current;
    const structureChanged = lastStructureKey.current !== structureKey;
    lastStructureKey.current = structureKey;

    if (currentPoints.length === 0) {
      if (structureChanged) map.setView(centerRef.current, zoomRef.current);
      return;
    }

    const outOfView = currentPoints.some((point) => !map.getBounds().contains(point));
    if (!structureChanged && !outOfView) return;

    if (currentPoints.length === 1) {
      const target = currentPoints[0];
      if (structureChanged) map.setView(target, Math.max(zoomRef.current, 15));
      else map.panTo(target);
      return;
    }
    map.fitBounds(latLngBounds([...currentPoints]), { padding: [40, 40], maxZoom: 16 });
  }, [map, structureKey, coordsKey, enabled]);

  // Re-center on `center` prop changes when there is nothing to fit.
  const [centerLat, centerLng] = center;
  useEffect(() => {
    if (pointsRef.current.length > 0 && enabled) return;
    map.setView([centerLat, centerLng], map.getZoom());
  }, [map, centerLat, centerLng, enabled]);

  return null;
}

/** Keeps tiles correct when the container is resized (drawers, tabs, responsive layouts). */
function ResizeWatcher() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(container);
    return () => observer.disconnect();
  }, [map]);
  return null;
}

function ClickHandler({ onMapClick }: { onMapClick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(event: LeafletMouseEvent) {
      onMapClick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

interface MarkerItemProps {
  marker: MapMarker;
  draggable: boolean;
  onMarkerDrag?: MapViewProps["onMarkerDrag"];
  onMarkerClick?: MapViewProps["onMarkerClick"];
}

function MarkerItem({ marker, draggable, onMarkerDrag, onMarkerClick }: MarkerItemProps) {
  const tone = marker.tone ?? "stop";
  const icon = markerIcon(tone, marker.number);
  const eventHandlers = useMemo(
    () => ({
      click: () => onMarkerClick?.(marker.id),
      dragend: (event: { target: { getLatLng: () => { lat: number; lng: number } } }) => {
        const position = event.target.getLatLng();
        onMarkerDrag?.(marker.id, position.lat, position.lng);
      },
    }),
    [marker.id, onMarkerClick, onMarkerDrag],
  );

  return (
    <Marker
      position={[marker.lat, marker.lng]}
      icon={icon}
      draggable={draggable}
      title={marker.label}
      alt={marker.label ?? (marker.number !== undefined ? `Точка ${marker.number}` : "Точка")}
      zIndexOffset={tone === "selected" ? 1000 : tone === "warehouse" ? 500 : 0}
      eventHandlers={eventHandlers}
    >
      {marker.label ? (
        <Tooltip direction="top" opacity={0.95}>
          {marker.label}
        </Tooltip>
      ) : null}
    </Marker>
  );
}

/* ------------------------------------------------------------------ */
/* Map                                                                 */
/* ------------------------------------------------------------------ */

/** Leaflet implementation of <MapView>. Import <MapView> instead (SSR-safe wrapper). */
export default function MapViewClient({
  center,
  zoom = 13,
  markers = [],
  route,
  onMapClick,
  draggableMarkerId = null,
  onMarkerDrag,
  onMarkerClick,
  fitBounds = true,
  scrollWheelZoom = true,
  ariaLabel = "Карта",
}: MapViewProps) {
  const centerTuple = toTuple(center);
  const validMarkers = useMemo(() => markers.filter((marker) => isFiniteLatLng(marker.lat, marker.lng)), [markers]);
  const validRoute = useMemo(
    () => (route ?? []).filter((point) => isFiniteLatLng(point[0], point[1])),
    [route],
  );

  const points = useMemo<LatLngTuple[]>(
    () => [...validMarkers.map((marker): LatLngTuple => [marker.lat, marker.lng]), ...validRoute],
    [validMarkers, validRoute],
  );
  const structureKey = `${validMarkers.map((marker) => String(marker.id)).join("|")}#${validRoute.length}`;
  const coordsKey = points.map((point) => `${point[0].toFixed(6)},${point[1].toFixed(6)}`).join(";");

  return (
    <div role="region" aria-label={ariaLabel} className="size-full">
      <MapContainer
        center={centerTuple}
        zoom={zoom}
        scrollWheelZoom={scrollWheelZoom}
        className={onMapClick ? "size-full cursor-crosshair" : "size-full"}
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer url={OSM_TILES} attribution={OSM_ATTRIBUTION} maxZoom={19} />
        <ViewController
          points={points}
          structureKey={structureKey}
          coordsKey={coordsKey}
          center={centerTuple}
          zoom={zoom}
          enabled={fitBounds}
        />
        <ResizeWatcher />
        {onMapClick ? <ClickHandler onMapClick={onMapClick} /> : null}
        {validRoute.length > 1 ? (
          <Polyline
            positions={validRoute as LatLngTuple[]}
            pathOptions={{ color: "#c2410c", weight: 4, opacity: 0.75, lineJoin: "round" }}
          />
        ) : null}
        {validMarkers.map((marker) => (
          <MarkerItem
            key={marker.id}
            marker={marker}
            draggable={draggableMarkerId !== null && draggableMarkerId === marker.id}
            onMarkerDrag={onMarkerDrag}
            onMarkerClick={onMarkerClick}
          />
        ))}
      </MapContainer>
    </div>
  );
}
