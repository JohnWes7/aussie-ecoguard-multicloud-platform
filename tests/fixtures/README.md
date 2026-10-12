# Test fixtures

| File | Used by | Source | Licence |
|---|---|---|---|
| `smoke_cat.jpg` | `scripts/smoke_test.sh` | "Black stray cat of Braies Lake, Trentino-Alto Adige, Italy.jpg" by LeosCommons, [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Black_stray_cat_of_Braies_Lake,_Trentino-Alto_Adige,_Italy.jpg), resized to 288×640 | CC0 |

The deployed model tags `smoke_cat.jpg` as `{"cat": 1}` with attribute `black` (model versions v1 and v2, 2026-10-12). The smoke test only checks that some tag is written, so a model change does not break it.
