import { useEffect, useState } from "react";
import { api, errorText, type Region } from "./api";
import RegionPage from "./pages/RegionPage";
import TradesPage from "./pages/TradesPage";
import IndexPage from "./pages/IndexPage";
import QualityPage from "./pages/QualityPage";
import DeveloperPage from "./pages/DeveloperPage";

const TABS = [
  ["region", "지역 탐색"],
  ["trades", "거래 목록"],
  ["index", "지수"],
  ["quality", "데이터 품질"],
  ["dev", "개발자"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function App() {
  const [tab, setTab] = useState<Tab>(() => (location.hash.slice(1) as Tab) || "region");
  const [regions, setRegions] = useState<Region[]>([]);
  const [meta, setMeta] = useState<{ v?: string; asOf?: string | null }>({});
  const [loadErr, setLoadErr] = useState<string | null>(null);
  useEffect(() => {
    api<{ items: Region[]; datasetVersion: string; dataAsOf: string | null }>("/v1/regions")
      .then((r) => {
        setRegions(r.items);
        setMeta({ v: r.datasetVersion, asOf: r.dataAsOf });
      })
      .catch((e) => { setRegions([]); setLoadErr(errorText(e)); });
  }, []);
  useEffect(() => {
    history.replaceState(null, "", `#${tab}`);
  }, [tab]);
  useEffect(() => {
    const onHash = () => {
      const t = location.hash.slice(1) as Tab;
      if (TABS.some(([id]) => id === t)) setTab(t);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return (
    <>
      <header>
        <h1>AptLake 집값 레이크하우스</h1>
        <span className="meta">
          데이터셋 {meta.v ?? "-"} · 원천 관측 {meta.asOf ? new Date(meta.asOf).toLocaleString("ko-KR") : "-"} · 공식 통계 아님
        </span>
      </header>
      {loadErr && <p className="error" style={{ margin: "8px 24px 0" }}>시군구 목록을 불러오지 못했습니다 — {loadErr}</p>}
      <nav aria-label="화면">
        {TABS.map(([id, name]) => (
          <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>
            {name}
          </button>
        ))}
      </nav>
      <main>
        {tab === "region" && <RegionPage regions={regions} />}
        {tab === "trades" && <TradesPage regions={regions} />}
        {tab === "index" && <IndexPage regions={regions} />}
        {tab === "quality" && <QualityPage regions={regions} />}
        {tab === "dev" && <DeveloperPage />}
      </main>
    </>
  );
}
