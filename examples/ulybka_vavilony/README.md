# Example project — Улыбка Вавилоны, scene 1

A small, valid project used by the tests and as a shape to copy. It holds the
bible, one scene (`s01_pub`) and three shots (`sh06`, `sh07`, `sh08`).

What comes from the design doc, as written there: the shot spec for `sh07`, the
`dima` character file, the scene's seating and 180° axis, and the world rules
about the plaster walls and the calm beer.

Everything else — `sh06` and `sh08`, the `durik` and `zhmurik` files, the style
text — is filled in so the chain has two cuts to check and the prompts have
something to render. Replace it with the real design before using it for a film.

The pub includes a generated example location sheet and a plaster texture
derived from its swatch. The sheet illustrates the written design for this
sample project. Use your own approved location sheet for production.
Generate all three blockout PNGs and a shared Blender scene with the
blockout command below. Character sheets and references can be added during
production.

```bash
shotforge validate -C examples/ulybka_vavilony
shotforge blockout s01_pub -C examples/ulybka_vavilony
shotforge status   -C examples/ulybka_vavilony
shotforge serve    -C examples/ulybka_vavilony
```

Open the **Bible** to see the pub sheet and its plaster texture together.
To inspect the material in one frame, run
`shotforge blockout s01_pub sh07 -C examples/ulybka_vavilony` and open
`03_blender/renders/s01_pub/sh07/blockout.png` in the example folder.
