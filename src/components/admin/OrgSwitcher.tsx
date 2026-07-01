import { Check, ChevronDown } from "lucide-react";

import type { OrgSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function OrgSwitcher({
  orgs,
  activeOrgId,
  onSwitch,
}: {
  orgs: OrgSummary[];
  activeOrgId: string | null;
  onSwitch: (orgId: string) => void;
}): React.ReactElement | null {
  const active = orgs.find((o) => o.id === activeOrgId) ?? orgs[0];
  if (!active) return null;

  // Single org — a non-interactive label.
  if (orgs.length <= 1) {
    return (
      <span className="rounded-md border border-border px-2.5 py-1 text-sm font-medium text-foreground">
        {active.name}
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          {active.name}
          <ChevronDown className="size-4 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-52">
        {orgs.map((o) => (
          <DropdownMenuItem
            key={o.id}
            onSelect={() => {
              if (o.id !== active.id) onSwitch(o.id);
            }}
            className="gap-2"
          >
            <span className="flex-1 truncate">
              {o.name}
              <span className="text-muted-foreground"> · {o.role}</span>
            </span>
            {o.id === active.id ? (
              <Check className="size-4 text-primary" aria-hidden="true" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
