import { useState } from "react";

/** 펼침 상태처럼 '여러 개를 켜고 끄는' 집합. */
export function useToggleSet(): { has: (id: string) => boolean; size: number; toggle: (id: string) => void; set: (ids: Iterable<string>) => void } {
  const [items, setItems] = useState<Set<string>>(new Set());
  return {
    has: (id) => items.has(id),
    size: items.size,
    toggle: (id) => setItems((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; }),
    set: (ids) => setItems(new Set(ids)),
  };
}
