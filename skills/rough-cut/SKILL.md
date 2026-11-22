---
name: rough-cut
description: Plan an approved scene's cut order, sound bridges and end card for ffmpeg.
kind: adapter
stage: edit
tool: ffmpeg
---

Use the scene's shot order. Include each shot only at Video Approved or Done
and require its approved shot.mp4. Stop when any shot is missing or unapproved.

A bridge repeats the outgoing clip's ambience tail under the incoming clip
and fades out at the chosen duration. Review the tail for speech before using
it. A cut without a bridge keeps its own audio. Append a title card after the
last shot.

Run `shotforge cut <scene>` or use **Rough cut** from the scene storyboard.
The scene's `rough_cut.yaml` stores the bridge durations in seconds, keyed by
the outgoing shot ID, along with `end_title` and `end_seconds`. The output
rate is the project rate. Source and record out points in the CMX 3600 EDL
are exclusive.
