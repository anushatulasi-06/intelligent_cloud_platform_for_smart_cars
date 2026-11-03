// Pager: "x-y of total", Prev, numbered pages, Next. Pages are 1-based.
//
// Usage:
//   <Pagination page={page} pageSize={20} total={data.total} onChange={setPage} />
export default function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  // Show at most 7 page numbers around the current page.
  const start = Math.max(1, Math.min(page - 3, pages - 6));
  const numbers = Array.from({ length: Math.min(7, pages) }, (_, i) => start + i);
  return (
    <nav className="pagination" aria-label="Pagination">
      <span>{from}-{to} of {total}</span>
      <button className="btn btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)} aria-label="Previous page">‹</button>
      {numbers.map((n) => (
        <button
          key={n}
          className={`btn btn-sm ${n === page ? 'btn-primary' : ''}`}
          aria-current={n === page ? 'page' : undefined}
          onClick={() => onChange(n)}
        >{n}</button>
      ))}
      <button className="btn btn-sm" disabled={page >= pages} onClick={() => onChange(page + 1)} aria-label="Next page">›</button>
    </nav>
  );
}
