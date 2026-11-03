// Row of filter inputs with Apply / Reset. The parent keeps the values;
// edits stay local until Apply is pressed.
//
// Usage:
//   <FilterBar
//     fields={[
//       { name: 'status', label: 'Status', type: 'select', options: ['new', 'acknowledged'] },
//       { name: 'q', label: 'Search', type: 'text', placeholder: 'Device name' },
//       { name: 'from', label: 'From', type: 'date' },
//     ]}
//     values={filters}
//     onApply={(values) => setFilters(values)}
//   />
// `options` may be strings or { value, label } objects. Selects get an "All" option.
import { useEffect, useState } from 'react';

export default function FilterBar({ fields, values, onApply }) {
  const [draft, setDraft] = useState(values);
  useEffect(() => setDraft(values), [values]);

  const set = (name, v) => setDraft((d) => ({ ...d, [name]: v }));
  const empty = Object.fromEntries(fields.map((f) => [f.name, '']));

  return (
    <form className="filter-bar" onSubmit={(e) => { e.preventDefault(); onApply(draft); }}>
      {fields.map((f) => (
        <label key={f.name} className="field">
          <span>{f.label}</span>
          {f.type === 'select' ? (
            <select value={draft[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)}>
              <option value="">All</option>
              {f.options.map((o) => {
                const opt = typeof o === 'object' ? o : { value: o, label: String(o).replace(/_/g, ' ') };
                return <option key={opt.value} value={opt.value}>{opt.label}</option>;
              })}
            </select>
          ) : (
            <input
              type={f.type || 'text'}
              value={draft[f.name] ?? ''}
              placeholder={f.placeholder}
              onChange={(e) => set(f.name, e.target.value)}
            />
          )}
        </label>
      ))}
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">Apply</button>
        <button type="button" className="btn" onClick={() => { setDraft(empty); onApply(empty); }}>Reset</button>
      </div>
    </form>
  );
}
