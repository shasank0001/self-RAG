type TokenGetter = () => Promise<string | null>;

let tokenGetter: TokenGetter | null = null;

export function setTokenGetter(getter: TokenGetter | null): void {
  tokenGetter = getter;
}

export async function getToken(): Promise<string | null> {
  if (!tokenGetter) {
    return null;
  }

  return tokenGetter();
}
