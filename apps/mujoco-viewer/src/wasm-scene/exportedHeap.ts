/** Emscriptenの未export accessorは読むだけでabortするためdata propertyだけを見る。 */
export function exportedHeapBytes(module: object|null|undefined): number|null {
  if (!module) return null;
  const descriptor = Object.getOwnPropertyDescriptor(module, "HEAPU8");
  return descriptor && "value" in descriptor && descriptor.value instanceof Uint8Array
    ? descriptor.value.byteLength : null;
}
