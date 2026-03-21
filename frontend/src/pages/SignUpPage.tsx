import { SignUp, useAuth } from "@clerk/react";
import { Navigate, useLocation } from "react-router-dom";

const bypassAuth = import.meta.env.VITE_BYPASS_AUTH === "true";

export function SignUpPage() {
  if (bypassAuth) {
    return (
      <section className="auth-page">
        <div className="page-card">
          <h2>Sign up</h2>
          <p>Authentication is bypassed in local screenshot mode.</p>
        </div>
      </section>
    );
  }

  const { isLoaded, isSignedIn } = useAuth();
  const location = useLocation();
  const redirectTo = (location.state as { redirectTo?: string } | null)?.redirectTo ?? "/";

  if (isLoaded && isSignedIn) {
    return <Navigate to={redirectTo} replace />;
  }

  return (
    <section className="auth-page">
      <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" forceRedirectUrl={redirectTo} />
    </section>
  );
}
