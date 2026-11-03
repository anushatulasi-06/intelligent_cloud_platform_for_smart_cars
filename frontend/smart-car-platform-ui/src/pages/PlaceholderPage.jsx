import { PAGES } from '../config/pages.js';
import { useActiveTab } from '../components/PageTabs.jsx';

// Shows only the page title and its frame id until the real page is built.
// For tabbed pages, the frame id follows the active tab.
export default function PlaceholderPage({ pageKey }) {
  const page = PAGES.find((p) => p.key === pageKey);
  const tabKey = useActiveTab(page.tabs);
  const tab = page.tabs.find((t) => t.key === tabKey);
  return (
    <div className="placeholder">
      <h1>{page.label}{tab ? ` · ${tab.label}` : ''}</h1>
      <p className="frame-id">{tab?.frame ?? page.frame}</p>
    </div>
  );
}
