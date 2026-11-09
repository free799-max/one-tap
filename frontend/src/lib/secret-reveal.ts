const STORAGE_KEY = "integration_secret_reveal_authorization";

export interface SecretRevealAuthorization {
  token: string;
  expiresAt: string;
}

export function getSecretRevealAuthorization(): SecretRevealAuthorization | null {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (!stored) return null;

  try {
    const value = JSON.parse(stored) as Partial<SecretRevealAuthorization>;
    const expiresAt = typeof value.expiresAt === "string" ? value.expiresAt : "";
    const expiry = Date.parse(expiresAt);
    if (
      typeof value.token !== "string" ||
      !value.token ||
      !Number.isFinite(expiry) ||
      expiry <= Date.now()
    ) {
      clearSecretRevealAuthorization();
      return null;
    }
    return { token: value.token, expiresAt };
  } catch {
    clearSecretRevealAuthorization();
    return null;
  }
}

export function saveSecretRevealAuthorization(token: string, expiresAt: string) {
  const authorization: SecretRevealAuthorization = { token, expiresAt };
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(authorization));
}

export function clearSecretRevealAuthorization() {
  sessionStorage.removeItem(STORAGE_KEY);
}
