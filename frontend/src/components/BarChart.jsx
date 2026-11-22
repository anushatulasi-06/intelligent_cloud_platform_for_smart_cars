// Small bar chart made of plain elements, so the project needs no chart library.
//
// Usage:
//   <BarChart data={[{ label: 'emergency', value: 12 }, { label: 'human', value: 3 }]} />
//   <BarChart data={rows} color="var(--sky)" empty="No alerts in this window" />
export default function BarChart({ data, color = 'var(--primary)', empty = 'No data', height = 150 }) {
  if (!data || data.length === 0) return <p className="muted small">{empty}</p>;
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="bar-chart" style={{ height }}>
      {data.map((d) => (
        <div key={d.label} className="bar-col" title={`${d.label}: ${d.value}`}>
          <span className="bar-value">{d.value}</span>
          <span
            className="bar"
            style={{ height: `${Math.max(2, Math.round((d.value / max) * 100))}%`, background: d.color || color }}
          />
          <span className="bar-label">{d.label}</span>
        </div>
      ))}
    </div>
  );
}
