import type { ComponentType } from "react";

import {
  IconConversations,
  IconCustomers,
  IconDashboard,
  IconDelivery,
  IconFaq,
  IconFinance,
  IconOrders,
  IconProduction,
  IconProducts,
  IconReports,
  IconSettings,
  IconStatistics,
  IconUsers,
  type IconProps,
} from "@/components/ui/icons";
import type { UserRole } from "@/types/api";

export interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<IconProps>;
  /** Visible only for these roles (default: all staff). */
  roles?: UserRole[];
}

/** Main menu, order per 07-frontend.md §4. */
export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: IconDashboard },
  { href: "/orders", label: "Заказы", icon: IconOrders },
  { href: "/customers", label: "Клиенты", icon: IconCustomers },
  { href: "/products", label: "Товары", icon: IconProducts },
  { href: "/delivery", label: "Доставка", icon: IconDelivery },
  { href: "/production", label: "Производство", icon: IconProduction },
  { href: "/finance", label: "Финансы", icon: IconFinance },
  { href: "/statistics", label: "Статистика", icon: IconStatistics },
  { href: "/reports", label: "Отчёты", icon: IconReports },
  { href: "/faq", label: "FAQ", icon: IconFaq },
  { href: "/conversations", label: "Диалоги", icon: IconConversations },
  { href: "/settings", label: "Настройки", icon: IconSettings },
  { href: "/users", label: "Пользователи", icon: IconUsers, roles: ["ADMIN"] },
];

export function isNavItemActive(href: string, pathname: string | null): boolean {
  if (!pathname) return false;
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
