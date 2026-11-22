# Rules — chatgpt-identity-pass

Each line is a constraint the edit prompt must carry. What follows `(case: …)`
is why the rule exists; it stays in this file and out of the prompt.

- Change one face per round — (case: editing two at once drifted both)
- Lighting, pose, framing, wardrobe and background stay exactly as they are — (case: a face fix moved the whole frame)
- Facial hair is named explicitly — (case: Durik lost his moustache whenever it was not stated)
- Age and hair colour are named explicitly — (case: Zhmurik's came back wrong when they were left to the model)
- Hair texture is named explicitly — (case: Dima's curls flattened out)
- The face crop is attached immediately before its mask — (case: the selection was read against the wrong reference)
