"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type UserProfile = {
  telegram_id: number;
  display_name: string;
  full_name?: string;
  last_name?: string;
  email?: string | null;
  phone_number?: string | null;
  has_telegram: boolean;
  is_web_only: boolean;
  web_account_complete: boolean;
  auth_source?: string;
  is_admin?: boolean;
  username?: string | null;
  address?: string | null;
  has_password?: boolean;
  bot_link?: string;
  channel_link?: string;
  can_publish_adverts?: boolean;
};

type AuthCtx = {
  token: string | null;
  user: UserProfile | null;
  loading: boolean;
  setSession: (token: string, user: UserProfile) => void;
  logout: () => void;
  refreshMe: () => Promise<void>;
};

const AuthContext = createContext<AuthCtx | null>(null);
const STORAGE_KEY = "sepid_web_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    const t = localStorage.getItem(STORAGE_KEY);
    if (!t) {
      setToken(null);
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!res.ok) throw new Error("unauthorized");
      const data = await res.json();
      setToken(t);
      setUser(data.user);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  const setSession = useCallback((t: string, u: UserProfile) => {
    localStorage.setItem(STORAGE_KEY, t);
    setToken(t);
    setUser(u);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ token, user, loading, setSession, logout, refreshMe }),
    [token, user, loading, setSession, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}

export function authHeaders(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}
