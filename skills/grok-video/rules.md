# Rules — grok-video

Each line is a constraint the prompt must carry. What follows `(case: …)` is
why the rule exists; it stays in this file and out of the prompt.

- The camera is completely static and locked off; camera movement is done in the edit — (case: S00 pushed in 42% and accelerated)
- The action describes only what happens after the frame, never how the pose was reached — (case: characters re-staged themselves)
- Gaze direction is given in frame terms, anchored to something visible — (case: S06 looked the wrong way)
- Nobody drinks while speaking; a sip comes at the end of the shot — (case: Durik spoke mid-swallow in S10)
- The beer stays calm: no spilling, no foam, no refilling — (case: the mug overflowed)
- No subtitles and no on-screen text — (case: burned-in captions came back)
- In exterior shots with small or no characters, no character tags and no interior location tag — (case: interior tags pulled the wrong set)
- Every beat carries a timecode — (case: untimed beats came back out of order)
