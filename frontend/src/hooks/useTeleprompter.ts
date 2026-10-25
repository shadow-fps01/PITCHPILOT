/**
 * useTeleprompter — maps the live spoken transcript onto the script outline.
 *
 * Runs a two-pointer word alignment (transcript words may contain extra
 * ums/repetitions; the outline pointer only advances on matches), yielding
 * the spoken-through index that drives highlighting and auto-scroll.
 */

import { useMemo } from "react";

export interface TeleprompterToken {
  /** Original outline word (with punctuation) for rendering. */
  raw: string;
  /** Normalized word used for matching. */
  normalized: string;
}

export interface UseTeleprompterResult {
  tokens: TeleprompterToken[];
  /** Index of the first not-yet-spoken outline word. */
  currentWordIndex: number;
  /** Fraction of the outline already covered (0..1). */
  progress: number;
}

const NON_WORD = /[^a-z0-9'\-]/g;

export function normalizeWord(word: string): string {
  return word.toLowerCase().replace(NON_WORD, "");
}

export function tokenizeOutline(outline: string): TeleprompterToken[] {
  return outline
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .map((raw) => ({ raw, normalized: normalizeWord(raw) }))
    .filter((token) => token.normalized.length > 0);
}

/** Greedy alignment: advance the outline pointer on transcript matches. */
function alignProgress(
  outlineTokens: TeleprompterToken[],
  transcript: string,
): number {
  const spoken = transcript
    .split(/\s+/)
    .map(normalizeWord)
    .filter((word) => word.length > 0);
  let outlineIndex = 0;
  for (const word of spoken) {
    if (outlineIndex >= outlineTokens.length) break;
    if (word === outlineTokens[outlineIndex].normalized) {
      outlineIndex += 1;
    }
  }
  return outlineIndex;
}

export function useTeleprompter(
  outline: string,
  finalTranscript: string,
): UseTeleprompterResult {
  const tokens = useMemo(() => tokenizeOutline(outline), [outline]);
  const currentWordIndex = useMemo(
    () => alignProgress(tokens, finalTranscript),
    [tokens, finalTranscript],
  );
  const progress =
    tokens.length > 0 ? Math.min(1, currentWordIndex / tokens.length) : 0;
  return { tokens, currentWordIndex, progress };
}
