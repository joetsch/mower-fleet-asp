// Vitest setup for the jsdom component tests (ADR-0028): the jest-dom matchers
// (toBeInTheDocument, toBeDisabled, …), a predictable in-memory localStorage, and
// cleanup after each test.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom's localStorage is flaky under recent Node (a native experimental localStorage
// shadows it). A tiny in-memory store is enough for the UI hooks and keeps tests isolated.
class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  clear() {
    this.m.clear();
  }
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  key(i: number) {
    return [...this.m.keys()][i] ?? null;
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
}
vi.stubGlobal("localStorage", new MemoryStorage());

// jsdom does not implement scrollIntoView; App calls it to bring the Scenario card back
// into view from the schedule. A no-op is all the component tests need.
Element.prototype.scrollIntoView = vi.fn();

beforeEach(() => localStorage.clear());
afterEach(cleanup);
