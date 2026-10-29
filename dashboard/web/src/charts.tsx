import { useEffect, useRef, useState } from "react";

/** The rendered width of an element (the chart draws in real pixels, so text keeps its size on any screen). */
function useWidth(fallback = 720) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(280, Math.round(el.getBoundingClientRect().width)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** 1234 → "1.2k", 2_500_000 → "2.5M" (the same short form the task log uses). */
export const short = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n));

/** A bar with rounded top corners only (the baseline end stays square). */
function topRounded(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

/** A bar with rounded right corners only (horizontal bars grow from the left baseline). */
function rightRounded(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * p >= v / 3) ?? 10;
  return Math.ceil(v / (step * p)) * step * p;
}

export interface Series {
  key: string;
  label: string;
  color: string; // a CSS var, e.g. "var(--series-1)"
}

/** Stacked daily bars (one stack per day), a legend, and a per-day hover tooltip. */
export function StackedDays<T extends { day: string }>({ rows, series, value }: {
  rows: T[];
  series: Series[];
  value: (row: T, key: string) => number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const H = 220, left = 44, bottom = 22, top = 8;
  const plotW = W - left - 8, plotH = H - top - bottom;
  const totals = rows.map((r) => series.reduce((s, x) => s + value(r, x.key), 0));
  const max = niceMax(Math.max(0, ...totals));
  const slot = plotW / Math.max(1, rows.length);
  const barW = Math.max(3, slot - 4);
  const y = (v: number) => top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const tip = hover !== null ? rows[hover] : null;
  return (
    <div className="viz">
      <div className="legend">
        {series.map((s) => (
          <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
        ))}
      </div>
      <div className="viz-plot" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Tokeni pe zi" onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={W - 8} y1={y(t)} y2={y(t)} className="grid-line" />
              <text x={left - 6} y={y(t) + 4} textAnchor="end" className="axis">{short(t)}</text>
            </g>
          ))}
          {rows.map((r, i) => {
            const x = left + i * slot + (slot - barW) / 2;
            let base = 0;
            const drawn = series.filter((s) => value(r, s.key) > 0);
            return (
              <g key={r.day}>
                {drawn.map((s, k) => {
                  const v = value(r, s.key);
                  const y0 = y(base), y1 = y(base + v);
                  base += v;
                  const gap = k > 0 ? 2 : 0; // a 2px surface gap between stacked segments
                  const h = Math.max(1, y0 - y1 - gap);
                  return k === drawn.length - 1 ? (
                    <path key={s.key} d={topRounded(x, y1, barW, h, 4)} fill={s.color} />
                  ) : (
                    <rect key={s.key} x={x} y={y1 + gap} width={barW} height={h} fill={s.color} />
                  );
                })}
                {i % 7 === (rows.length - 1) % 7 && (
                  <text x={x + barW / 2} y={H - 6} textAnchor="middle" className="axis">{r.day.slice(5)}</text>
                )}
                <rect x={left + i * slot} y={top} width={slot} height={plotH} fill="transparent"
                  onMouseEnter={() => setHover(i)} />
              </g>
            );
          })}
          {hover !== null && (
            <line x1={left + hover * slot + slot / 2} x2={left + hover * slot + slot / 2} y1={top} y2={top + plotH}
              className="crosshair" />
          )}
        </svg>
        {tip && hover !== null && (
          <div className="tooltip" style={{ left: `${((left + hover * slot + slot / 2) / W) * 100}%` }}>
            <b>{tip.day}</b>
            {series.map((s) => (
              <span key={s.key}><i style={{ background: s.color }} />{s.label}: {short(value(tip, s.key))}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Horizontal bars, best first, with the value as a direct label and a hover tooltip. */
export function TopBars({ rows, color, note }: {
  rows: { label: string; value: number; title?: string }[];
  color: string;
  note?: (row: { label: string; value: number; title?: string }) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const rowH = 26, labelW = Math.min(230, Math.round(W * 0.42)), valueW = 40;
  const H = rows.length * rowH + 4;
  const max = Math.max(1, ...rows.map((r) => r.value));
  const plotW = W - labelW - valueW;
  return (
    <div className="viz">
      <div className="viz-plot" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Top skill-uri cerute" onMouseLeave={() => setHover(null)}>
          {rows.map((r, i) => {
            const w = Math.max(2, (r.value / max) * plotW);
            const yy = i * rowH + 4;
            return (
              <g key={r.label} onMouseEnter={() => setHover(i)}>
                <rect x={0} y={yy - 2} width={W} height={rowH} fill="transparent" />
                <text x={labelW - 8} y={yy + 14} textAnchor="end" className={hover === i ? "bar-label on" : "bar-label"}>
                  {r.label.length > labelW / 7 ? r.label.slice(0, Math.floor(labelW / 7) - 1) + "…" : r.label}
                </text>
                <path d={rightRounded(labelW, yy + 2, w, rowH - 10, 4)} fill={color} opacity={hover === null || hover === i ? 1 : 0.55} />
                <text x={labelW + w + 6} y={yy + 14} className="bar-value">{r.value}</text>
              </g>
            );
          })}
        </svg>
        {hover !== null && rows[hover] && (
          <div className="tooltip" style={{ left: "50%", top: `${((hover * rowH + 4) / H) * 100}%` }}>
            <b>{rows[hover].label}</b>
            <span>{note ? note(rows[hover]) : `${rows[hover].value}`}</span>
          </div>
        )}
      </div>
    </div>
  );
}
