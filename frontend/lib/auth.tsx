"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { authApi } from "@/services/api";
import { isApiError, REFRESH_TOKEN_STORAGE_KEY, refreshSession, setAuthHandlers, tokenStore } from "@/services/http";
import type { UserOut, UserRole } from "@/types/api";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

/** Why the user is signed out: explicit logout (no `?next=` redirect back) or expired/lost session. */
export type SignOutReason = "logout" | "session";

export interface AuthContextValue {
  user: UserOut | null;
  status: AuthStatus;
  /** Set when `status === "unauthenticated"` after having been signed in / restoring. */
  signOutReason: SignOutReason | null;
  isAdmin: boolean;
  /** `POST /auth/login`; throws `ApiError` on failure. */
  login: (username: string, password: string) => Promise<UserOut>;
  /** Revokes the refresh token (best effort) and clears the local session. */
  logout: () => Promise<void>;
  /** Re-reads `GET /auth/me` (e.g. after the user edited their own profile). */
  reloadUser: () => Promise<void>;
  hasRole: (...roles: UserRole[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthState {
  user: UserOut | null;
  status: AuthStatus;
  signOutReason: SignOutReason | null;
}

const authenticated = (user: UserOut): AuthState => ({ user, status: "authenticated", signOutReason: null });

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ user: null, status: "loading", signOutReason: null });

  const setSignedOut = useCallback(
    (reason: SignOutReason = "session") => {
      setState({ user: null, status: "unauthenticated", signOutReason: reason });
      queryClient.clear();
    },
    [queryClient],
  );

  // Wire the http layer to this provider.
  useEffect(() => {
    return setAuthHandlers({
      onTokensRefreshed: (pair) => setState(authenticated(pair.user)),
      onAuthFailure: () => setSignedOut("session"),
    });
  }, [setSignedOut]);

  // Restore the session on load: POST /auth/refresh with the stored refresh token.
  useEffect(() => {
    let cancelled = false;
    refreshSession()
      .then((pair) => {
        if (cancelled) return;
        setState(pair ? authenticated(pair.user) : { user: null, status: "unauthenticated", signOutReason: "session" });
      })
      .catch(() => {
        // Backend unreachable: cannot restore the session now (the stored refresh token is kept).
        if (!cancelled) setState({ user: null, status: "unauthenticated", signOutReason: "session" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Logout in another tab removes the refresh token (or storage is cleared) → sign out here too.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      const removed = event.key === REFRESH_TOKEN_STORAGE_KEY && event.newValue === null;
      const cleared = event.key === null;
      if (removed || cleared) {
        tokenStore.clear();
        setSignedOut("logout");
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [setSignedOut]);

  const login = useCallback(
    async (username: string, password: string) => {
      const pair = await authApi.login({ username, password });
      tokenStore.setTokens(pair);
      queryClient.clear();
      setState(authenticated(pair.user));
      return pair.user;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    const refreshToken = tokenStore.getRefreshToken();
    if (refreshToken) {
      try {
        await authApi.logout({ refresh_token: refreshToken });
      } catch (error) {
        // Access token expired/missing: refresh once, then revoke the NEW refresh token
        // (rotation has already revoked the old one). Best effort only.
        if (isApiError(error) && error.status === 401) {
          try {
            const pair = await refreshSession();
            if (pair) await authApi.logout({ refresh_token: pair.refresh_token });
          } catch {
            /* the local session is cleared regardless */
          }
        }
      }
    }
    tokenStore.clear();
    setSignedOut("logout");
  }, [setSignedOut]);

  const reloadUser = useCallback(async () => {
    const user = await authApi.me();
    setState(authenticated(user));
  }, []);

  const value = useMemo<AuthContextValue>(() => {
    const role = state.user?.role;
    return {
      user: state.user,
      status: state.status,
      signOutReason: state.signOutReason,
      isAdmin: role === "ADMIN",
      login,
      logout,
      reloadUser,
      hasRole: (...roles: UserRole[]) => (role ? roles.includes(role) : false),
    };
  }, [state, login, logout, reloadUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
