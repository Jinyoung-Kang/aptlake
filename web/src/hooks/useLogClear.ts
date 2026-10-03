import { useState } from "react";
import { apiSend, errorText } from "../api/client";
import { paths } from "../api/endpoints";

/** 오류 로그 '비우기'·되돌리기 — 확인 단계, 운영자 키, 요청, 실패 문구. 성공하면 onDone (목록 다시 읽기).
 * 공개 화면의 웹 키는 보기(ops_read)만 할 수 있어 두 동작은 운영자 키(ops)가 필요하다 (QA-001). 키는 이 상태에만 둔다. */
export function useLogClear(onDone: () => void) {
  const [confirming, setConfirming] = useState<"clear" | "restore" | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (!confirming) return;
    setError(null);
    try {
      await apiSend(confirming === "clear" ? "POST" : "DELETE", paths.opsErrorsClear(), key.trim() || undefined);
      setConfirming(null);
      onDone();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return { confirming, setConfirming, key, setKey, error, run };
}
