import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, MapChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent, DataZoomComponent, GeoComponent, GridComponent, LegendComponent, MarkAreaComponent,
  MarkLineComponent, TooltipComponent, VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart, LineChart, HeatmapChart, MapChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent,
  MarkAreaComponent, MarkLineComponent, VisualMapComponent, GeoComponent, DataZoomComponent, AxisPointerComponent,
  CanvasRenderer,
]);

export { echarts };

/** 툴팁(HTML)용 제곱미터 표기 — 캔버스(축 이름·범례)는 'm²' 문자를 그대로 쓴다. */
export const M2 = 'm<sup style="font-size:.72em;line-height:0">2</sup>';

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** 역할 토큰 → 차트 색 (테마가 바뀌면 다시 읽는다). */
export function palette() {
  return {
    text: cssVar("--text"), text2: cssVar("--text-2"), text3: cssVar("--text-3"), surface: cssVar("--chart-surface"),
    grid: cssVar("--grid"), axis: cssVar("--axis"), border: cssVar("--border"),
    s1: cssVar("--series-1"), s2: cssVar("--series-2"), s3: cssVar("--series-3"), band: cssVar("--series-1-band"),
    up: cssVar("--up"), down: cssVar("--down"), na: cssVar("--na"),
    seq: [0, 1, 2, 3, 4, 5, 6, 7].map((i) => cssVar(`--seq-${i}`)),
    div: ["--div-neg-2", "--div-neg-1", "--div-mid", "--div-pos-1", "--div-pos-2"].map(cssVar),
    status: { good: cssVar("--status-good"), warn: cssVar("--status-warn"), bad: cssVar("--status-bad"),
              info: cssVar("--status-info"), none: cssVar("--status-none") },
    font: cssVar("--font"),
  };
}

export type Palette = ReturnType<typeof palette>;

export function base(p: Palette) {
  return {
    backgroundColor: "transparent",
    animationDuration: 300,
    textStyle: { fontFamily: p.font, color: p.text2, fontSize: 12 },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis", confine: true,
      backgroundColor: p.surface, borderColor: p.border, borderWidth: 1, padding: [8, 10],
      textStyle: { color: p.text, fontSize: 12 }, extraCssText: "box-shadow: 0 6px 20px rgba(0,0,0,.12);",
      axisPointer: { type: "line", lineStyle: { color: p.axis } },
    },
    // 범례는 오른쪽 위 — 왼쪽 위는 y축 이름(단위) 자리라 겹친다
    legend: { top: 0, right: 0, itemWidth: 12, itemHeight: 8, icon: "roundRect", textStyle: { color: p.text2, fontSize: 12 } },
  };
}

export function axisX(p: Palette, extra: Record<string, unknown> = {}) {
  return {
    axisLine: { lineStyle: { color: p.axis } }, axisTick: { show: false },
    axisLabel: { color: p.text3, fontSize: 11 }, splitLine: { show: false }, ...extra,
  };
}

export function axisY(p: Palette, extra: Record<string, unknown> = {}) {
  return {
    axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: p.text3, fontSize: 11 },
    splitLine: { lineStyle: { color: p.grid } }, nameTextStyle: { color: p.text3, fontSize: 11 }, ...extra,
  };
}

type Props = {
  build: (p: Palette) => echarts.EChartsCoreOption;
  deps: unknown[];
  height?: number | string;
  label: string;
  onClick?: (params: { name?: string; data?: unknown; dataIndex?: number; seriesIndex?: number }) => void;
  onReady?: (inst: echarts.ECharts) => void;
};

/** ECharts 래퍼: 크기 변화·테마 변화(data-theme)에 맞춰 다시 그린다. */
export function Chart({ build, deps, height = 320, label, onClick, onReady }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  const buildRef = useRef(build);
  buildRef.current = build;
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "canvas" });
    inst.current = chart;
    chart.on("click", (e) => clickRef.current?.(e as never));
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    const mo = new MutationObserver(() => chart.setOption(buildRef.current(palette()), true));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    onReady?.(chart);
    return () => {
      ro.disconnect();
      mo.disconnect();
      chart.dispose();
      inst.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    inst.current?.setOption(build(palette()), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return <div ref={el} role="img" aria-label={label} style={{ width: "100%", height }} />;
}
