/**
 * Reusable SVG line charts for dashboard panels.
 *
 * Trainer-agnostic: callers describe the metrics to plot and provide rows
 * grouped by UTC day ({ date: 'YYYY-MM-DD', ... }). Handles missing values,
 * empty ranges, and single-point charts.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';
const DAY_MS = 86400000;

function svgElement(name, attrs) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  return element;
}

function rangeStart(rows, rangeDays, endTime) {
  if (rangeDays === 'all') {
    return rows.length ? Date.parse(`${rows[0].date}T00:00:00Z`) : endTime;
  }
  return endTime - (Number(rangeDays) - 1) * DAY_MS;
}

function renderChartCard(chart, points, start, endTime) {
  const card = document.createElement('article');
  card.className = 'chart-card';
  const heading = document.createElement('h4');
  heading.textContent = chart.label;
  card.append(heading);

  if (!points.length) {
    const empty = document.createElement('p');
    empty.className = 'chart-empty';
    empty.textContent = 'No data in this range';
    card.append(empty);
    return card;
  }

  const pad = chart.pad ?? 50;
  const format = chart.format || (value => String(value));
  const values = points.map(row => row[chart.key]);
  const low = Math.max(0, Math.min(...values) - pad);
  const high = Math.max(...values) + pad;

  const svg = svgElement('svg', {
    viewBox: '0 0 360 180',
    role: 'img',
    'aria-label': `${chart.label}, daily progression`,
  });
  for (const [value, y] of [[high, 22], [(high + low) / 2, 82], [low, 142]]) {
    svg.append(svgElement('line', { x1: 54, x2: 348, y1: y, y2: y, class: 'chart-gridline' }));
    const text = svgElement('text', { x: 46, y: y + 4, 'text-anchor': 'end' });
    text.textContent = format(Math.round(value));
    svg.append(text);
  }
  const coords = points.map(row => [
    54 + (Date.parse(`${row.date}T00:00:00Z`) - start) / Math.max(DAY_MS, endTime - start) * 294,
    142 - (row[chart.key] - low) / (high - low) * 120,
  ]);
  svg.append(svgElement('polyline', {
    points: coords.map(point => point.join(',')).join(' '),
    class: 'chart-line',
  }));
  points.forEach((row, index) => {
    const dot = svgElement('circle', {
      cx: coords[index][0], cy: coords[index][1], r: 3, class: 'chart-point', tabindex: '0',
    });
    const title = svgElement('title', {});
    title.textContent = `${row.date}: ${format(row[chart.key])}`;
    dot.append(title);
    dot.setAttribute('aria-label', title.textContent);
    svg.append(dot);
  });
  for (const [timestamp, x, anchor] of [[start, 54, 'start'], [endTime, 348, 'end']]) {
    const text = svgElement('text', { x, y: 170, 'text-anchor': anchor });
    text.textContent = new Date(timestamp).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: '2-digit', timeZone: 'UTC',
    });
    svg.append(text);
  }
  card.append(svg);
  return card;
}

/**
 * Render line charts into `container`.
 * charts: [{ key, label, format?, pad? }]
 * rows: [{ date: 'YYYY-MM-DD', ... }] sorted ascending by date
 * rangeDays: '7' | '30' | '90' | 'all'
 */
export function renderLineCharts(container, charts, rows, rangeDays) {
  const now = new Date();
  const endTime = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const start = rangeStart(rows, rangeDays, endTime);
  const visible = rows.filter(row => Date.parse(`${row.date}T00:00:00Z`) >= start);
  container.replaceChildren();
  for (const chart of charts) {
    const points = visible.filter(row => row[chart.key] !== null && Number.isFinite(row[chart.key]));
    container.append(renderChartCard(chart, points, start, endTime));
  }
}
