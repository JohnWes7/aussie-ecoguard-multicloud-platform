# Model comparison (min_species_score=0.3, smooth=True, colour>=0.4, Rekognition>=80)

| pipeline | species exactly right | count exactly right | count abs error (total) | false alarms on negatives | colour right | avg s/image | load s | peak MB |
|---|---|---|---|---|---|---|---|---|
| md_clip | 21/26 | 14/21 | 31 | 0/5 | 2/6 | 1.76 | 106.6 | 2292 |
| md1000_clip | 21/26 | 13/21 | 31 | 0/5 | 3/6 | 2.02 | 41.3 | 2272 |
| md_bioclip | 19/26 | 13/21 | 24 | 1/5 | 2/6 | 1.97 | 68.8 | 2931 |
| oiv7 | 18/26 | 13/21 | 39 | 0/5 | 3/6 | 0.64 | 22.8 | 1872 |
| rekognition | 17/26 | 12/21 | 40 | 0/5 | 1/6 | 0.74 | 0.4 | 99 |
| speciesnet | 15/26 | 9/21 | 28 | 0/5 | 0/6 | 1.96 | 55.5 | 1708 |
| speciesnet_rules | 20/26 | 13/21 | 13 | 0/5 | 0/6 | 1.96 | 55.5 | 1708 |
| chain | 23/26 | 16/21 | 9 | 0/5 | 0/6 | 2.62 | 55.5 | 1708 |
| speciesnet_perbox | 21/26 | 15/21 | 11 | 0/5 | 0/6 | 2.50 | 55.5 | 1708 |
| chain_perbox | 23/26 | 17/21 | 9 | 0/5 | 0/6 | 3.09 | 55.5 | 1708 |

Bold = species set wrong. `?` = edge case, not scored.

| file | expected | md_clip | md1000_clip | md_bioclip | oiv7 | rekognition | speciesnet | speciesnet_rules | chain | speciesnet_perbox | chain_perbox |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fox_trap_night.jpg | fox 1 | **cat 1 (cat white)** | **cat 1 (cat grey)** | fox 1 | **cat 1 (cat grey)** | **cat 1, fox 1 (cat grey)** | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 |
| fox_otway.jpg | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | **dog 1, fox 1 (dog grey)** | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 |
| fox_mornington.jpg | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 | fox 1 |
| fox_play.jpg | fox 2 | fox 2 | fox 1 | fox 2 | **-** | **dog 1, fox 1** | fox 2 | fox 2 | fox 2 | fox 2 | fox 2 |
| rabbit_trough.jpg | rabbit many | rabbit 2 | rabbit 95 | rabbit 2 | **-** | **-** | **-** | rabbit 3 | rabbit 3 | rabbit 3 | rabbit 3 |
| rabbit_tasmania.jpg | rabbit 1 | rabbit 1 | rabbit 1 | rabbit 1 | rabbit 1 | rabbit 1 | **-** | rabbit 1 | rabbit 1 | rabbit 1 | rabbit 1 |
| rabbit_hidden.jpg | rabbit 1 | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** |
| rabbit_csiro.jpg | rabbit 12 | rabbit 9 | rabbit 10 | rabbit 9 | rabbit 4 | **cat 1, rabbit 1** | **-** | rabbit 12 | rabbit 12 | rabbit 12 | rabbit 12 |
| pig_feral.jpg | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 | feral pig 1 |
| pig_feral_group.jpg | feral pig 17 | **-** | **-** | **cat 4, dog 3, feral pig 8, rabbit 2 (cat white, dog black)** | **-** | **dog 1** | feral pig 17 | feral pig 17 | feral pig 17 | feral pig 17 | feral pig 17 |
| pig_boar_piglets.jpg | feral pig 8 | feral pig 1 | feral pig 1 | **feral pig 1, rabbit 1** | **-** | feral pig 2 | feral pig 3 | feral pig 3 | feral pig 3 | feral pig 3 | feral pig 3 |
| toad_csiro.jpg | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | **-** | **-** | cane toad 1 | **-** | cane toad 1 |
| toad_group.jpg | cane toad 2 | cane toad 2 | cane toad 2 | cane toad 2 | cane toad 2 | cane toad 1 | **-** | **-** | cane toad 2 | **cat 1** | **cat 1** |
| toad_bufo.jpg | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | cane toad 1 | **-** | **-** | cane toad 1 | **-** | cane toad 1 |
| toad_big.jpg | - | - | - | **cane toad 1** | - | - | - | - | - | - | - |
| cat_trap_feral.jpg | cat 1 | **-** | **-** | cat 1 (cat brown) | **-** | **-** | **-** | cat 1 | cat 1 | cat 1 | cat 1 |
| cat_orange.jpg | cat 1 | cat 1 (cat orange) | cat 1 (cat orange) | cat 1 (cat orange) | cat 1 (cat orange) | cat 1 (cat grey) | cat 1 | cat 1 | cat 1 | cat 1 | cat 1 |
| cat_black.jpg | cat 1 | cat 1 (cat black) | cat 1 (cat black) | cat 1 (cat black) | cat 1 (cat black) | cat 1 (cat black) | cat 1 | cat 1 | cat 1 | cat 1 | cat 1 |
| cat_dog_sleeping.jpg | cat 1, dog 1 | cat 1, dog 1 (dog grey) | cat 1, dog 1 (cat orange) | **-** | cat 1, dog 1 (cat orange, dog grey) | cat 2, dog 1 (dog grey, cat grey) | **-** | **cat 2** | **cat 2** | cat 1, dog 1 | cat 1, dog 1 |
| dog_labrador_bw.jpg | dog 1 | dog 1 (dog black) | dog 1 (dog black) | dog 1 (dog black) | dog 1 (dog black) | dog 1 (dog black) | **-** | dog 1 | dog 1 | dog 1 | dog 1 |
| dog_small_brown.jpg | dog 1 | dog 1 (dog orange) | dog 1 (dog orange) | **rabbit 1** | dog 1 (dog orange) | dog 1 (dog grey) | dog 1 | dog 1 | dog 1 | dog 1 | dog 1 |
| dingo.jpg | dog 1 ? | - | - | dog 1 (dog brown) | dog 1 (dog brown) | dog 1 (dog grey) | - | dog 1 | dog 1 | dog 1 | dog 1 |
| kangaroo_mob.jpg | - | - | - | - | - | - | - | - | - | - | - |
| kangaroo_one.jpg | - | - | - | - | - | - | - | - | - | - | - |
| possum.jpg | - | - | - | - | - | - | - | - | - | - | - |
| kookaburra_toad.jpg | cane toad 1 | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** | **-** |
| deer_trap.jpg | - | - | - | - | - | - | - | - | - | - | - |
