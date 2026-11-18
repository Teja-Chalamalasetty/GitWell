import { createContext } from "react";
import type { ApiError } from "../api/client";
import type { User } from "../api/types";

export interface AuthState {
  token: string | null;
  user: User | undefined;
  isAdmin: boolean;
  /** True while the stored token is being checked against /auth/me. */
  isLoading: boolean;
  error: ApiError | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthState | null>(null);
