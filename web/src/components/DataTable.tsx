import { useMemo, useState, type ReactNode } from "react";
import { sqm } from "./ui";

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T, i: number) => ReactNode;
  sort?: (row: T) => number | string | null;
  align?: "left" | "right";
  width?: number | string;
  title?: string;
};

/** 조밀한 정렬 표. 정렬 상태는 컴포넌트 안에만 (URL 에 남길 필요가 없는 보기 설정). */
export function DataTable<T>({ rows, columns, rowKey, onRowClick, selectedKey, initialSort, empty, maxHeight, foot, caption }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; selectedKey?: string | null;
  initialSort?: { key: string; dir: "asc" | "desc" }; empty?: ReactNode; maxHeight?: number | string; foot?: ReactNode;
  caption?: string;
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1; // 빈 값은 정렬 방향과 무관하게 뒤로
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }, [rows, columns, sort]);
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: 스크롤 영역은 키보드로도 넘길 수 있어야 한다 (WCAG 2.1.1, axe scrollable-region-focusable, QA-011)
    <section className="table-wrap" tabIndex={0} aria-label={caption ?? "표"} style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}>
      <table className="t">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => {
              const dir = sort && sort.key === c.key ? sort.dir : null;  // 이 열로 정렬 중이면 방향
              const arrow = dir ? <span className="arrow" aria-hidden="true">{dir === "asc" ? "▲" : "▼"}</span> : null;
              // 정렬 전 표시(↕)도 화살표와 같은 쪽에 — 오른쪽 정렬 열에서 처음 정렬할 때 머리글 글자가 움직이지 않게
              const idle = <span className="arrow idle" aria-hidden="true">↕</span>;
              return (
                <th key={c.key} className={`${c.align === "right" ? "num" : ""} ${c.sort ? "sortable" : ""}`} style={{ width: c.width }}
                    title={c.title} aria-sort={dir ? (dir === "asc" ? "ascending" : "descending") : undefined} scope="col">
                  {c.sort ? (
                    // 정렬은 머리글 안의 버튼으로 — 키보드(Tab·Enter·Space)와 화면낭독기로도 쓸 수 있게
                    <button type="button" className="th-sort" onClick={() => setSort(dir ? { key: c.key, dir: dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: "desc" })}>
                      {c.align === "right" ? (arrow ?? idle) : null}
                      {sqm(c.header)}
                      {c.align !== "right" ? (arrow ?? idle) : null}
                    </button>
                  ) : sqm(c.header)}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr><td colSpan={columns.length}><div className="empty">{empty ?? "데이터가 없습니다."}</div></td></tr>
          ) : sorted.map((r, i) => {
            const k = rowKey(r);
            return (
              <tr key={k} className={`${onRowClick ? "clickable" : ""} ${selectedKey === k ? "selected" : ""}`}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  // 누를 수 있는 행은 키보드로도: Tab 으로 이동, Enter·Space 로 선택
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRowClick(r); } } : undefined}>
                {columns.map((c) => <td key={c.key} className={c.align === "right" ? "num" : ""}>{c.cell(r, i)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
      {foot ? <div className="table-foot">{foot}</div> : null}
    </section>
  );
}
