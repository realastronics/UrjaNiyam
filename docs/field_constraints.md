# Field constraints: what public sources say about our placeholders

**Status: first pass, public sources only. Nothing here has been validated by a grid operator.**
Every row says what the sources report, what our model currently assumes, and what we should do about it.
Facts come from the linked sources (paraphrased); anything marked *(our arithmetic)* is our own calculation from a cited number.
Confidence is about how much the evidence supports the **number** we would use, not about whether the phenomenon exists.

How to use this file: a row with "keep" needs no action; a row with "sensitivity" means we should **test the result against a range** instead of trusting one value; a row with "open" means no public source was found and an operator must tell us.

---

## 1. Summary of what changes

| # | Model item | Current placeholder | What the evidence suggests | Action |
|---|---|---|---|---|
| 1 | Consumers per feeder | 100 to 4,000 by feeder type | National average is about 1,370 per 11 kV feeder *(our arithmetic)* | Keep (same order of magnitude) |
| 2 | Farm supply hours | 8-hour blocks (06-14, 14-22, 22-06) | 8 hours of three-phase supply is the stated norm, day or night, rotated weekly in Maharashtra; a daytime window of 08:00-17:00 for solar feeders | Keep 8 hours; add a daytime 08-17 style block |
| 3 | Promise share | At least 75% of block hours on | In one crisis a Punjab utility delivered 6-7 of 8 promised hours (75-88%) | Sensitivity: 60%, 75%, 100% |
| 4 | Restoration surge | 5% + 1% per outage slot, capped at 30% | Cold-load pickup can exceed normal peak; depends on outage length, temperature and residential share | **Sensitivity: cap at 30%, 50%, 100%.** Our value is probably low for hot evenings |
| 5 | Maximum downtime, min durations, switching budgets | 3 h; 30 min; 3 per feeder and 20 system per shift | No all-India numbers. Limits are set by each state regulator | **Open.** Test a range; get state values from the regulations |
| 6 | Tiers and exemptions | Critical shed last, with approval; loss-blind | Loss-based feeder bands with exemptions for essential services exist in practice (J&K) | Keep tiers; add an optional loss-weighted policy experiment |
| 7 | Hospitals | Critical tier | Courts and states treat hospitals as exempt where they have their own feeder; shared feeders are the problem | Keep; keep the "mixed feeder" tag |

---

## 2. Evidence rows

### 2.1 Feeder size (consumers per feeder)

- **Evidence.** A CEA report on metering status as of 31 March 2024 says there are about 2.50 lakh 11 kV feeders, 1.51 crore distribution transformers and 34.18 crore consumers in the country. That is about **1,370 consumers and about 60 transformers per feeder** on average *(our arithmetic)*.
- **Our model.** Consumers per feeder: urban 800-3,000, rural 300-1,500, mixed 1,000-4,000, agricultural 100-600, industrial 5-60.
- **Assessment.** Same order of magnitude as the national average. The split by type is not in the source.
- **Action.** Keep. Confidence: medium (an average, not a distribution).
- **Source.** CEA, *Report on Metering Status of Feeders, DTs and Consumers as on 31st March 2024*, https://cea.nic.in/wp-content/uploads/2025/05/Report-on-Metering-status-of-Feeders-DTs-and-Consumers-as-on-31st-March-2024.pdf

### 2.2 Agricultural supply hours

- **Evidence.**
  - A Maharashtra utility (MSEDCL) circular of December 2025 sets three-phase availability for its agricultural load-management feeders at 8 hours, in either the day or the night, rotated on a weekly basis between day and night slots. Solarised agricultural feeders are to get 8 hours of three-phase supply in the daytime, scheduled between 08:00 and 17:00. (The scanned letter is partly garbled, so the exact wording should be re-read from the original.)
  - A power-sector explainer describes rural feeders serving mixed farm and village loads for roughly 8-10 hours, with pumps getting three-phase supply for 6-8 hours.
  - The Maharashtra solar-feeder scheme is described as reliable daytime supply for 8-10 hours between 8 am and 6 pm.
  - In Tamil Nadu, farm feeders were reported to get three-phase power for only a few hours a day in peak summer.
- **Our model.** Agricultural feeders are on only inside an 8-hour block (06-14, 14-22 or 22-06) and have zero load outside it.
- **Assessment.** Eight-hour blocks, day or night, are consistent with the evidence. A daytime window around 08:00-17:00 is missing from our block set.
- **Action.** Keep 8 hours. Add a daytime block of roughly 08:00-17:00 to the generator. Confidence: medium-high.
- **Sources.**
  - MSEDCL, *Time schedule for 3-phase power availability to Ag LM scheme feeders, January-March 2026* (letter dated 31.12.2025), https://www.mahadiscom.in/wp-content/uploads/2025/12/Letter-to-field_AGLM-time-Sch_Jan-26-to-Mar-26_31.12.2025-1.pdf
  - Bajaj Electricals, *Feeder separation*, https://bajajelectricals.com/power-distribution/feeder-separation
  - *Significance of Solar Feeders: Maharashtra Model*, https://www.shankariasparliament.com/current-affairs/gs-iii/significance-of-solar-feeders-maharashtra-model
  - *Farmers in Tiruppur seek sustained three-phase power supply to agri pump sets*, The Hindu, 30 April 2024, https://www.pressreader.com/india/the-hindu-coimbatore-9WW4/20240430/281586655668357

### 2.3 What "promised hours" means in a crisis

- **Evidence.**
  - A statement by the Punjab utility (PSPCL), carried by a news aggregator, says agricultural feeders are promised eight hours a day. During a late-June heat wave some feeders received only six to seven hours, and the utility later supplied up to 20 hours to make up the difference. (The date of the article should be confirmed from the PSPCL release.)
  - The Electricity (Rights of Consumers) Rules, 2020 (Rule 10) require 24x7 supply to all consumers, but allow the state regulator to specify lower hours for categories such as agriculture.
- **Our model.** A promised window must be on for at least 75% of its slots (6 of 8 hours).
- **Assessment.** The 75% placeholder sits at the low end of the Punjab episode (6-7 of 8 hours is 75-88%). One event is thin evidence.
- **Action.** Sensitivity: 60%, 75%, 100%. Confidence: low-medium.
- **Sources.**
  - *PSPCL News: Normal power supply restored for Punjab farmers after brief disruptions*, https://newswav.com/article/pspcl-news-normal-power-supply-restored-for-punjab-farmers-after-brief-disr-A2607_1IjYt0
  - Ministry of Power, *Electricity (Rights of Consumers) Rules, 2020* (summaries: https://en.vikaspedia.in/energy/energy-access/rights-of-power-consumers-in-the-country and https://psuwatch.com/govt-notifies-electricity-rights-of-consumers-rules-2020)

### 2.4 Restoration surge (cold-load pickup)

- **Evidence.**
  - Cold-load pickup is the extra demand when a feeder is re-energised after an outage, caused by loss of load diversity (everything starts at once) and inrush. A peer-reviewed study (Schneider et al., IEEE Transactions on Power Systems, 2016) states it can be significantly above pre-outage levels and can even exceed the normal feeder peak.
  - A utility research description (Oncor, in a survey of AI for power grids) says the size of the cold-load peak depends on outage duration, temperatures before and after the outage, and the share of residential customers, and that they model the ratio of post-outage peak to pre-outage load with a regression.
  - Outside India, a Canadian utility's rotating-outage plan (Prince Edward Island, January 2026) selects feeders using projected cold-load pickup and aims to keep each feeder's outage under one hour, preferably about 30 minutes, to limit that effect.
- **Our model.** Extra load in the first slot after a restore: 5% plus 1% per slot of outage, capped at 30% of normal load.
- **Assessment.** The effect is real. For hot-weather residential or air-conditioning-heavy feeders our cap is probably **low**. No Indian measurement was found. Our slot is 15 minutes, so what matters is the average over the first 15 minutes, which is below the instantaneous peak.
- **Action.** **Sensitivity: cap at 30% (current), 50% and 100%, and optionally a higher value for urban and mixed feeders than for industrial ones.** Confidence that the surge exists: high. Confidence in the magnitude for India: low.
- **Sources.**
  - K. P. Schneider et al., *Evaluating the Magnitude and Duration of Cold Load Pick-up on Residential Distribution Feeders using Multi-State Load Models*, IEEE Trans. Power Systems 31(5), 2016, https://www.pnnl.gov/publications/evaluating-magnitude-and-duration-cold-load-pick-residential-distribution-feeders
  - *Massively Digitized Power Grid: Opportunities and Challenges of Use-inspired AI*, https://arxiv.org/pdf/2205.05180
  - Maritime Electric, response to IRAC on rotating outages, 6 January 2026, https://irac.pe.ca/wp-content/uploads/Resp-to-IRAC-Dec-2025-letter-rotating-outages-Jan-6-2026.pdf

### 2.5 Limits on outage duration, interruptions and operations

- **Evidence.**
  - The Rights of Consumers Rules, 2020 say that the regulator specifies limits on the number of interruptions and on how long a consumer can go without supply, and that scheduled outages must be notified to consumers. The numbers are set **state by state**.
  - The Ministry of Power told the Rajya Sabha (16 March 2026) that load-shedding data and consumer categories are within the purview of each state commission, and that Grid India publishes energy requirement and energy met.
  - The Tamil Nadu performance standards (2004) pay compensation of Rs. 50 for every six hours (or part) of delay in restoring supply, up to Rs. 2,000, which shows that restoration delays are measured in six-hour units there.
  - A Minister's statement reported in the press says discoms must pay compensation for unjustified load shedding.
  - No public source was found for how many breaker operations a substation or feeder can do per shift, or for minimum on and off times.
- **Our model.** Maximum downtime 12 slots (3 h, soft); minimum cut and restore 2 slots (30 min); 3 operations per feeder and 20 per system per shift.
- **Assessment.** The structure (state-level limits, a soft downtime rule that can be relaxed under stress) matches. **The numbers are unsupported.** Our ladder results show the numbers matter a great deal: halving the maximum downtime halved the shortfall the tool can absorb without relaxing anything.
- **Action.** **Open.** (1) Extract the numeric limits from one or two state supply-code and standards-of-performance regulations. (2) Test a range. (3) Ask an operator.
- **Sources.**
  - Rights of Consumers Rules summaries (as in 2.3), and Chennai Citizens Action Group note on the Tamil Nadu standards, https://www.cag.org.in/blogs/electricity-rights-consumers-rules-2020
  - Ministry of Power, Rajya Sabha Starred Question 252, 16.03.2026, https://powermin.gov.in/sites/default/files/uploads/RS16032026_Eng.pdf
  - *Electricity rights of consumers rules: power corporations liable to pay compensation*, Greater Kashmir, https://greaterkashmir.com/front-page-2/electricity-rights-of-consumers-rules-power-corporations-liable-to-pay-compensation-to-consumers-for-poor-supply

### 2.6 Feeder tiers, exemptions and loss-based shedding

- **Evidence.**
  - In Jammu and Kashmir, a news report describes grouping 1,236 feeders into five bands by aggregate technical and commercial (AT&C) loss. About 20% of feeders (242) are treated as exempt, and these include essential services, defence areas and consumers with smart metering. One band with losses up to 40% is planned for 4.5 hours of shedding a day. The largest band, 723 feeders (58%), faces a maximum of 8-9 hours.
  - A 2014 working paper by Carnegie Mellon and Brookings India analyses how supply rostering (load shedding) is distributed across metropolitan, small-town and rural 11 kV feeders in 2012-13, and treats the distribution as an equity question.
- **Our model.** Three tiers, with harm weights as operator-set policy; loss-blind by default.
- **Assessment.** Tiers with exemptions for essential services match practice. Loss-based shedding is real in India, which is why the harm weights are policy inputs and not hard-coded. Shedding of 4.5-9 hours *per day* for chronic-deficit feeders shows the scale of exposure the tool will have to plan around on top of a single 4-hour horizon.
- **Action.** Keep the tiers. **Add an optional experiment**: a loss-weighted harm policy versus the loss-blind one, and report the difference in who is cut. Confidence: medium (news report, not a primary document).
- **Sources.**
  - *Kashmir's Load Shedding Logic*, Kashmir Life, vol. 16, issue 37, https://kashmirlife.net/kashmirs-load-shedding-logic-vol-16-issue-37-375310/
  - Brookings India Working Paper 04-2014, *Cross subsidies* (supply rostering across feeder types), https://www.brookings.edu/wp-content/uploads/2014/09/cross-subsidies-working-paper-august-v2.pdf

### 2.7 Hospitals and essential services

- **Evidence.** In a public-interest case, the Meghalaya High Court observed that if supply has to be restricted, hospitals and similar institutions should be exempted and have diesel generator backup. The state's submission was that hospitals have by and large been exempted, **provided they are connected by high-tension feeders**.
- **Our model.** Critical tier: shed last, only with approval. Mixed feeders (a hospital on a feeder shared with homes) are tagged with their highest tier.
- **Assessment.** Matches, and supports the mixed-feeder concern: exemption depends on how the hospital is connected.
- **Action.** Keep. Confidence: medium.
- **Source.** *Meghalaya High Court observes state's continuous supply of power*, India Legal, https://indialegallive.com/constitutional-law-news/courts-news/meghalaya-high-court-observes-state-scontinuous-supply-power

### 2.8 A candidate dataset for feeder realism (not yet inspected)

MSEDCL publishes weekly PDFs titled "Feederwise Load Information for All Feeders" (the ones we saw are from 2017), with load in MW per feeder per day and a feeder category such as "Urban / sheddable urban feeder", "Gaothan feeder" or agricultural. If the format and units check out, this could replace our synthetic load levels and feeder-type mix with real ones for one state. **It has not been downloaded or inspected, so it must not go into `data-sources.md` until its columns, units, time resolution and licence are recorded.**
Example: https://www.mahadiscom.in/wp-content/uploads/2018/02/ALL_FEEDERS_645_08-AUG-17_14-AUG-17.pdf

---

## 3. Sensitivity experiments this file recommends

These change one assumption at a time and re-run the ladder statistics and the loop, so the report can say "the conclusions hold across this range" instead of relying on one value.

| Experiment | Values | Why |
|---|---|---|
| Restoration surge cap | 30% (now), 50%, 100% | Evidence says our cap is probably low; the surge feeds directly into the required relief |
| Promise share for farm feeders | 60%, 75%, 100% | One crisis episode suggests 75-88% delivered |
| Maximum downtime | 3 h (now), 2 h, 6 h | Ladder results are most sensitive to it; no numeric source |
| Switching budget per feeder per shift | 2, 3 (now), 6 | No public source |
| Farm blocks | add a daytime 08:00-17:00 block | Maharashtra schedule |
| Loss-weighted harm | loss-blind versus loss-weighted | Practice in J&K |

## 4. Questions for an operator (when access is possible)

1. How many breaker operations per feeder and per substation per shift are realistic, and which feeders are manual?
2. What is the shortest time you will leave a feeder off, and on, before switching it again?
3. What is the longest continuous outage you allow, and who sets it?
4. How are farm feeders handled when supply is short? Are promised hours ever cut, and by how much?
5. How big is the load that returns when a feeder is restored after one hour, two hours and four hours, in summer?
6. Which feeders are exempt, who decides, and how do shared hospital feeders work?
7. Which of your feeder readings can you trust, and which are guesses?
8. What would make you refuse to use a tool like this?

## 5. Not found

No public source was found for: breaker operations per shift; minimum on and off times for feeders; the size of the restoration surge in Indian conditions; how often operators override a plan. These stay as placeholders, flagged, until an operator answers.