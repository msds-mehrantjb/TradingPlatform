import { easternMinutes, isRegularSession, sessionLabelForMinutes } from "../indicators.ts";
import type { RegimeTimeOfDayContext } from "../types.ts";

export function resolveRegimeSessionContext(timestamp: string): RegimeTimeOfDayContext {
  const minutes = easternMinutes(timestamp);
  return Object.freeze({
    minutes,
    label: isRegularSession(timestamp) ? sessionLabelForMinutes(minutes) : "Outside regular session",
    weightMultiplier: minutes < 10 * 60 ? 1.1 : minutes >= 15 * 60 ? 0.75 : 1,
    newTradesAllowed: isRegularSession(timestamp) && minutes < 15 * 60 + 45,
  });
}

