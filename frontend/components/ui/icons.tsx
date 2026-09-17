import type { ReactNode, SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement>;

function createIcon(displayName: string, content: ReactNode) {
  function Icon({ className, ...props }: IconProps) {
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        focusable="false"
        className={className ?? "size-5"}
        {...props}
      >
        {content}
      </svg>
    );
  }
  Icon.displayName = displayName;
  return Icon;
}

export const IconDashboard = createIcon(
  "IconDashboard",
  <>
    <rect x="3" y="3" width="7" height="9" rx="1.5" />
    <rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" />
    <rect x="3" y="16" width="7" height="5" rx="1.5" />
  </>,
);

export const IconOrders = createIcon(
  "IconOrders",
  <>
    <path d="M9 3h6a1 1 0 0 1 1 1v2H8V4a1 1 0 0 1 1-1Z" />
    <path d="M8 5H6a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-2" />
    <path d="M9 11h6M9 15h4" />
  </>,
);

export const IconCustomers = createIcon(
  "IconCustomers",
  <>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
    <path d="M16 4.5a3.5 3.5 0 0 1 0 7" />
    <path d="M18 14.5a6.5 6.5 0 0 1 3.5 5.5" />
  </>,
);

export const IconProducts = createIcon(
  "IconProducts",
  <>
    <path d="M4 21h16" />
    <path d="M5 21v-8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8" />
    <path d="M5 15.5c1.2 1 2.3 1 3.5 0s2.3-1 3.5 0 2.3 1 3.5 0 2.3-1 3.5 0" />
    <path d="M12 11V8" />
    <path d="M12 3.5c.9.9.9 2.1 0 3-.9-.9-.9-2.1 0-3Z" />
  </>,
);

export const IconDelivery = createIcon(
  "IconDelivery",
  <>
    <path d="M3 6h11v10H3z" />
    <path d="M14 9h4l3 3.5V16h-7" />
    <circle cx="7" cy="17.5" r="1.8" />
    <circle cx="17" cy="17.5" r="1.8" />
  </>,
);

export const IconProduction = createIcon(
  "IconProduction",
  <path d="M12 21c-3.9 0-6.5-2.6-6.5-6 0-3.6 2.8-5.5 4-9 2.2 1.4 3 3.5 3 5 1-.6 1.8-1.8 2-3 1.9 1.6 4 4.1 4 7 0 3.4-2.6 6-6.5 6Z" />,
);

export const IconFinance = createIcon(
  "IconFinance",
  <>
    <rect x="2.5" y="6" width="19" height="12" rx="2" />
    <circle cx="12" cy="12" r="2.5" />
    <path d="M6 9.5v5M18 9.5v5" />
  </>,
);

export const IconStatistics = createIcon(
  "IconStatistics",
  <>
    <path d="M4 20h16" />
    <path d="M7 16v-5M12 16V6M17 16v-8" />
  </>,
);

export const IconReports = createIcon(
  "IconReports",
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" />
    <path d="M14 3v5h5" />
    <path d="M9 13h6M9 17h6" />
  </>,
);

export const IconFaq = createIcon(
  "IconFaq",
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6" />
    <path d="M12 17h.01" />
  </>,
);

export const IconConversations = createIcon(
  "IconConversations",
  <>
    <path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-9l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z" />
    <path d="M8 10h8M8 13h5" />
  </>,
);

export const IconSettings = createIcon(
  "IconSettings",
  <>
    <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="10" cy="12" r="2" />
    <circle cx="18" cy="18" r="2" />
  </>,
);

export const IconUsers = createIcon(
  "IconUsers",
  <>
    <circle cx="10" cy="8" r="4" />
    <path d="M3 21a7 7 0 0 1 11-5.7" />
    <path d="M18 14l3 1.2v2.3c0 1.9-1.3 3.2-3 3.8-1.7-.6-3-1.9-3-3.8v-2.3L18 14Z" />
  </>,
);

export const IconMenu = createIcon("IconMenu", <path d="M4 6h16M4 12h16M4 18h16" />);

export const IconClose = createIcon("IconClose", <path d="M6 6l12 12M18 6L6 18" />);

export const IconLogout = createIcon(
  "IconLogout",
  <>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
    <path d="M10 16l-4-4 4-4" />
    <path d="M6 12h10" />
  </>,
);

export const IconChevronLeft = createIcon("IconChevronLeft", <path d="M15 18l-6-6 6-6" />);

export const IconChevronRight = createIcon("IconChevronRight", <path d="M9 18l6-6-6-6" />);

export const IconInbox = createIcon(
  "IconInbox",
  <>
    <path d="M3 13h5l1.5 3h5l1.5-3h5" />
    <path d="M5.5 5h13L21 13v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6l2.5-8Z" />
  </>,
);

export const IconAlert = createIcon(
  "IconAlert",
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5V13" />
    <path d="M12 16.5h.01" />
  </>,
);

export const IconWrench = createIcon(
  "IconWrench",
  <path d="M14.7 6.3a4 4 0 0 0-5.4 5.1l-5.8 5.8a1.8 1.8 0 0 0 2.5 2.5l5.8-5.8a4 4 0 0 0 5.1-5.4l-2.4 2.4-2.2-.5-.5-2.2 2.9-1.9Z" />,
);

export const IconCopy = createIcon(
  "IconCopy",
  <>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
  </>,
);

export const IconPlus = createIcon("IconPlus", <path d="M12 5v14M5 12h14" />);

export const IconSearch = createIcon(
  "IconSearch",
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-3.5-3.5" />
  </>,
);

export const IconRefresh = createIcon(
  "IconRefresh",
  <>
    <path d="M20 11a8 8 0 0 0-14.9-3.5M4 4v4h4" />
    <path d="M4 13a8 8 0 0 0 14.9 3.5M20 20v-4h-4" />
  </>,
);

export const IconMapPin = createIcon(
  "IconMapPin",
  <>
    <path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z" />
    <circle cx="12" cy="9.5" r="2.5" />
  </>,
);

export const IconPrinter = createIcon(
  "IconPrinter",
  <>
    <path d="M7 8V3h10v5" />
    <rect x="3" y="8" width="18" height="9" rx="2" />
    <path d="M7 14h10v7H7z" />
  </>,
);
