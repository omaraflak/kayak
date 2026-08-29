import { HuggingFaceModelSearchResult, Modality, RuntimeDescriptor } from '../../types';

/**
 * Whether a local runtime can load a repository, from Hub metadata alone.
 *
 * Mirrors the test the runtime itself applies, and is driven entirely by the
 * descriptor the API serves — no library or model name is written down here. That
 * matters because the answer changes when a backend is added to the image, and it
 * must change without a frontend release.
 *
 * The Hub reports no library at all for a surprising share of popular models —
 * Kokoro, the most downloaded speech model, among them — so an id fragment is a
 * first-class way to match rather than a fallback.
 */
export function canRuntimeServe(
  runtime: RuntimeDescriptor,
  model: Pick<HuggingFaceModelSearchResult, 'id' | 'library_name' | 'tags'>
): boolean {
  const libraries = runtime.supported_libraries ?? [];
  const fragments = runtime.supported_id_fragments ?? [];

  // A runtime that declares no restriction serves anything with its pipeline tag,
  // which is the case for vLLM and text generation.
  if (libraries.length === 0 && fragments.length === 0) return true;

  if (model.library_name && libraries.includes(model.library_name)) return true;

  const lowered = model.id.toLowerCase();
  if (fragments.some((fragment) => lowered.includes(fragment))) return true;

  return (model.tags ?? []).some((tag) => libraries.includes(tag));
}

/** The runtime serving one modality, if the server offers one. */
export function runtimeFor(
  runtimes: RuntimeDescriptor[],
  modality: Modality
): RuntimeDescriptor | undefined {
  return runtimes.find((runtime) => runtime.modality === modality);
}

/**
 * The search task for a modality.
 *
 * Falls back to text generation so a page rendered before the descriptors arrive
 * still searches for something usable rather than failing.
 */
export function pipelineTagFor(
  runtimes: RuntimeDescriptor[],
  modality: Modality
): string {
  return runtimeFor(runtimes, modality)?.pipeline_tags[0] ?? 'text-generation';
}

/**
 * The query the catalogue opens with for a modality.
 *
 * Read from the runtime rather than kept in a table here: which model is a
 * representative example changes as backends are added, and a list in the page
 * would be one more thing that quietly goes stale. The fallback only covers the
 * moment before the descriptors have arrived.
 */
export function defaultQueryFor(
  runtimes: RuntimeDescriptor[],
  modality: Modality
): string {
  return runtimeFor(runtimes, modality)?.default_query ?? '';
}
