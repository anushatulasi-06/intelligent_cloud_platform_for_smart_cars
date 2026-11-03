// Table with built-in loading, error and empty states.
//
// Usage:
//   <DataTable
//     columns={[
//       { key: 'name', header: 'Name' },
//       { key: 'status', header: 'Status', render: (row) => <StatusBadge value={row.status} /> },
//     ]}
//     rows={data?.items}
//     loading={loading}
//     error={error}                       // Error object or string
//     empty="No devices match the filters"
//     rowKey="id"                         // field name or (row) => key
//     onRowClick={(row) => navigate(`/devices/${row.id}`)}   // optional
//   />
export default function DataTable({
  columns, rows, loading = false, error = null, empty = 'No data', rowKey = 'id', onRowClick,
}) {
  const keyOf = (row, i) => (typeof rowKey === 'function' ? rowKey(row) : row[rowKey] ?? i);
  let body;
  if (loading) body = <StateRow cols={columns.length}>Loading...</StateRow>;
  else if (error) body = <StateRow cols={columns.length} error>Error: {error.message || String(error)}</StateRow>;
  else if (!rows || rows.length === 0) body = <StateRow cols={columns.length}>{empty}</StateRow>;
  else {
    body = rows.map((row, i) => (
      <tr
        key={keyOf(row, i)}
        className={onRowClick ? 'clickable' : undefined}
        onClick={onRowClick ? () => onRowClick(row) : undefined}
      >
        {columns.map((c) => (
          <td key={c.key} style={c.width ? { width: c.width } : undefined}>
            {c.render ? c.render(row) : row[c.key] ?? '-'}
          </td>
        ))}
      </tr>
    ));
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>{columns.map((c) => <th key={c.key}>{c.header}</th>)}</tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}

function StateRow({ cols, error, children }) {
  return (
    <tr className="state-row">
      <td colSpan={cols} className={error ? 'state-error' : undefined}>{children}</td>
    </tr>
  );
}
