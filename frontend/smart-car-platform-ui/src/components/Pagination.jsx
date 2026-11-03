// Prev / next pager with a "x-y of total" label. Pages are 1-based.
//
// Usage:
//   <Pagination page={page} pageSize={20} total={data.total} onChange={setPage} />
export default function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="pagination">
      <span>{from}-{to} of {total}</span>
      <button className="btn" disabled={page <= 1} onClick={() => onChange(page - 1)}>Prev</button>
      <span>Page {page} / {pages}</span>
      <button className="btn" disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</button>
    </div>
  );
}
