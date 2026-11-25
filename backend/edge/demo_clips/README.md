# Recorded sound scenarios for the CMPE281 demo

These five recordings are bundled for the noncommercial educational C2/C3
demonstration, one per sound class. The edge clients upload them when a scenario runs
(see [`../edge_scenarios.json`](../edge_scenarios.json), the mapping all edge clients use).

| Scenario | Recording | Source | Original title and creator | Original license |
| --- | --- | --- | --- | --- |
| Emergency | `emergency.wav` (siren) | ESC-50 `1-54084-A-42.wav` | `civildefense.wav` by guitarguy1985, [Freesound 54084](https://freesound.org/people/guitarguy1985/sounds/54084/) | CC0 |
| In vehicle | `in_vehicle.wav` (glass breaking) | ESC-50 `1-20133-A-39.wav` | `breaking_glass.wav` by gezortenplotz, [Freesound 20133](https://freesound.org/people/gezortenplotz/sounds/20133/) | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| Human | `human.wav` (female scream) | course dataset | `Human_Scream/female-scream-1-86768.wav`, creator not recorded | as provided for the course |
| Traffic | `traffic.wav` (road traffic) | course dataset | `road_traffic_dataset/road (87).wav`, creator not recorded | as provided for the course |
| Environmental | `environmental.wav` (thunderstorm) | course dataset | `Environmental_Sounds/thunderstorm (74).wav`, creator not recorded | as provided for the course |

## ESC-50 clips (emergency, in vehicle)

They come from [ESC-50](https://github.com/karolpiczak/ESC-50), curated by Karol J.
Piczak, at source commit `33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6`. Each is an
unchanged ESC-50 WAV (five seconds, mono, 44.1 kHz), renamed after its sound class;
the original ESC-50 filename is kept in the table and in [manifest.json](manifest.json).

ESC-50 is distributed under [Creative Commons Attribution-NonCommercial 3.0
Unported](https://creativecommons.org/licenses/by-nc/3.0/).
- Keep these credits, the license link and
  [the complete upstream notices](ESC-50-LICENSE.txt) when redistributing them.
- Commercial use is outside that permission.
- The original licenses above are as stated in ESC-50's notices.

## Course dataset clips (human, traffic, environmental)

They come from the CMPE 281 course-provided `Audio_Datasets`, which was used to train
`cnn-v1`.
- **Held out:** all three are from the **validation or test split**, so the model never
  trained on them.
- **Format:** the 16 kHz mono versions prepared by `model/dataset_prep.py`, set to
  exactly 5.0 s. The scream was padded with silence from 2.69 s; the other two were
  adjusted by a few samples.
- **Creators:** the course dataset doesn't record their original creators or sources.

| Clip | `cnn-v1` prediction |
| --- | --- |
| scream | human, 0.996 |
| road traffic | traffic, 0.999 |
| thunderstorm | environmental, 0.989 |

## Notes

- **Files:** [manifest.json](manifest.json) lists every clip's SHA-256 checksum,
  duration and available source/license metadata. The two ESC-50 entries retain
  their original filenames, creator attribution and pinned upstream source URLs.
  The course-provided clips have no supplied license metadata; the ESC-50 license
  does not cover those three files.
- **Glass breaking** stands in for an in-vehicle incident; it wasn't recorded in a car.
- **Scenario vs. prediction:** a scenario is the selected recording plus the upload's
  `trigger` metadata. The CNN predicts independently, so its result may differ from
  the selected scenario.
- **Endorsement:** the original creators and dataset curators don't endorse this
  application.
