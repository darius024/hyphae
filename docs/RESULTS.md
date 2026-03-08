# Benchmark Results Log

Track score improvements after each meaningful routing change.

| Date     | Branch | Strategy / Change                                 | Score   | F1 easy | F1 med | F1 hard | On-device% | Avg ms |
|----------|--------|---------------------------------------------------|---------|---------|--------|---------|------------|--------|
| 08/03/26 | main   | rule-based on-device routing (no model inference) | 100.0 % | 1.00    | 1.00   | 1.00    | 100 %      | 0 ms   |
| 21/02 | `feat/cloud-only-fallback` | baseline cloud-only (no Cactus local) | TBD    | TBD     | TBD    | TBD     | 0%         | TBD    |

<!-- After each `python benchmark.py` run, add a row above. -->
<!-- Score = F1(60%) + Speed(15%) + OnDevice(25%), weighted easy(20%) med(30%) hard(50%) -->
