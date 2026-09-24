# Parent debt evidence

The preparer supports a narrow FR Y-9LP Schedule PC funding bridge for
`capital.parent_opening_debt`. It sums the reported repo, commercial paper,
other short-term borrowing, long-term borrowing and subordinated debt balances
(BHCP0279, BHCP2309, BHCP2332, BHCP0368, BHCP4062).

All five amounts must be cited from one normalized parent-only report at the
opening date. Deposits (BHCP2200) and each related-institution balance
(BHCP3605, BHCP3606, BHCP3607) must be explicitly reported as zero for this
route. A missing field is unavailable. Nonzero related balances need a separate
principal allocation: they can include accrued interest, taxes and other
liabilities, so their full balance cannot be silently classified as debt.
Other liabilities (BHCP2930), including operating leases, must be separately
reported and are not included in the principal sum. Their related operating
cash costs remain in the parent forecast. This bridge does not estimate market value, certify
off-balance-sheet obligations, or infer equivalence with FR Y-9SP.

The normalization layer verifies the original document and field labels; the
proof layer checks entity, date, currency, duplicate components, signs and the
sum. This is evidence for a reported opening balance, not approval of a
forecast or of upstream distributions.

Basis: [Federal Reserve FR Y-9LP instructions, effective March 2026](https://www.federalreserve.gov/apps/reportingforms/Download/DownloadAttachment?guid=eb9daf46-0a00-4955-8055-a0eac6350cf5),
Schedule PC items 11–18. The source was checked on 23 September 2026.
