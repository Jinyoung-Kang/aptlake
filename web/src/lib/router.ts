import { useEffect, useState } from "react";

// 해시 라우터: #/경로?키=값 — 새로고침·공유·뒤로 가기가 그대로 동작한다.
export type Route = { path: string; parts: string[]; params: URLSearchParams };

const LEGACY: Record<string, string> = {
  "#region": "#/region", "#trades": "#/trades", "#index": "#/index", "#quality": "#/quality", "#dev": "#/dev",
};

export function parseHash(hash = location.hash): Route {
  const raw = LEGACY[hash] ?? hash;
  const s = raw.replace(/^#/, "") || "/market";
  const [pathPart, query = ""] = s.split("?");
  const path = pathPart.startsWith("/") ? pathPart : `/${pathPart}`;
  return { path, parts: path.split("/").filter(Boolean), params: new URLSearchParams(query) };
}

export function href(path: string, params?: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const qs = q.toString();
  return `#${path}${qs ? `?${qs}` : ""}`;
}

export function navigate(path: string, params?: Record<string, string | number | boolean | null | undefined>, replace = false) {
  const h = href(path, params);
  if (replace) history.replaceState(null, "", h);
  else history.pushState(null, "", h);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash());
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    window.addEventListener("popstate", on);
    return () => {
      window.removeEventListener("hashchange", on);
      window.removeEventListener("popstate", on);
    };
  }, []);
  return route;
}

/** 현재 경로의 쿼리 값 일부만 바꾼다 (기록을 쌓지 않음). */
export function setParams(route: Route, patch: Record<string, string | number | boolean | null | undefined>) {
  const p: Record<string, string> = {};
  route.params.forEach((v, k) => (p[k] = v));
  navigate(route.path, { ...p, ...patch }, true);
}
