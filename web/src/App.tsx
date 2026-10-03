import { lazy, Suspense, useEffect } from "react";
import { useApi } from "./hooks/useApi";
import { useRoute } from "./lib/router";
import { RegionsProvider } from "./lib/regions";
import { Shell } from "./components/Shell";
import { Skeleton } from "./components/ui";
import { paths } from "./api/endpoints";
import type { Versioned } from "./api/types";

// 페이지는 처음 열 때 받는다 (첫 화면에 쓰지 않는 페이지 코드를 미리 받지 않음). ECharts 는 공용 청크로 한 번만.
const MarketPage = lazy(() => import("./pages/MarketPage"));
const RegionPage = lazy(() => import("./pages/RegionPage"));
const TradesPage = lazy(() => import("./pages/TradesPage"));
const IndexPage = lazy(() => import("./pages/IndexPage"));
const ComplexPage = lazy(() => import("./pages/ComplexPage"));
const QualityPage = lazy(() => import("./pages/QualityPage"));
const DeveloperPage = lazy(() => import("./pages/DeveloperPage"));
const OpsPage = lazy(() => import("./pages/OpsPage"));

const TITLES: Record<string, string> = {
  market: "시장 개요", region: "지역 분석", trades: "거래 목록", index: "가격지수", complex: "단지",
  quality: "데이터 품질", dev: "개발자", ops: "수집 상태",
};

function Pages() {
  const route = useRoute();
  const head = route.parts[0] ?? "market";
  const { data } = useApi<Versioned>(paths.ticker());
  useEffect(() => {
    document.title = `${TITLES[head] ?? "AptLake"} · AptLake`;
    window.scrollTo({ top: 0 });
  }, [head, route.parts[1]]);
  const page = (() => {
    switch (head) {
      case "region": return <RegionPage route={route} />;
      case "trades": return <TradesPage route={route} />;
      case "index": return <IndexPage route={route} />;
      case "complex": return <ComplexPage route={route} />;
      case "quality": return <QualityPage route={route} />;
      case "dev": return <DeveloperPage />;
      case "ops": return <OpsPage route={route} />;
      default: return <MarketPage route={route} />;
    }
  })();
  return (
    <Shell path={route.path} meta={{ version: data?.datasetVersion, asOf: data?.dataAsOf }}>
      <Suspense fallback={<Skeleton h={420} />}>{page}</Suspense>
    </Shell>
  );
}

export default function App() {
  return <RegionsProvider><Pages /></RegionsProvider>;
}
