import { EmptyState } from "@/components/ui/EmptyState";
import { IconProduction } from "@/components/ui/icons";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { formatQuantity } from "@/lib/format";
import type { ProductionItem } from "@/types/api";

export interface ProductionTableProps {
  items: readonly ProductionItem[];
}

/**
 * Product — quantity table for the kitchen: large, high-contrast, easy to read at a glance
 * (SPEC §19). Only confirmed-and-later orders are counted, so an empty table is expected
 * on a quiet day — the caller shows an explanatory EmptyState in that case.
 */
export function ProductionTable({ items }: ProductionTableProps) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon={<IconProduction className="size-6" />}
        title="На эту дату товаров нет"
        description="В сводку попадают только подтверждённые и более поздние по статусу заказы (начиная с «Подтверждён»)."
      />
    );
  }

  return (
    <Table caption="Производственная сводка" containerClassName="rounded-xl border border-slate-200 bg-white">
      <THead>
        <tr>
          <TH className="text-base">Товар</TH>
          <TH align="right" className="text-base">
            Количество
          </TH>
        </tr>
      </THead>
      <TBody>
        {items.map((item) => (
          <TR key={item.product_id ?? item.product_name}>
            <TD className="py-4 text-xl font-medium text-slate-900">{item.product_name}</TD>
            <TD align="right" className="py-4 text-xl font-semibold tabular-nums text-slate-900">
              {formatQuantity(item.quantity, item.unit)}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
