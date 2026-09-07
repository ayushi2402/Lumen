"use client";

/**
 * Charts: a sparkline for list rows and a price line with event markers for
 * stock detail.
 *
 * Deliberately small. A trading terminal was explicitly out of scope; the
 * chart exists to place events in time, not to support technical analysis.
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatTime } from "@/lib/format";
import type { Severity } from "@/lib/types";

export function Sparkline({
  values,
  className,
}: {
  values: number[];
  className?: string;
}) {
  if (!values || values.length < 2) {
    return <div className={className ?? "h-8 w-24"} />;
  }

  const rising = values[values.length - 1] >= values[0];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 100 - ((value - min) / range) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className={className ?? "h-8 w-24"}
      aria-hidden="true"
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={4}
        vectorEffect="non-scaling-stroke"
        className={rising ? "stroke-up" : "stroke-down"}
      />
    </svg>
  );
}

export interface ChartPoint {
  timestamp: string;
  price: number | null;
  score: number;
  severity: Severity;
}

const MARKER_COLOUR: Record<string, string> = {
  Critical: "#cf222e",
  "High Attention": "#9a6700",
  "Worth Watching": "#1f6feb",
  Noise: "#8c959f",
};

export function PriceChart({
  points,
  onSelectPoint,
  selectedIndex,
}: {
  points: ChartPoint[];
  onSelectPoint?: (index: number) => void;
  selectedIndex?: number | null;
}) {
  const data = points
    .filter((point) => point.price !== null)
    .map((point, index) => ({
      index,
      time: formatTime(point.timestamp),
      price: point.price as number,
      score: point.score,
      severity: point.severity,
    }));

  if (data.length < 2) {
    return (
      <div className="flex h-56 items-center justify-center rounded-lg border border-dashed border-ink-200 text-sm text-ink-400">
        Not enough observations yet to draw a chart.
      </div>
    );
  }

  const prices = data.map((d) => d.price);
  const padding = (Math.max(...prices) - Math.min(...prices)) * 0.08 || 1;

  // Markers sit where the event was significant enough to be worth noting.
  const markers = data.filter((d) => d.severity !== "Noise");

  return (
    <div className="h-56 w-full sm:h-72">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
          onClick={(state: unknown) => {
            // Recharts' chart state type varies across versions; read the one
            // field needed rather than depending on its exported shape.
            const index = (state as { activeTooltipIndex?: number } | null)
              ?.activeTooltipIndex;
            if (onSelectPoint && typeof index === "number") onSelectPoint(index);
          }}
        >
          <CartesianGrid stroke="#eaeef2" vertical={false} />
          <XAxis
            dataKey="time"
            tick={{ fontSize: 11, fill: "#8c959f" }}
            tickLine={false}
            axisLine={{ stroke: "#d0d7de" }}
            minTickGap={24}
          />
          <YAxis
            domain={[Math.min(...prices) - padding, Math.max(...prices) + padding]}
            tick={{ fontSize: 11, fill: "#8c959f" }}
            tickLine={false}
            axisLine={false}
            width={56}
            tickFormatter={(value: number) => `₹${value.toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{
              borderRadius: 8,
              border: "1px solid #d0d7de",
              fontSize: 12,
            }}
            // Recharts v3 types the tooltip value as possibly undefined, so it
            // is narrowed here rather than assumed to be a number.
            formatter={(value, name) => {
              const numeric = typeof value === "number" ? value : Number(value);
              return Number.isFinite(numeric)
                ? [`₹${numeric.toFixed(2)}`, "Price"]
                : ["--", String(name ?? "")];
            }}
          />
          <Line
            type="monotone"
            dataKey="price"
            stroke="#0d1117"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {markers.map((marker) => (
            <ReferenceDot
              key={marker.index}
              x={marker.time}
              y={marker.price}
              r={selectedIndex === marker.index ? 7 : 5}
              fill={MARKER_COLOUR[marker.severity] ?? "#8c959f"}
              stroke="#fff"
              strokeWidth={2}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
