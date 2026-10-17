# Data Summary: Indian State Electricity Demand (Zenodo, Hunt & Bloomfield)

*Based on the six EDA summary files. Raw files have not been opened directly yet, so facts marked (check) need confirming in the notebook.*

## 1. What the dataset is

For each Indian state or UT, it records, **once per day**: the highest power demand that was met, the highest demand that was **not** met (unmet), and the total energy delivered. It runs from **1 Jan 2013 to 28 Apr 2024**. A second set of files holds daily weather for each state. It is **state-level and daily only**: no hours, no feeders.

## 2. How it is organized

| Folder | Files | What each row is | Unit | Size |
|---|---|---|---|---|
| `zenodo_electricity_MW` | `peak_unmet_MW`, `peak_met_MW`, `corrected_peak_met_MW` | One day; one column per state | MW (power) | 3,955 rows × 39 columns |
| `zenodo_electricity_MU` | `daily_energy_met_MU`, `corrected_daily_energy_met_MU` | One day; one column per state | MU (1 MU = 1 GWh, energy) | 3,955 rows × 39 columns |
| `zenodo_state` | One file per state (36 files) | One day of weather | Wind, temperature, dew point, solar radiation, cloud cover, heat-stress index (UTCI), each as min / mean / max | ~16,554 rows × 22 columns each |
| `zenodo_derived` | `weighted_daily_mean_utci_india` | One day, national heat-stress index | UTCI | not inspected yet |

The 39 demand columns are 36 states/UTs plus **AMNSIL**, **Damodar Valley Corporation (DVC)** and **Total**. AMNSIL and DVC look like non-state entities, so they could double-count (check).

## 3. What the numbers say

- **Unmet demand fell steeply.** The average unmet MW dropped from about 319 (2013) to about 14 (2024, partial year). Most of the "crisis" days are early in the record.
- **It is zero-heavy with a long tail.** Most states have a median unmet of 0 but a maximum in the hundreds or thousands of MW (for example Andhra Pradesh: mean 106, max 3,800).
- **Two kinds of states.**
  - *Chronic, small:* Assam, J&K, and the north-east states have unmet demand on 60-80% of days, usually small.
  - *Rare, large:* Andhra Pradesh, Punjab, Rajasthan, Gujarat have few unmet days but big spikes.
- **A few states dominate.** Uttar Pradesh (mean about 721 MW) and J&K (about 314 MW) account for much of the national total. Karnataka and Tamil Nadu follow.
- **Chandigarh is always zero**, and Sikkim is almost always zero.

## 4. Data quality issues

- **Missing values:** about 6% of cells (around 9,800 of 154,000) in each demand file. Some are structural: Telangana starts after 2014, and Dadra & Nagar Haveli and Daman & Diu have fewer rows (merged as one UT in 2020).
- **Date problems:** 55 duplicate dates, and roughly 240 calendar days are missing (for example 7 Jan to 31 Mar 2013 is absent).
- **Raw files contain errors that the "corrected" files fix.** The correction touched about 200 cells in each file. One peak-met correction was about **100,000 MW**, an obvious typo in the raw data. **Always use the corrected files.**
- **Unmet demand has no corrected version.** Its outliers need checking by hand.
- **Naming mismatch:** demand uses "Delhi", weather uses "NCT_of_Delhi", and weather names use underscores. Weather has no AMNSIL, DVC or Total. A name-mapping table is needed.
- **Totals:** the "Total" column should be compared against the sum of the states (check).

## 5. What this means for our project

- **Use it for shortfall size and frequency only.** It cannot give feeders, hourly shape, or load by consumer type, so those stay synthetic, as planned.
- **Convert to a ratio before scaling.** Use `unmet / (peak met + unmet)`: the share of demand that went unserved. This scales to any synthetic grid of 10 to 500 feeders. Raw MW cannot.
- **Define mild, moderate and crisis from non-zero days only,** using percentiles of that ratio. Because the data is mostly zeros, percentiles over all days would make every tier zero.
- **Weather is optional context.** The heat-stress index can tag which days are heatwaves and shape the synthetic evening-peak profile.
- **Document the choices:** which years, which states, and how the tiers are cut. These go in `data-sources.md`.
