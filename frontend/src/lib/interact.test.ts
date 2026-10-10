import { describe, expect, it } from "vitest";
import { isRepeatClick } from "./interact";

describe("isRepeatClick", () => {
  it("is false with no previous click", () => expect(isRepeatClick(null, 100, 10)).toBe(false));
  it("is true within time and distance", () => expect(isRepeatClick({ t: 100, x: 10 }, 300, 13)).toBe(true));
  it("is false when too late", () => expect(isRepeatClick({ t: 100, x: 10 }, 600, 10)).toBe(false));
  it("is false when too far", () => expect(isRepeatClick({ t: 100, x: 10 }, 200, 30)).toBe(false));
});
