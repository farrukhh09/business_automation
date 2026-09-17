"use client";

import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";

/** Error boundary for panel pages: shows the error message and a retry button (07 §5). */
export default function PanelError({ error, reset }: { error: unknown; reset: () => void }) {
  return (
    <Card>
      <ErrorState error={error} title="Не удалось отобразить раздел" onRetry={reset} />
    </Card>
  );
}
