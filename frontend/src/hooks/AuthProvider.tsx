import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, setUnauthorizedHandler, tokenStore } from "../api/client";
import { keys } from "../api/hooks";
import type { LoginResponse, User } from "../api/types";
import { type AuthState, AuthContext } from "./authContext";

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => tokenStore.get());

  const me = useQuery({
    queryKey: keys.me(token),
    queryFn: () => api.get<User>("/auth/me"),
    enabled: token !== null,
    retry: false,
    staleTime: Infinity,
  });

  const logout = useCallback(() => {
    tokenStore.clear();
    setToken(null);
    qc.clear();
  }, [qc]);

  // PRD 15: a 401 from any call clears the token and redirects to /login.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      logout();
      navigate("/login", { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [logout, navigate]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await api.post<LoginResponse>("/auth/login", { email, password }, false);
      tokenStore.set(result.access_token);
      qc.setQueryData(keys.me(result.access_token), result.user);
      setToken(result.access_token);
    },
    [qc],
  );

  const value = useMemo<AuthState>(
    () => ({
      token,
      user: me.data,
      isAdmin: me.data?.role === "admin",
      isLoading: token !== null && me.isPending,
      error: me.error instanceof ApiError ? me.error : null,
      login,
      logout,
    }),
    [token, me.data, me.isPending, me.error, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
