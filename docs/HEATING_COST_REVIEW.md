# Heating cost review — 2026-11-05

This is a read-only diagnosis and correction proposal. No tariff settings,
Recorder data, household configuration or live automation was changed. The
existing uncommitted cost calculator and template work were present before this
task and remain separate from the Hugo adaptations.

## Measured findings

At 11:32 UTC (12:32 Europe/Prague), HA 2026.11.0 reported the cost sensor as
`partial`. The displayed annual amount was a **known subtotal**, not a full
annual cost. Values below come from that one consistent cost-sensor snapshot;
do not combine them with a later meter reading as if timestamps matched.

| Current calendar period | Recorded energy, kWh | Energy with price, kWh | Known subtotal, CZK | Energy without price, kWh |
|---|---:|---:|---:|---:|
| Today | 2.62225 | 2.61946 | 9.28960 | 0.00279 |
| Week | 11.03791 | 10.97805 | 38.87559 | 0.05986 |
| Month | 12.10626 | 12.04386 | 42.65912 | 0.06240 |
| Year | 636.75736 | 37.47988 | 133.54766 | 599.27748 |

Only **5.89%** of annual measured energy has a documented price in this
calculator. The first recorded price is 2026-10-24 03:14 UTC. Missing earlier
prices must not be reconstructed by multiplying all earlier kWh by today's
price. Today's priced-energy fraction is approximately **99.89%**, but this
does not prove the independent energy-completeness flag.

The current price source reported **4.00824 CZK/kWh**. Its installed source
selects the regular-contract dynamic `total_price`, or the non-dynamic price
for trial/non-dynamic contracts. It is not the annual account cost divided by
energy. The value's unit and code path therefore do not demonstrate a factor
of 1000 mistake. Historical rates over the last two days range from
1.89894 to 4.64424 CZK/kWh (50 returned state-history records).

At the first snapshot, heat-pump power was **−70.621 W** and backup power was
**0 W**. The requested display rule clamps negative power to zero. Thus the
displayed instantaneous rate is zero at that snapshot, independently of the
positive unit price: `(max(P_hp,0)+max(P_backup,0))/1000 * price` has units
CZK/hour. This is neither a cumulative cost nor evidence that the price is zero.
The negative meter sign needs physical-channel verification before changing
its interpretation; no automatic sign inversion is justified by these data.

## History coverage and quality

The 14-day hourly statistics query returned 335 records for each energy/power
series. It did not return price statistics. A 14-day state-history query returned
no series; that is an API coverage limitation, **not zero usage or zero price**.
A targeted 48-hour query did return 2,860 heat-pump counter states, 128 backup
counter states, 612 backup power states and the 50 price records. Price history
is therefore available in the recent window but not validated for the whole
requested year through this endpoint.

The latest backup power report was over an hour old, at a value of zero. The
calculator's `backup:power_unconfirmed` warning is real missing freshness, but
the implementation applies a current warning to the completeness flags of
every historical period. This conflates historical energy coverage with
current input quality. It must be split before treating all year/month kWh as
historically incomplete. A constant zero is not proof of fresh reporting.

The deployed calculator matches the existing local draft's historical-price
method. Its source opens Recorder read-only, uses counter deltas, separates
unpriced kWh, retains a daily archive and limits time-proportional allocation
to intervals of at most 120 seconds. It does **not** simply multiply yearly
energy by the current price. The code correctly returns `cost: null` when the
full period is not priced. The dashboard falls back to `known_cost` with a dagger,
but the dominant label is still the calendar period, which can easily look
like a complete annual bill.

## Recommended corrections, in order

1. Label every incomplete amount directly as **known subtotal**, and display
   priced-energy coverage beside it. For this snapshot the annual row should
   read approximately **133.55 CZK — subtotal, 5.89% of energy priced**, with
   **599.27748 kWh unpriced**. A dagger in a footnote is insufficient emphasis.
2. Distinguish `historical_energy_complete`, `price_coverage` and
   `current_input_quality`. A stale present power report must not silently
   rewrite the meaning of an otherwise measured past energy sum.
3. Investigate the tariff integration's own historical archive before any
   backfill. Its metadata reports 17,518 archived hours and explicit missing-HDO
   intervals. Verify price basis, units, contract period, DST and gaps for each
   interval; do not equate that count with complete usable heat-pump prices.
4. For older periods with only hourly energy, exact intrahour dynamic pricing
   cannot be recovered without finer-grained consumption. Present a separately
   marked estimate or keep those kWh unpriced; require an explicit estimation
   policy before recalculation. Never modify historical source measurements.
5. Keep tariff valuation distinct from **grid purchase cost**. These appliance
   meters include possible self-generated electricity and TUV. Billing standing
   charges and solar allocation are not apportioned to the appliances here.

Hugo's daily cloud energy history does not repair the cost problem: it is
indicative daily energy, with no intraday tariff/consumption alignment. Importing
it over the Shelly counters would introduce another basis and risk double count.

Private raw evidence and installed-source snapshots are in `.audit-private/`
inside this checkout (ignored by Git). They must not be added to a commit or
release. This report uses aggregate values and no household entity mappings.
