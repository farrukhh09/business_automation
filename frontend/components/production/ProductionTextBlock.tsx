"use client";

import { CopyButton } from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/Button";
import { IconPrinter } from "@/components/ui/icons";

export interface ProductionTextBlockProps {
  text: string;
}

/** Copyable, printable plain-text version of the production summary (SPEC §19). */
export function ProductionTextBlock({ text }: ProductionTextBlockProps) {
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
        <pre className="font-mono text-sm whitespace-pre-wrap text-slate-800">{text}</pre>
      </div>
      <div className="flex flex-wrap gap-2 no-print">
        <CopyButton text={text} label="Скопировать текст" />
        <Button variant="outline" leftIcon={<IconPrinter className="size-4" />} onClick={() => window.print()}>
          Печать
        </Button>
      </div>
    </div>
  );
}
