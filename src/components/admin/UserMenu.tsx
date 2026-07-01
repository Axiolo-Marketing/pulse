import { LogOut, Settings, ShieldUser } from "lucide-react";
import { useNavigate } from "react-router-dom";

import type { AuthUser } from "@/lib/api";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function UserMenu({
  user,
  onSignOut,
}: {
  user: AuthUser;
  onSignOut: () => void;
}): React.ReactElement {
  const navigate = useNavigate();
  const display = user.name?.trim() || user.email;
  const initial = (display[0] ?? "?").toUpperCase();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          <Avatar className="size-6">
            <AvatarFallback className="bg-secondary text-xs text-secondary-foreground">
              {initial}
            </AvatarFallback>
          </Avatar>
          <span className="hidden max-w-40 truncate sm:inline">{display}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        <DropdownMenuLabel className="font-normal">
          {user.name?.trim() ? (
            <div className="text-sm font-medium text-foreground">
              {user.name.trim()}
            </div>
          ) : null}
          <div className="truncate text-xs text-muted-foreground">
            {user.email}
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => navigate("/settings/personal")}>
          <Settings />
          Settings
        </DropdownMenuItem>
        {user.is_superadmin ? (
          <DropdownMenuItem onSelect={() => navigate("/superadmin")}>
            <ShieldUser />
            Superadmin
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onSignOut}>
          <LogOut />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
