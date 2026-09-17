/** Types for <MapView> (kept separate so importing them never pulls Leaflet into the bundle). */

/** `[latitude, longitude]` */
export type LatLngTuple = [number, number];

/**
 * - `warehouse` — dark square with a house glyph (route start);
 * - `stop` — orange numbered pin (route stop / delivery point);
 * - `candidate` — white pin with amber border (geocoder candidate);
 * - `selected` — blue pin (currently selected / editable point).
 */
export type MapMarkerTone = "warehouse" | "stop" | "candidate" | "selected";

export interface MapMarker {
  id: string | number;
  lat: number;
  lng: number;
  /** Tooltip text and marker title (address, recipient …). */
  label?: string;
  /** Text inside the pin (route sequence, candidate index). Max ~3 chars. */
  number?: number | string;
  /** Default `stop`. */
  tone?: MapMarkerTone;
}

export interface MapViewProps {
  /** Initial centre (and the view when there are no markers). */
  center: LatLngTuple | { lat: number; lng: number };
  /** Initial zoom (default 13). */
  zoom?: number;
  markers?: readonly MapMarker[];
  /** Route polyline points in order. */
  route?: readonly LatLngTuple[];
  /** Click on the map (e.g. "поставить точку на карте"). Enables a crosshair cursor. */
  onMapClick?: (lat: number, lng: number) => void;
  /** Marker id that can be dragged. */
  draggableMarkerId?: string | number | null;
  /** Fired at the end of a drag of `draggableMarkerId`. */
  onMarkerDrag?: (id: string | number, lat: number, lng: number) => void;
  /** Fired when a marker is clicked. */
  onMarkerClick?: (id: string | number) => void;
  /** CSS height (default 400). */
  height?: number | string;
  /**
   * Fit the view to markers + route (default true): on mount, when the set of marker ids / the route changes,
   * and when a marker ends up outside the visible area.
   */
  fitBounds?: boolean;
  /** Zoom with the mouse wheel (default true). */
  scrollWheelZoom?: boolean;
  /** Accessible label of the map region (default "Карта"). */
  ariaLabel?: string;
  className?: string;
}
