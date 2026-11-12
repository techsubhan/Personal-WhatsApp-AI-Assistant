import { createContext, useContext, useState, ReactNode } from "react";
import { setAdminKey, clearAdminKey } from "../api/client";

interface AuthContextValue {
  isAuthed: boolean;
  signIn: (key: string) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthed, setIsAuthed] = useState<boolean>(() => !!localStorage.getItem("admin_key"));

  const signIn = (key: string) => {
    setAdminKey(key);
    setIsAuthed(true);
  };

  const signOut = () => {
    clearAdminKey();
    setIsAuthed(false);
  };

  return <AuthContext.Provider value={{ isAuthed, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
