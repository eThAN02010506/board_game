export const UI_CORROBORATION_KINDS = [
  "session_end_visible",
  "continue_campaign_visible",
  "authoritative_ending_visible"
] as const;

export type UiCorroborationKind = typeof UI_CORROBORATION_KINDS[number];

export type UiCorroborationEvent = Readonly<{
  kind: UiCorroborationKind;
  observed_at: string;
  session_index: number;
}>;

/**
 * Records a deliberately narrow player-visible timeline for later DB
 * reconstruction. These events corroborate that a control or result was
 * visible; they never claim that the underlying transition was authoritative.
 */
export class UiCorroborationCollector {
  private readonly events: UiCorroborationEvent[] = [];
  private lastObservedAt = 0;

  constructor(private readonly now: () => Date = () => new Date()) {}

  record(kind: UiCorroborationKind, sessionIndex: number): UiCorroborationEvent {
    if (!Number.isInteger(sessionIndex) || sessionIndex < 1) {
      throw new Error("UI corroboration session index must be a positive integer");
    }
    const candidate = this.now().getTime();
    if (!Number.isFinite(candidate)) {
      throw new Error("UI corroboration clock returned an invalid timestamp");
    }
    const observedAt = Math.max(candidate, this.lastObservedAt + 1);
    this.lastObservedAt = observedAt;
    const event = Object.freeze({
      kind,
      observed_at: new Date(observedAt).toISOString(),
      session_index: sessionIndex
    });
    this.events.push(event);
    return event;
  }

  snapshot(): UiCorroborationEvent[] {
    return this.events.map((event) => ({ ...event }));
  }
}
