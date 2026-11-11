/**
 * Localize semantic timestamps rendered by the server. Day grouping stays
 * UTC; day labels are shown in UTC, individual times in the browser timezone.
 * Without JS the server-rendered UTC text remains.
 */
export function localizeTimestamps(root = document) {
  const dateFormat = new Intl.DateTimeFormat(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
  });
  const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  for (const element of root.querySelectorAll('time[datetime][data-format]')) {
    const date = new Date(element.getAttribute('datetime'));
    if (Number.isNaN(date.getTime())) continue;
    element.textContent = element.dataset.format === 'date'
      ? dateFormat.format(date)
      : timeFormat.format(date);
  }
}
