# Flat-layout MCAP quality and export check — 2026-10-27

The `商品上架` batch contained 100 MCAP files directly under one input directory,
totaling 665,084,090 bytes (about 634 MiB). The Jindian Marvin v3 profile was
used as a provisional joint/gripper quality test. Global statistics were
recomputed over all 100 files. Source MCAPs were read only.

| Measure | Result |
|---|---:|
| Successfully judged / input failures | 100 / 0 |
| Quality frames / duration | 34,612 / 1,384.490 s |
| Fully kept / partly removed / wholly discarded | 100 / 0 / 0 |
| Invalid gripper-gap frames | 0 |
| Stage1 sudden-change frames | 0 |
| Stage2 rejected episodes | 0 |
| Stage3 extreme-value frames | 0 |
| Stage4 static frames removed | 0 |
| Total removed | 0 frames, 0 s |

Stage2 mean DA scores ranged from 0.974675 to 0.999271. The lack of removals
only means that this provisional profile did not flag the batch. The quality
operators do not inspect video pixels, TCP poses, status validity flags or
tactile data. These MCAPs contain left/right gripper video channels but no
separate colour-camera channel.

One fully kept episode was exported to a new MCAP: all 21,593 topic/timestamp/
message-payload records matched the source. Separately, a **synthetic**
10-quality-frame interior deletion checked the cut path: the output MCAP was
readable, and both video channels were re-encoded and decode-validated. The
synthetic deletion was an export test, not a quality result. The v3 test suite
passed all five tests; the original 88-episode subdirectory layout remained
discoverable.
