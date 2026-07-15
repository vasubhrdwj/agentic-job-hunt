// UI-strengthened milestone-correction views derived from the generated
// FastAPI contract. Pydantic serializes nullable lineage fields, so the
// browser treats them as present rather than optional.

import type { components } from "./api-generated";

type ApiSchemas = components["schemas"];

export type ApplicationMilestoneCorrectionCreate =
  ApiSchemas["ApplicationMilestoneCorrectionCreate"];

export type ApplicationMilestoneCorrectionResponse = Omit<
  ApiSchemas["ApplicationMilestoneCorrectionResponse"],
  "supersedes_correction_id"
> & {
  supersedes_correction_id: string | null;
};
