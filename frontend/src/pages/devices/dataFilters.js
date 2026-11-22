// Only supported record filters are read from shared links. Other query parameters
// (including the active tab) survive Search and Reset.
export const DATA_FILTER_KEYS = ['status', 'car_id', 'device_id', 'from', 'to'];
const STATUSES = ['pending', 'processing', 'processed', 'failed'];

function positiveId(value) {
  return /^\d+$/.test(value || '') && Number.isSafeInteger(Number(value)) && Number(value) > 0
    ? String(Number(value)) : '';
}

function calendarDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return '';
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : '';
}

export function parseDataFilters(params) {
  const filters = {
    status: STATUSES.includes(params.get('status')) ? params.get('status') : '',
    car_id: positiveId(params.get('car_id')),
    device_id: positiveId(params.get('device_id')),
    from: calendarDate(params.get('from')),
    to: calendarDate(params.get('to')),
  };
  if (filters.from && filters.to && filters.from > filters.to) {
    filters.from = ''; filters.to = '';
  }
  return filters;
}

export function dataFilterParams(current, values) {
  const next = new URLSearchParams(current);
  const valid = parseDataFilters(new URLSearchParams(values));
  for (const key of DATA_FILTER_KEYS) {
    if (valid[key]) next.set(key, valid[key]); else next.delete(key);
  }
  return next;
}
