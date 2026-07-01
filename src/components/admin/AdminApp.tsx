import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Gate } from "./Gate";

export default function AdminApp(): React.ReactElement {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={qc}>
      <Gate />
    </QueryClientProvider>
  );
}
