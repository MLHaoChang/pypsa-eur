/** A component class in the user's words, singular ("StorageUnit" → "Storage").
 *  One map for the header search, flow items and the field drawer. */
export const COMPONENT_LABEL: Record<string, string> = {
  Bus: 'Bus', Line: 'Line', Generator: 'Generator', StorageUnit: 'Storage', Store: 'Store',
  Load: 'Load', Link: 'Link', Transformer: 'Transformer', ShuntImpedance: 'Shunt', Carrier: 'Carrier',
}
export const componentLabel = (cls: string): string => COMPONENT_LABEL[cls] ?? cls
