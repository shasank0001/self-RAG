import { useAuth } from "@clerk/react";
import { type PropsWithChildren, useEffect } from "react";
import { setTokenGetter } from "@/lib/auth/token";

function ClerkTokenBridge() {
  const { getToken } = useAuth();

  useEffect(() => {
    setTokenGetter(async () => getToken());
    return () => {
      setTokenGetter(null);
    };
  }, [getToken]);

  return null;
}

export function AuthProvider({ children }: PropsWithChildren) {
  return (
    <>
      <ClerkTokenBridge />
      {children}
    </>
  );
}
