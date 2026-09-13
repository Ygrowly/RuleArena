/**
 * Read and write the `a.b[0].c` paths the compiler uses to point at an ambiguity.
 *
 * Mirrors `value_at_path` in the compiler so the suggestion a question shows and the
 * field the answer is written back to are the same location. A path that does not
 * resolve is left alone rather than created: a question about `chinese_modification`
 * has no home inside the RuleSpec, and inventing one would silently change the contract.
 */

type Json = Record<string, unknown> | unknown[];

function segments(path: string): { name: string; index: number | null }[] | null {
  const parsed: { name: string; index: number | null }[] = [];
  for (const segment of path.split(".")) {
    const match = /^([^[\]]+)(?:\[(\d+)\])?$/.exec(segment);
    if (match === null) {
      return null;
    }
    parsed.push({ name: match[1], index: match[2] === undefined ? null : Number(match[2]) });
  }
  return parsed.length > 0 ? parsed : null;
}

export function valueAtPath(document: unknown, path: string): unknown {
  const parts = segments(path);
  if (parts === null) {
    return undefined;
  }
  let current: unknown = document;
  for (const part of parts) {
    if (typeof current !== "object" || current === null || !(part.name in current)) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part.name];
    if (part.index !== null) {
      if (!Array.isArray(current) || part.index >= current.length) {
        return undefined;
      }
      current = current[part.index];
    }
  }
  return current;
}

export function setAtPath(document: Json, path: string, value: unknown): Json {
  const parts = segments(path);
  if (parts === null) {
    return document;
  }
  const clone = structuredClone(document);
  let current: unknown = clone;
  for (const part of parts.slice(0, -1)) {
    if (typeof current !== "object" || current === null || !(part.name in current)) {
      return document;
    }
    current = (current as Record<string, unknown>)[part.name];
    if (part.index !== null) {
      if (!Array.isArray(current) || part.index >= current.length) {
        return document;
      }
      current = current[part.index];
    }
  }
  const last = parts[parts.length - 1];
  if (typeof current !== "object" || current === null || !(last.name in current)) {
    return document;
  }
  if (last.index === null) {
    (current as Record<string, unknown>)[last.name] = value;
  } else {
    const list = (current as Record<string, unknown>)[last.name];
    if (!Array.isArray(list) || last.index >= list.length) {
      return document;
    }
    list[last.index] = value;
  }
  return clone;
}
