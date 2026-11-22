# Recorded sound scenarios for the CMPE281 demo

These five recordings are bundled for Rishi Sheth's noncommercial educational
C2/C3 demonstration. They come from [ESC-50](https://github.com/karolpiczak/ESC-50),
curated by Karol J. Piczak, at source commit
`33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6`. Each is an unchanged ESC-50 WAV: five
seconds, mono, 44.1 kHz. No audio was transformed after obtaining the ESC-50 files.
ESC-50 previously prepared these clips from the original recordings credited below.

The dataset is distributed under [Creative Commons Attribution-NonCommercial 3.0
Unported](https://creativecommons.org/licenses/by-nc/3.0/). Retain these credits,
the license link, and [the complete upstream notices](ESC-50-LICENSE.txt) when
redistributing these demonstration assets. Commercial use of this bundled dataset
subset is outside that permission. The original source licenses below are recorded
as stated in ESC-50's notices; they do not replace this dataset license notice.

| Scenario | ESC-50 recording | Original title and creator | Original source | Original license |
| --- | --- | --- | --- | --- |
| Emergency | `1-54084-A-42.wav` (siren) | `civildefense.wav` by guitarguy1985 | [Freesound 54084](https://freesound.org/people/guitarguy1985/sounds/54084/) | CC0 |
| In vehicle | `1-20133-A-39.wav` (glass breaking) | `breaking_glass.wav` by gezortenplotz | [Freesound 20133](https://freesound.org/people/gezortenplotz/sounds/20133/) | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| Human | `1-211527-A-20.wav` (crying baby) | `Crying newborn baby child 3.WAV` by the_yura | [Freesound 211527](https://freesound.org/people/the_yura/sounds/211527/) | CC0 |
| Traffic | `1-17124-A-43.wav` (car horn) | `car sharp horn.wav` by cognito perceptu | [Freesound 17124](https://freesound.org/people/cognito%20perceptu/sounds/17124/) | CC0 |
| Environmental | `1-17367-A-10.wav` (rain) | `unlocki door to mod rain.wav` by cognito perceptu | [Freesound 17367](https://freesound.org/people/cognito%20perceptu/sounds/17367/) | CC0 |

The glass-breaking recording is used to illustrate an in-vehicle incident. It was
not recorded in this simulated vehicle. The camera and GPS belong to the virtual
local edge demonstration; audio uses the actual recorded clips.

[manifest.json](manifest.json) includes pinned raw source links, SHA-256 checksums,
durations, original source attribution, and license information.
[esc50-selected.csv](esc50-selected.csv) retains the selected upstream metadata.
[scenarios.json](scenarios.json) is the default five-category mapping used by
`python -m edge.demo_client`.

A scenario is the selected recording and the upload's `trigger` metadata. The CNN
predicts independently; its result may differ from the selected scenario. This
small set demonstrates component behavior and provides no accuracy estimate or
claim that these samples are held out from training. The original creators and
dataset curator do not endorse this application.
