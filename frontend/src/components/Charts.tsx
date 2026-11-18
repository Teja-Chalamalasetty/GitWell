import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RuleFailure, TrendPoint } from "../api/types";
import { chartColors, useDarkMode } from "../hooks/useDarkMode";
import { formatDate } from "../lib/format";
import { GRADES, GRADE_COLORS } from "../lib/status";

function useColors() {
  return chartColors(useDarkMode());
}

function tooltipStyle(c: ReturnType<typeof chartColors>) {
  return {
    backgroundColor: c.tooltipBg,
    border: `1px solid ${c.tooltipBorder}`,
    borderRadius: 6,
    fontSize: 12,
  };
}

/** "Most common failures": horizontal bars per rule (PRD 15). */
export function FailuresChart({ data }: { data: RuleFailure[] }) {
  const c = useColors();
  const rows = data.map((r) => ({ name: r.rule_name, fail: r.fail, warn: r.warn }));
  return (
    <div role="img" aria-label="Failing and warning repositories per rule" style={{ height: 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid stroke={c.grid} horizontal={false} />
          <XAxis type="number" allowDecimals={false} stroke={c.text} fontSize={12} />
          <YAxis type="category" dataKey="name" width={150} stroke={c.text} fontSize={12} />
          <Tooltip contentStyle={tooltipStyle(c)} cursor={{ fill: c.grid, opacity: 0.4 }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="fail" name="Failing repos" stackId="a" fill={c.fail} />
          <Bar dataKey="warn" name="Warning repos" stackId="a" fill={c.warn} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 30-day average score (PRD 15). */
export function TrendChart({ data }: { data: TrendPoint[] }) {
  const c = useColors();
  const rows = data.map((p) => ({ date: p.date, label: formatDate(p.date), score: p.average_score }));
  return (
    <div role="img" aria-label="Average score over the last 30 days" style={{ height: 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ left: 0, right: 16, top: 8 }}>
          <CartesianGrid stroke={c.grid} />
          <XAxis dataKey="label" stroke={c.text} fontSize={12} minTickGap={24} />
          <YAxis domain={[0, 100]} stroke={c.text} fontSize={12} width={32} />
          <Tooltip contentStyle={tooltipStyle(c)} />
          <Line
            type="monotone"
            dataKey="score"
            name="Average score"
            stroke={c.line}
            strokeWidth={2}
            dot={rows.length < 12}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One repo's score over time, from its completed scans (oldest first). */
export function ScoreHistoryChart({
  data,
}: {
  data: { id: number; finished_at: string; score: number | null }[];
}) {
  const c = useColors();
  const rows = data.map((s) => ({
    id: s.id,
    when: new Date(s.finished_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    score: s.score,
  }));
  return (
    <div role="img" aria-label="Score history" style={{ height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ left: 0, right: 16, top: 8 }}>
          <CartesianGrid stroke={c.grid} />
          <XAxis dataKey="when" stroke={c.text} fontSize={12} minTickGap={24} />
          <YAxis domain={[0, 100]} stroke={c.text} fontSize={12} width={32} />
          <Tooltip contentStyle={tooltipStyle(c)} />
          <Line
            type="monotone"
            dataKey="score"
            name="Score"
            stroke={c.line}
            strokeWidth={2}
            dot={rows.length < 25}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Stacked bar of how many repos have each grade. */
export function GradeBar({ distribution }: { distribution: Record<string, number> }) {
  const total = GRADES.reduce((sum, g) => sum + (distribution[g] ?? 0), 0);
  if (total === 0) {
    return <p className="text-sm text-zinc-500">No graded repositories yet.</p>;
  }
  return (
    <div>
      <div
        className="flex h-4 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        role="img"
        aria-label={GRADES.map((g) => `${distribution[g] ?? 0} grade ${g}`).join(", ")}
      >
        {GRADES.map((g) => {
          const n = distribution[g] ?? 0;
          return n === 0 ? null : (
            <div
              key={g}
              style={{ width: `${(n / total) * 100}%`, backgroundColor: GRADE_COLORS[g] }}
              title={`Grade ${g}: ${n}`}
            />
          );
        })}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {GRADES.map((g) => (
          <li key={g} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: GRADE_COLORS[g] }}
              aria-hidden
            />
            {g}: <span className="font-medium tabular-nums">{distribution[g] ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
