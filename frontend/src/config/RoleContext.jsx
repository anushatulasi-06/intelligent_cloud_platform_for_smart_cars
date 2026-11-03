// Demo-only role state. The header dropdown changes it; nothing is sent to a server.
//   const { role, setRole } = useRole();
import { createContext, useContext, useState } from 'react';
import { DEFAULT_ROLE } from './access.js';

const RoleContext = createContext(null);

export function RoleProvider({ children }) {
  const [role, setRole] = useState(DEFAULT_ROLE);
  return <RoleContext.Provider value={{ role, setRole }}>{children}</RoleContext.Provider>;
}

export function useRole() {
  return useContext(RoleContext);
}
