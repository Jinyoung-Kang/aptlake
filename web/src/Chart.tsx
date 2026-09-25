import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkAreaComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, HeatmapChart, GridComponent, LegendComponent, TooltipComponent, MarkAreaComponent,
  VisualMapComponent, SVGRenderer]);

// ECharts 얇은 래퍼. 색은 CSS 변수(역할 토큰)에서 읽어 밝은/어두운 모드를 따라간다.
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function baseOption(): echarts.EChartsCoreOption {
  const muted = cssVar("--text-muted");
  const grid = cssVar("--grid");
  return {
    textStyle: { fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: cssVar("--text-secondary") },
    grid: { left: 56, right: 24, top: 36, bottom: 40 },
    tooltip: {
      trigger: "axis",
      backgroundColor: cssVar("--surface-1"),
      borderColor: cssVar("--border"),
      textStyle: { color: cssVar("--text-primary") },
    },
    xAxis: { axisLine: { lineStyle: { color: cssVar("--baseline") } }, axisLabel: { color: muted }, axisTick: { show: false } },
    yAxis: { splitLine: { lineStyle: { color: grid } }, axisLabel: { color: muted } },
    legend: { top: 0, textStyle: { color: cssVar("--text-secondary") }, itemWidth: 12, itemHeight: 8 },
  };
}

export function Chart({ option, height = 320, label }: { option: echarts.EChartsCoreOption; height?: number; label: string }) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    inst.current = echarts.init(el.current, undefined, { renderer: "svg" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      inst.current?.dispose();
    };
  }, []);
  useEffect(() => {
    inst.current?.setOption(option, true);
  }, [option]);
  return <div ref={el} role="img" aria-label={label} style={{ width: "100%", height }} />;
}
