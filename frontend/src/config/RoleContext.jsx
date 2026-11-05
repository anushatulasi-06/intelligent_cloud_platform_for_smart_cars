// Demo-only role state. The header dropdown changes it. With VITE_USE_MOCK=false the
// C4/C5 calls send it to the backend as X-Role / X-User-Id (api/endpoints.js setApiRole).
//   const { role, setRole } = useRole();
import { createContext, useContext, useState } from 'react';
import { DEFAULT_ROLE } from './access.js';
import { setApiRole } from '../api/endpoints.js';

const RoleContext = createContext(null);

export function RoleProvider({ children }) {
  const [role, setRoleState] = useState(DEFAULT_ROLE);
  setApiRole(role); // keep the API headers in step with the dropdown before children fetch
  const setRole = (r) => { setApiRole(r); setRoleState(r); };
  return <RoleContext.Provider value={{ role, setRole }}>{children}</RoleContext.Provider>;
}

export function useRole() {
  return useContext(RoleContext);
}
