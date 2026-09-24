"use client";

/**
 * The Usage tab: tiles, a daily chart and a sortable breakdown table over
 * `usage_summary`/`usage_refresh`. Everything here is read-only - a range and
 * two client-side view toggles are the only state that is not the backend's.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { backend } from "@/lib/bridge";
import type { UsageRow, UsageSeriesPoint, UsageSummary, UsageTile, UsageTileSlot } from "@/lib/types";
import type { ToastKind } from "./Toast";
import { ChevronIcon, RefreshIcon } from "./icons";
import { Button, cx } from "./ui";

type Range = "7d" | "30d" | "all";
type Metric = "cost" | "tokens";
type Grouping = "by_model" | "by_project";
type RowSortKey = "app" | "name" | "input" | "output" | "cache_read" | "cache_write" | "tokens" | "cost";
type SortDir = "asc" | "desc";

const RANGE_OPTIONS: { id: Range; label: string }[] = [
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "all", label: "All" },
];

const METRIC_OPTIONS: { id: Metric; label: string }[] = [
  { id: "cost", label: "Cost" },
  { id: "tokens", label: "Tokens" },
];

const GROUPING_OPTIONS: { id: Grouping; label: string }[] = [
  { id: "by_model", label: "By model" },
  { id: "by_project", label: "By project" },
];

const APP_LABEL: Record<string, string> = { claude: "Claude", codex: "Codex" };

/**
 * Claude's clay is the same literal hex the rest of the app already uses for
 * it (Header's tab tint, the provider Avatar) - there is no token for it.
 * Codex takes the brand-blue token rather than OpenAI's near-black: the
 * dataviz palette validator fails black as a second categorical mark (too
 * dark, effectively no chroma - it reads as gray, not an identity). Validated
 * as a pair: `node scripts/validate_palette.js "#D97757,#007AFF" --mode
 * light` passes every check (CVD ΔE 26.6/27.9, normal-vision ΔE 33.7,
 * contrast >= 3:1 against a light surface).
 */
const CLAUDE_COLOR = "#D97757";
const CODEX_COLOR = "var(--color-brand-600)";
const APP_COLOR: Record<string, string> = { claude: CLAUDE_COLOR, codex: CODEX_COLOR };
/** The series carries no per-app token split (only cost is split by app - see UsageChart), so the combined-tokens bar gets its own neutral tone rather than reusing either app's color. */
const TOKENS_COLOR = "var(--color-accent-500)";

const EMPTY_SLOT: UsageTileSlot = { tokens: 0, cost: null };
const EMPTY_TILE: UsageTile = { this_month: EMPTY_SLOT, all_time: EMPTY_SLOT };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** A key `tiles` did not report - the mock and a fresh install can both omit one - renders as a zeroed tile rather than a crash. */
function tileFor(summary: UsageSummary, appId: string): UsageTile {
  return summary.tiles[appId] ?? EMPTY_TILE;
}

/** Never 0 for a null cost - an unpriced model is unknown spend, not free. */
function formatCost(cost: number | null): string {
  return cost === null ? "—" : `$${cost.toFixed(2)}`;
}

function formatTokens(tokens: number): string {
  return Math.round(tokens).toLocaleString();
}

function formatAsOf(iso: string): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function compactNumber(value: number): string {
  if (Math.abs(value) < 1000) return formatTokens(value);
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/** Axis ticks stay readable down to a few cents, not just whole dollars. */
function formatTickCost(value: number): string {
  if (value <= 0) return "$0";
  if (value >= 1000) return `$${compactNumber(value)}`;
  if (value >= 10) return `$${Math.round(value).toLocaleString()}`;
  return `$${value.toFixed(2)}`;
}

function rowName(row: UsageRow, grouping: Grouping): string {
  const raw = grouping === "by_model" ? row.model : row.project;
  return raw && raw.trim() ? raw : "(unspecified)";
}

function compareRows(a: UsageRow, b: UsageRow, key: RowSortKey, grouping: Grouping): number {
  switch (key) {
    case "app":
      return a.app.localeCompare(b.app);
    case "name":
      return rowName(a, grouping).localeCompare(rowName(b, grouping));
    case "cost":
      if (a.cost === null && b.cost === null) return 0;
      if (a.cost === null) return -1;
      if (b.cost === null) return 1;
      return a.cost - b.cost;
    case "input":
      return a.input - b.input;
    case "output":
      return a.output - b.output;
    case "cache_read":
      return a.cache_read - b.cache_read;
    case "cache_write":
      return a.cache_write - b.cache_write;
    case "tokens":
      return a.tokens - b.tokens;
    default:
      return 0;
  }
}

/** One segmented control, shared by the range, metric and grouping toggles - the same visual as the tab bar's `.upool-segment`. */
function SegmentToggle<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="upool-segment relative inline-flex shrink-0 items-center p-0.5"
    >
      {options.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.id)}
            className={cx(
              "pressable relative z-[1] flex h-7 items-center rounded-[8px] px-3 text-[12px] font-semibold tracking-[-0.01em]",
              active
                ? "text-[var(--color-label)]"
                : "text-[var(--color-secondary-label)] hover:text-[var(--color-label)]",
            )}
          >
            {active ? <span aria-hidden className="upool-segment-thumb" /> : null}
            <span className="relative z-[1]">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function TileSlot({ label, slot }: { label: string; slot: UsageTileSlot }) {
  return (
    <div className="rounded-[14px] bg-[var(--color-fill)] px-3 py-2.5">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.05em] text-[var(--color-tertiary-label)]">
        {label}
      </p>
      <p className="mt-1 text-[19px] font-semibold tabular-nums text-[var(--color-label)]">
        {formatCost(slot.cost)}
      </p>
      <p className="mt-0.5 text-[11.5px] tabular-nums text-[var(--color-secondary-label)]">
        {formatTokens(slot.tokens)} tokens
      </p>
    </div>
  );
}

function AppTile({ label, color, tile }: { label: string; color: string; tile: UsageTile }) {
  return (
    <div className="liquid-glass rounded-[22px] p-4">
      <div className="mb-3 flex items-center gap-2">
        <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
        <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-[var(--color-label)]">{label}</h3>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <TileSlot label="This month" slot={tile.this_month} />
        <TileSlot label="All time" slot={tile.all_time} />
      </div>
    </div>
  );
}

const CHART_HEIGHT = 150;
const CHART_TOP = 8;
const MARGIN_LEFT = 46;
const MARGIN_RIGHT = 6;
const AXIS_HEIGHT = 22;
const BAR_SLOT = 16;
const BAR_GAP = 4;
const BAR_WIDTH = BAR_SLOT - BAR_GAP;
const SEGMENT_GAP = 2;

interface DayBar {
  date: string;
  claude: number;
  codex: number;
  total: number;
}

/** Rounds a value up to a "nice" ceiling (1/2/5 x 10^n) so axis ticks land on clean numbers. */
function niceMax(value: number): number {
  if (!(value > 0)) return 1;
  const exp = Math.floor(Math.log10(value));
  const base = 10 ** exp;
  const fraction = value / base;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * base;
}

/** A bar/segment path: rounded top corners at the stack's outer end, square everywhere else (including the baseline). */
function roundedTopPath(x: number, y: number, width: number, height: number, radius: number): string {
  if (height <= 0) return "";
  const r = Math.max(0, Math.min(radius, width / 2, height));
  if (r === 0) return `M${x},${y} h${width} v${height} h${-width} Z`;
  return [
    `M${x},${y + r}`,
    `a${r},${r} 0 0 1 ${r},${-r}`,
    `h${width - 2 * r}`,
    `a${r},${r} 0 0 1 ${r},${r}`,
    `v${height - r}`,
    `h${-width}`,
    "Z",
  ].join(" ");
}

function formatDayLabel(dateStr: string, short: boolean): string {
  const at = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(at.getTime())) return dateStr;
  return short ? String(at.getDate()) : at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * A stacked/single-series bar chart, inline SVG, no library. `series.claude`
 * and `series.codex` are each day's per-app *cost* (the backend never splits
 * tokens by app) - so cost mode stacks two colors and tokens mode falls back
 * to one combined bar rather than inventing a per-app token split the data
 * does not have.
 */
function UsageChart({ series, metric }: { series: UsageSeriesPoint[]; metric: Metric }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const days: DayBar[] = useMemo(
    () =>
      series.map((point) =>
        metric === "cost"
          ? { date: point.date, claude: point.claude, codex: point.codex, total: point.claude + point.codex }
          : { date: point.date, claude: 0, codex: 0, total: point.tokens },
      ),
    [series, metric],
  );

  const hasData = days.some((day) => day.total > 0);

  if (days.length === 0 || !hasData) {
    return (
      <div className="rounded-[16px] bg-[var(--color-fill)] px-6 py-10 text-center">
        <p className="text-[13px] font-medium text-[var(--color-label)]">No usage recorded yet</p>
        <p className="mt-1 text-[12px] text-[var(--color-secondary-label)]">
          Numbers appear here once U-Pool has read a Claude Code or Codex transcript for this range.
        </p>
      </div>
    );
  }

  const maxValue = niceMax(days.reduce((max, day) => Math.max(max, day.total), 0));
  const width = MARGIN_LEFT + days.length * BAR_SLOT + MARGIN_RIGHT;
  const height = CHART_TOP + CHART_HEIGHT + AXIS_HEIGHT;
  const baseline = CHART_TOP + CHART_HEIGHT;
  const heightFor = (value: number) => (value / maxValue) * CHART_HEIGHT;
  const ticks = [0, maxValue / 2, maxValue];
  const formatTick = metric === "cost" ? formatTickCost : compactNumber;

  // Roughly one date label per 60px, but never fewer than the first and last day.
  const step = Math.max(1, Math.round(60 / BAR_SLOT), Math.ceil(days.length / 10));
  const shortLabels = step * BAR_SLOT < 46;

  const hoverDay = hoverIndex !== null ? days[hoverIndex] : null;

  return (
    <div>
      {metric === "cost" ? (
        <div className="mb-2 flex items-center gap-4">
          <span className="inline-flex items-center gap-1.5 text-[11.5px] font-medium text-[var(--color-secondary-label)]">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: CLAUDE_COLOR }} />
            Claude
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11.5px] font-medium text-[var(--color-secondary-label)]">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: CODEX_COLOR }} />
            Codex
          </span>
        </div>
      ) : null}

      <div className="w-full overflow-x-auto">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block">
          <title>{metric === "cost" ? "Daily cost by app" : "Daily combined tokens"}</title>

          {ticks.map((tick, i) => {
            const y = baseline - heightFor(tick);
            return (
              <g key={i}>
                <line
                  x1={MARGIN_LEFT}
                  x2={width - MARGIN_RIGHT}
                  y1={y}
                  y2={y}
                  stroke="var(--color-separator)"
                  strokeWidth={1}
                />
                <text
                  x={MARGIN_LEFT - 6}
                  y={y}
                  textAnchor="end"
                  dominantBaseline={i === 0 ? "auto" : "middle"}
                  className="fill-[var(--color-tertiary-label)]"
                  fontSize={10}
                >
                  {formatTick(tick)}
                </text>
              </g>
            );
          })}

          {days.map((day, i) => {
            const barX = MARGIN_LEFT + i * BAR_SLOT + BAR_GAP / 2;
            const hitX = MARGIN_LEFT + i * BAR_SLOT;
            const hovered = hoverIndex === i;
            const segments =
              metric === "cost"
                ? [
                    { value: day.claude, color: CLAUDE_COLOR },
                    { value: day.codex, color: CODEX_COLOR },
                  ]
                : [{ value: day.total, color: TOKENS_COLOR }];

            let cursor = baseline;
            const rects = segments.map((segment, si) => {
              const segHeight = heightFor(segment.value);
              if (segHeight <= 0) return null;
              const isTop = segments.slice(si + 1).every((rest) => rest.value <= 0);
              const y = cursor - segHeight;
              cursor = y - (isTop ? 0 : SEGMENT_GAP);
              return (
                <path
                  key={si}
                  d={roundedTopPath(barX, y, BAR_WIDTH, segHeight, isTop ? 4 : 0)}
                  fill={segment.color}
                  opacity={hoverIndex === null || hovered ? 1 : 0.45}
                />
              );
            });

            const showLabel = i % step === 0 || i === days.length - 1;
            const dayLabel =
              metric === "cost"
                ? `${formatDayLabel(day.date, false)}: Claude ${formatCost(day.claude)}, Codex ${formatCost(day.codex)}`
                : `${formatDayLabel(day.date, false)}: ${formatTokens(day.total)} tokens`;

            return (
              <g
                key={day.date}
                role="img"
                aria-label={dayLabel}
                tabIndex={0}
                onMouseEnter={() => setHoverIndex(i)}
                onMouseLeave={() => setHoverIndex((current) => (current === i ? null : current))}
                onFocus={() => setHoverIndex(i)}
                onBlur={() => setHoverIndex((current) => (current === i ? null : current))}
                className="cursor-pointer"
              >
                <rect x={hitX} y={CHART_TOP} width={BAR_SLOT} height={CHART_HEIGHT} fill="transparent" />
                {rects}
                {showLabel ? (
                  <text
                    x={barX + BAR_WIDTH / 2}
                    y={baseline + 14}
                    textAnchor="middle"
                    className="fill-[var(--color-tertiary-label)]"
                    fontSize={10}
                  >
                    {formatDayLabel(day.date, shortLabels)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>

      <p className="mt-2 min-h-[16px] text-[12px] tabular-nums text-[var(--color-secondary-label)]">
        {hoverDay ? (
          metric === "cost" ? (
            <>
              {formatDayLabel(hoverDay.date, false)} — Claude {formatCost(hoverDay.claude)}, Codex{" "}
              {formatCost(hoverDay.codex)}, total {formatCost(hoverDay.total)}
            </>
          ) : (
            <>
              {formatDayLabel(hoverDay.date, false)} — {formatTokens(hoverDay.total)} tokens
            </>
          )
        ) : (
          "Hover or focus a bar for that day's numbers."
        )}
      </p>
    </div>
  );
}

export function UsagePanel({
  onToast,
}: {
  onToast: (kind: ToastKind, title: string, detail?: string) => void;
}) {
  const [range, setRange] = useState<Range>("30d");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [metric, setMetric] = useState<Metric>("cost");
  const [grouping, setGrouping] = useState<Grouping>("by_model");
  const [sort, setSort] = useState<{ key: RowSortKey; dir: SortDir }>({ key: "tokens", dir: "desc" });

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    backend
      .usageSummary(range)
      .then((next) => !cancelled && setSummary(next))
      .catch((error) => !cancelled && setLoadError(message(error)));
    return () => {
      cancelled = true;
    };
  }, [range]);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    backend
      .usageRefresh(range)
      .then(setSummary)
      .catch((error) => onToast("error", "Could not refresh usage", message(error)))
      .finally(() => setRefreshing(false));
  }, [range, onToast]);

  const handleSort = useCallback((key: RowSortKey) => {
    setSort((current) => {
      if (current.key === key) return { key, dir: current.dir === "asc" ? "desc" : "asc" };
      return { key, dir: key === "app" || key === "name" ? "asc" : "desc" };
    });
  }, []);

  const nameLabel = grouping === "by_model" ? "Model" : "Project";
  const columns: { key: RowSortKey; label: string; align: "left" | "right" }[] = [
    { key: "app", label: "App", align: "left" },
    { key: "name", label: nameLabel, align: "left" },
    { key: "input", label: "Input", align: "right" },
    { key: "output", label: "Output", align: "right" },
    { key: "cache_read", label: "Cache read", align: "right" },
    { key: "cache_write", label: "Cache write", align: "right" },
    { key: "tokens", label: "Total", align: "right" },
    { key: "cost", label: "Cost", align: "right" },
  ];

  const rows = summary ? (grouping === "by_model" ? summary.by_model : summary.by_project) : [];
  const sortedRows = useMemo(() => {
    const next = [...rows];
    next.sort((a, b) => {
      const cmp = compareRows(a, b, sort.key, grouping);
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return next;
  }, [rows, sort, grouping]);

  return (
    <div className="animate-fade-in mx-auto w-full max-w-[720px] px-5 py-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4 px-1">
        <div className="min-w-0">
          <h1 className="display-title text-[var(--color-label)]">Usage</h1>
          <p className="mt-2 text-[13px] leading-snug tracking-[-0.01em] text-[var(--color-secondary-label)]">
            Tokens and cost, read from the Claude Code and Codex transcripts on this machine.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <SegmentToggle label="Range" options={RANGE_OPTIONS} value={range} onChange={setRange} />
            <Button onClick={handleRefresh} disabled={refreshing || !summary}>
              <RefreshIcon className={cx("h-4 w-4", refreshing && "animate-spin")} />
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
          {summary?.as_of ? (
            <span className="text-[11px] text-[var(--color-tertiary-label)]">
              as of {formatAsOf(summary.as_of)}
            </span>
          ) : null}
        </div>
      </div>

      {loadError ? (
        <p className="liquid-glass rounded-[22px] px-6 py-10 text-center text-[14px] text-red-600">
          {loadError}
        </p>
      ) : !summary ? (
        <div className="flex flex-col gap-3">
          <div className="liquid-glass h-24 animate-pulse rounded-[22px]" />
          <div className="liquid-glass h-48 animate-pulse rounded-[22px]" />
          <div className="liquid-glass h-40 animate-pulse rounded-[22px]" />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <AppTile label="Claude" color={CLAUDE_COLOR} tile={tileFor(summary, "claude")} />
            <AppTile label="Codex" color={CODEX_COLOR} tile={tileFor(summary, "codex")} />
          </div>

          <div className="liquid-glass rounded-[22px] p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-[13px] font-semibold text-[var(--color-label)]">Daily usage</h3>
                <p className="text-[11px] text-[var(--color-tertiary-label)]">
                  {RANGE_OPTIONS.find((option) => option.id === range)?.label}
                </p>
              </div>
              <SegmentToggle label="Chart metric" options={METRIC_OPTIONS} value={metric} onChange={setMetric} />
            </div>
            <UsageChart series={summary.series} metric={metric} />
          </div>

          <div className="liquid-glass rounded-[22px] p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-[13px] font-semibold text-[var(--color-label)]">Breakdown</h3>
              <SegmentToggle
                label="Breakdown grouping"
                options={GROUPING_OPTIONS}
                value={grouping}
                onChange={setGrouping}
              />
            </div>
            {sortedRows.length === 0 ? (
              <p className="py-10 text-center text-[13px] text-[var(--color-secondary-label)]">
                No {grouping === "by_model" ? "model" : "project"} activity in this range.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[12.5px]">
                  <thead>
                    <tr>
                      {columns.map((col) => (
                        <th
                          key={col.key}
                          scope="col"
                          className={cx(
                            "border-b border-[var(--color-separator)] pb-2 pr-3 text-[11px] font-semibold uppercase tracking-[0.04em] text-[var(--color-tertiary-label)]",
                            col.align === "right" ? "text-right" : "text-left",
                          )}
                        >
                          <button
                            type="button"
                            onClick={() => handleSort(col.key)}
                            aria-label={`Sort by ${col.label}`}
                            className={cx(
                              "inline-flex items-center gap-1 hover:text-[var(--color-label)]",
                              col.align === "right" && "flex-row-reverse",
                            )}
                          >
                            {col.label}
                            {sort.key === col.key ? (
                              <ChevronIcon
                                className={cx("h-3 w-3 shrink-0", sort.dir === "asc" ? "-rotate-90" : "rotate-90")}
                              />
                            ) : null}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map((row, i) => (
                      <tr
                        key={`${row.app}-${rowName(row, grouping)}-${i}`}
                        className="border-b border-[var(--color-separator)] last:border-0"
                      >
                        <td className="py-2 pr-3">
                          <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                            <span
                              aria-hidden
                              className="h-1.5 w-1.5 shrink-0 rounded-full"
                              style={{ background: APP_COLOR[row.app] ?? "var(--color-tertiary-label)" }}
                            />
                            {APP_LABEL[row.app] ?? row.app}
                          </span>
                        </td>
                        <td className="max-w-[220px] truncate py-2 pr-3" title={rowName(row, grouping)}>
                          {rowName(row, grouping)}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(row.input)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(row.output)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(row.cache_read)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatTokens(row.cache_write)}</td>
                        <td className="py-2 pr-3 text-right font-medium tabular-nums">
                          {formatTokens(row.tokens)}
                        </td>
                        <td className="py-2 text-right tabular-nums">{formatCost(row.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
