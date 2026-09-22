# Scenario library

The scenarios the UI offers under **Load scenario**. Each file is a serialized
`fleetplanning.model.Scenario` (`<slug>.json`): holes, areas (type, size, availability
windows), mowers (model, capacity, which areas they can serve), the recent service
history, and the per-(area, mower) mowing durations.

The scenarios are **synthetic**, sampled by a scenario generator that is not part of this
snapshot, and picked to span small→large and lightly→heavily loaded fleets. Everything the
UI shows about a scenario (counts, load factor, per-area services/week bounds) is computed
live from the file, so nothing goes stale as you edit it.

| slug | holes | areas | mowers | load | availability pattern |
|---|--:|--:|--:|--:|---|
| `compact-single-mower` | 2 | 6 | 1 | 0.86 | normal |
| `well-resourced` | 4 | 14 | 7 | 0.51 | normal |
| `small-but-hard` | 2 | 6 | 3 | 1.40 | normal |
| `over-critical-load` | 5 | 16 | 4 | 1.42 | normal |
| `tight-fairway-windows` | 4 | 12 | 3 | 1.15 | 50% irrigation |
| `balanced-four-hole` | 4 | 14 | 5 | 0.93 | 75% irrigation |
| `heavy-irrigation` | 5 | 18 | 8 | 0.61 | 100% irrigation |
| `six-hole-course` | 6 | 19 | 5 | 0.76 | normal |
| `large-near-critical` | 6 | 20 | 8 | 0.99 | normal |
| `understaffed-fleet` | 4 | 15 | 1 | 1.78 | normal — the "buy more mowers" case |

*Load* is required mowing time over available fleet time; above 1 the fleet cannot meet
every requirement and the optimiser has to decide what to sacrifice. The smaller
scenarios typically prove optimal within 15–20 s on a laptop (default 4-thread portfolio);
the larger ones return the best schedule found so far when the time limit is reached.

Saving from the UI (**Save** / **Save as…**) writes into this directory.
