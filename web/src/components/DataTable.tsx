import { useMemo, useState, type ReactNode } from "react";

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
    <div className="table-wrap" style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}>
      <table className="t">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th key={c.key} className={`${c.align === "right" ? "num" : ""} ${c.sort ? "sortable" : ""}`} style={{ width: c.width }}
                    title={c.title} aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                    onClick={c.sort ? () => setSort(active ? { key: c.key, dir: sort!.dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: "desc" }) : undefined}>
                  {c.header}
                  {active ? <span className="arrow">{sort!.dir === "asc" ? "▲" : "▼"}</span> : null}
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
                  onClick={onRowClick ? () => onRowClick(r) : undefined}>
                {columns.map((c) => <td key={c.key} className={c.align === "right" ? "num" : ""}>{c.cell(r, i)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
      {foot ? <div className="table-foot">{foot}</div> : null}
    </div>
  );
}
