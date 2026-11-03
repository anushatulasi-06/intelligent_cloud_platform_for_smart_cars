// Tab bar synced to the ?tab= query parameter. The layout renders it
// automatically for pages with 2+ tabs (see src/config/pages.js); pages read
// the active tab with useActiveTab().
//
// Usage:
//   <PageTabs tabs={[{ key: 'devices', label: 'Devices' }, { key: 'data', label: 'Device data' }]} />
//
//   // inside the page:
//   const tab = useActiveTab(tabs);   // 'devices' | 'data'; first tab when ?tab= is missing/invalid
import { useSearchParams } from 'react-router-dom';

export function useActiveTab(tabs) {
  const [params] = useSearchParams();
  const requested = params.get('tab');
  return tabs.some((t) => t.key === requested) ? requested : tabs[0]?.key;
}

export default function PageTabs({ tabs }) {
  const [params, setParams] = useSearchParams();
  const active = useActiveTab(tabs);
  return (
    <div className="page-tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={t.key === active}
          className={`tab ${t.key === active ? 'tab-active' : ''}`}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.set('tab', t.key);
            setParams(next);
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
