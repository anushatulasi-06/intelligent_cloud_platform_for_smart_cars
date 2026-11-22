// Who is signed in. The role comes from the JWT issued by POST /login, not from a dropdown,
// and every page reads it with useRole().
//
//   const { role, user, signIn, signOut } = useRole();
//
// Pages that only need the role keep using `role` exactly as before. The backend re-checks the
// role on every call with require_role, so this is for showing the right pages, not security.
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { login as loginRequest } from '../api/endpoints.js';
import { SESSION_EXPIRED, clearSession, getSession, setSession } from '../api/session.js';

const RoleContext = createContext(null);

export function RoleProvider({ children }) {
  const [session, setLocal] = useState(getSession);

  // A 401 from any call means the token expired: drop the session and the router shows F1.
  useEffect(() => {
    const onExpired = () => setLocal(null);
    window.addEventListener(SESSION_EXPIRED, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED, onExpired);
  }, []);

  const signIn = useCallback(async (email, password) => {
    const res = await loginRequest({ email, password });
    setLocal(setSession({ token: res.access_token, user: res.user, role: res.role }));
    return res.role;
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setLocal(null);
  }, []);

  const value = { role: session?.role ?? null, user: session?.user ?? null,
                  signedIn: !!session, signIn, signOut };
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole() {
  return useContext(RoleContext);
}
