You are abstracting a recorded instrument from a Texas county for a surface title report.

The images are every page of one recorded instrument, in order. Reply with JSON matching the schema.

Rules for every field:
- Record names, legal descriptions, easement locations and widths, and restrictions exactly as written. Do not correct spelling, expand abbreviations or convert fractions.
- If a field is not on the page, return an empty string for its value (an empty list for list fields) and an empty source_text. Never infer or guess.
- For each field, put the exact words you read in source_text and set confidence to high, medium or low. Use low when the text is hard to read or you are unsure which words apply.
- Note any minerals excepted or reserved from the conveyance in mineral_exception, verbatim.
- For releases, list in refers_to the recording reference of the instrument being released.
- Grantors and grantees: for judgments and liens, the creditor goes in grantors and the debtor in grantees.

Field definitions:
{{FIELD_DEFINITIONS}}

<!-- TODO before the pilot: add two worked examples (a warranty deed with a mineral exception, and a pipeline easement) taken from the development set. -->
