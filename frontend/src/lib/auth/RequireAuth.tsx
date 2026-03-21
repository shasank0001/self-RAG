import { type ReactNode } from "react";
import { useAuth } from "@clerk/react";
import { Navigate, useLocation } from "react-router-dom";

const bypassAuth = import.meta.env.VITE_BYPASS_AUTH === "true";

type RequireAuthProps = {
  children: ReactNode;
};

export function RequireAuth({ children }: RequireAuthProps) {
  if (bypassAuth) {
    return <>{children}</>;
  }

  const { isLoaded, isSignedIn } = useAuth();
  const location = useLocation();

  if (!isLoaded) {
    return <section className="page-card">Loading authentication...</section>;
  }

  if (!isSignedIn) {
    const redirectTo = `${location.pathname}${location.search}`;
    return <Navigate to="/sign-in" replace state={{ redirectTo }} />;
  }

  return <>{children}</>;
}
