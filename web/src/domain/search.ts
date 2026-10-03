// 상단 검색의 규칙 (React 무관).

/** 단지 검색을 보낼 검색어 — 2글자 미만이거나 초성만이면 보내지 않는다 (초성 검색은 시군구 목록에서만). */
export function complexSearchTerm(q: string): string | null {
  const term = q.trim();
  if (term.length < 2 || /^[ㄱ-ㅎ\s]+$/.test(term)) return null;
  return term;
}
