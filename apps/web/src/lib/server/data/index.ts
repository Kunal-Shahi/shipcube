import "server-only";

import { mockDataEnabled } from "../config";
import type { ConsoleData } from "./source";

let source: Promise<ConsoleData> | null = null;

/** The data source for this process: the real API, or mocks when DEV_MODE=1. */
export function data(): Promise<ConsoleData> {
  source ??= mockDataEnabled()
    ? import("./mock").then((m) => m.mockData)
    : import("./http").then((m) => m.httpData);
  return source;
}

export type { ConsoleData, DemoState, Feature } from "./source";
export { FEATURE_ENDPOINT } from "./source";
