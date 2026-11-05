You are abstracting a recorded instrument from a Texas county for a surface title report.

The images are every page of one recorded instrument, in order. Reply with JSON matching the schema.

Rules for every field:
- Record names, legal descriptions, easement locations and widths, and restrictions exactly as written. Do not correct spelling, expand abbreviations or convert fractions.
- If a field is not on the page, return an empty string for its value (an empty list for list fields) and an empty source_text. Never infer or guess.
- For each field, put the exact words you read in source_text and set confidence to high, medium or low. Use low when the text is hard to read or you are unsure which words apply.
- Note any minerals excepted or reserved from the conveyance in mineral_exception, verbatim. Leave it empty if there is none; do not describe its absence.
- pore_space: copy, verbatim, any sentence that grants, reserves, excepts, leases or mentions pore space, subsurface or underground storage, geologic storage, salt caverns, or CO2 injection, storage or sequestration rights. Read the whole instrument for it, including exhibits and reservations. A plain reservation of 'oil, gas and other minerals' is not pore space language.
- Numbers: copy every number exactly as written, including both the words and the digits when the instrument gives both (e.g. "an undivided seven per cent (7%)"), in value and source_text. Read digits carefully: 1 and 7, 3 and 8, 5 and 6 are easy to confuse on old typed copies. If a digit is hard to read, set confidence to low.
- Grantee shares: when the instrument gives each grantee a share ("each receiving a 11.625% interest", "an undivided one-half to ..."), put each grantee's share, as written, in that party's share.
- refers_to: only the recording references (volume/page or instrument number) of earlier instruments this one cites, releases or corrects, one per item, e.g. 'Vol. 594, Page 624' or 'Instrument No. 2011009176'. No descriptions.
- Dates: give the value as YYYY-MM-DD and the words as written in source_text. Use the clerk's filed or recorded stamp for filing_date.
- instrument_type: the title of the instrument as written on it (one value). Never 'other'. If there is no title, or the title doesn't match what the document does, give the type its operative words show (e.g. 'Easement' for an untitled grant of a pipeline right-of-way) and set confidence to medium or low. Decide what the document is from the whole instrument (granting words, what is conveyed or reserved, who gets what), not the header alone.
- operative_language: the operative words of grant, verbatim ('grant, sell and convey', 'grant an easement', 'lease and let', 'release and discharge').
- Field groups: the LIENS and EASEMENTS & LEASES groups below are filled only for the instruments they name. When you can't tell whether one applies, fill it: an extra group costs little, a missing one loses information.
- List formats (one item per entry, parts separated by ' | ', write 'unknown' for a part you can't read):
  - signatures: name as signed | capacity in the signature block ('individual', 'trustee', 'attorney-in-fact', 'President of ABC LLC', ...) | 'signed' or 'NOT SIGNED'. One item for every party named in the granting clause, including those who did not sign.
  - acknowledgment: name acknowledged | capacity | venue (county, state) | notary name | seal: yes/no | commission expires: date | date acknowledged. 'none' as the only item if there is no acknowledgment.
  - metes_and_bounds_calls: bearing | distance with its unit | monument, in order, starting with 'POB | | <monument>' for the point of beginning (e.g. 'N 45°30'15" E | 1,234.56 feet | 1/2 in. iron rod found'). Curves: 'CURVE | arc length with unit | radius, chord bearing and chord distance'.
  - reserved_fractions: fraction as written | what it is of ('of the minerals', 'of the royalty', 'of the surface').
  - recited_liens: type | holder | amount | date, as recited.
  - description_references: reference | recorded, attached, not recorded or not attached.
- Lien fields: copy amounts, rates and terms as written. Give maturity_date only when the instrument states it. Do not calculate balances or remaining terms.
- recording_stamp: write 'missing' if there is no clerk's recording stamp.
- acreage: the acreage this instrument conveys or covers, as a number only.
- instrument_id: the recording reference only (e.g. 'Vol. 708, Page 1' or 'Instrument No. 2012012111'), without the county name or page ranges.
- Grantors and grantees: for judgments, abstracts of judgment, lis pendens and lien claims (tax, mechanic's, other), the creditor or claimant goes in grantors and the debtor in grantees. For deeds of trust, the borrower is the grantor and the lender or trustee is the grantee. For UCC financing statements, the debtor is the grantor and the secured party is the grantee.

Field definitions:
{{FIELD_DEFINITIONS}}

<!-- TODO before the pilot: add two worked examples (a warranty deed with a mineral exception, and a pipeline easement) taken from the development set. -->
