# variation divisor calibration — v2.4.0 (ADR 0019)

Fitted over ALL 2446 frozen `mx_` dumps in `raw/` — offline, no engine, no sampling:

    python3 calibrate_divisors.py --reuse <KB>/raw --prefix mx_

Rule: the corpus p99 of each group's raw signal normalises to 0.85 (ADR 0010). The 8 nursing
clips are KB content, not calibration inputs. "current" below is the v2.3.0 value, fitted on a
150-clip random sample; the sample's p99 sat up to 41% off the population's (root_vert), which is
why the full-population refit moves the root divisors the most.

```
group               n       p50       p90       p99       max   current |    fitted   sat.now sat.new
torso            2446    0.0726    0.1656    0.2524    0.3349    0.3174 |    0.2969      0.0%    0.1%
head             2446    0.1381    0.2934    0.4219    0.5323    0.5809 |    0.4963      0.0%    0.2%
arm              4892    0.1912    0.4147    0.5834    0.7998    0.6914 |    0.6864      0.2%    0.2%
leg              4892    0.1694    0.2927    0.3798    0.4999    0.4296 |    0.4468      0.2%    0.1%
hand             4892    0.1025    0.4119    0.6414    0.9282    0.7327 |    0.7546      0.2%    0.2%
root_trans       2446    0.1258    0.7984    1.6815    5.7685    1.5637 |    1.9782      1.4%    0.3%
root_vert        2446    0.0738    0.6773    1.5636    2.7677    1.3009 |    1.8396      1.9%    0.6%
root_heading     2446    9.7092   62.6182  112.0789  150.6787  142.1000 |  131.8575      0.0%    0.2%

2446 clips sampled, 0 failed. Nothing written to the KB.
Applying these means: bump metric_formula_version, re-extract every accepted record,
re-freeze test_golden_extraction.py, and record the change in a new ADR.
```
