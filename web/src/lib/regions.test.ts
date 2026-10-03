import { describe, expect, it } from "vitest";
import type { Region } from "./api";
import { searchRegions } from "./regions";

const R = (sggCd: string, sidoName: string, name: string): Region => ({
  sggCd, sidoCd: sggCd.slice(0, 2), sidoName, name, fullName: `${sidoName} ${name}`,
});
const REGIONS = [
  R("11110", "서울특별시", "종로구"),
  R("11680", "서울특별시", "강남구"),
  R("41131", "경기도", "성남시 수정구"),
  R("41135", "경기도", "성남시 분당구"),
  R("43111", "충청북도", "청주시 상당구"),
  R("43113", "충청북도", "청주시 흥덕구"),
];
const codes = (q: string) => searchRegions(REGIONS, q).map((r) => r.sggCd);

describe("시군구 검색", () => {
  it("이름 앞부분 > 단어 앞부분 > 포함 > '시도 시군구'", () => {
    expect(codes("강남")).toEqual(["11680"]);
    expect(codes("분당")).toEqual(["41135"]); // '성남시 분당구' 의 단어 앞부분
    expect(codes("성남")).toEqual(["41131", "41135"]);
    expect(codes("경기 분당")).toEqual(["41135"]);
  });
  it("초성은 시군구 이름에만 (시도 초성까지 보면 너무 많이 맞는다)", () => {
    expect(codes("ㅂㄷ")).toEqual(["41135"]);
    expect(codes("ㅊㅈ")).toEqual(["43111", "43113"]);
  });
  it("숫자는 코드 앞부분, 빈 질의는 결과 없음", () => {
    expect(codes("4113")).toEqual(["41131", "41135"]);
    expect(codes("  ")).toEqual([]);
  });
});
