/**
 * What a step card says about a tool call before it is opened.
 *
 * The card used to lead with the function name and the first characters of its
 * JSON -- `run_command {"command": "ls -la /work` -- which is precise and reads as
 * noise to anyone who has not written one. It now reads as a sentence fragment
 * ("Run command") with the one detail that says what the step was about.
 *
 * Derived from the call itself rather than from a table of known tools, so a tool
 * someone writes tomorrow is described the same way.
 */

/** Arguments that name what a step acted on, most telling first. */
const TELLING_ARGUMENTS = ['command', 'path', 'file_path', 'url', 'query', 'pattern', 'name'];

const MAX_DETAIL_LENGTH = 60;

/** "run_command" -> "Run command". */
export function humanizeToolName(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim();
  if (!words) return 'Step';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The one argument worth showing on the card's header, if there is one. */
export function toolCallDetail(argumentsStr: string | undefined): string | null {
  if (!argumentsStr) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(argumentsStr);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

  const values = parsed as Record<string, unknown>;
  const key =
    TELLING_ARGUMENTS.find((candidate) => typeof values[candidate] === 'string') ??
    Object.keys(values).find((candidate) => typeof values[candidate] === 'string');
  if (!key) return null;

  const text = String(values[key]).replace(/\s+/g, ' ').trim();
  if (!text) return null;
  return text.length > MAX_DETAIL_LENGTH ? `${text.slice(0, MAX_DETAIL_LENGTH)}…` : text;
}
