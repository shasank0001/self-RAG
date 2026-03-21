import { StrictMode, type ComponentProps, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { ClerkProvider as RawClerkProvider } from "@clerk/react";
import { AppProviders } from "./app/providers";
import { router } from "./routes";
import "./styles.css";

type ViteClerkProviderProps = Omit<ComponentProps<typeof RawClerkProvider>, "publishableKey">;
const ClerkProvider = RawClerkProvider as unknown as (props: ViteClerkProviderProps) => ReactElement;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ClerkProvider>
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>
    </ClerkProvider>
  </StrictMode>,
);
