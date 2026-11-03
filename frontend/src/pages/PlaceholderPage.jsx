import { PAGES, visibleTabs, frameFor } from '../config/pages.js';
import { useActiveTab } from '../components/PageTabs.jsx';
import { useRole } from '../config/RoleContext.jsx';

// Shows only the page title and its frame id until the real page is built.
// For tabbed pages, the frame id follows the active tab.
export default function PlaceholderPage({ pageKey }) {
  const { role } = useRole();
  const page = PAGES.find((p) => p.key === pageKey);
  const tabs = visibleTabs(page, role);
  const tabKey = useActiveTab(tabs);
  const tab = tabs.find((t) => t.key === tabKey);
  return (
    <div className="placeholder">
      <h1>{page.label}{tabs.length > 1 ? ` · ${tab.label}` : ''}</h1>
      <p className="frame-id">{frameFor(tab ?? page, role)}</p>
    </div>
  );
}
