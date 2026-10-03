// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { usePopover } from "./ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Picker() {
  const { open, setOpen, ref } = usePopover();
  return (
    <div ref={ref}>
      <button type="button" aria-haspopup="dialog" onClick={() => setOpen(!open)}>열기</button>
      {open && <div role="dialog"><input aria-label="찾기" /></div>}
    </div>
  );
}

describe("QA-016 팝업을 Esc 로 닫으면 초점이 연 버튼으로 돌아온다 (WAI-ARIA 대화상자 패턴)", () => {
  it("팝업 안 입력란에서 Esc → 연 버튼에 초점", async () => {
    const el = document.createElement("div");
    document.body.replaceChildren(el);
    const root = createRoot(el);
    await act(async () => root.render(<Picker />));
    const button = el.querySelector("button") as HTMLButtonElement;
    await act(async () => button.click());
    const input = el.querySelector("input") as HTMLInputElement;
    input.focus();
    await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(el.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(button);
    await act(async () => root.unmount());
  });
});
