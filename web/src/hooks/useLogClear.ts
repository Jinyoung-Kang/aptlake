import { useState } from "react";
import { apiSend, errorText } from "../api/client";
import { paths } from "../api/endpoints";

/** 오류 로그 '비우기'·되돌리기 — 확인 단계, 요청, 실패 문구. 성공하면 onDone (목록 다시 읽기). */
export function useLogClear(onDone: () => void) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setCleared = async (clear: boolean) => {
    setError(null);
    try {
      await apiSend(clear ? "POST" : "DELETE", paths.opsErrorsClear());
      setConfirming(false);
      onDone();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return { confirming, setConfirming, error, setCleared };
}
