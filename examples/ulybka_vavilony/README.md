# Example project — Улыбка Вавилоны, scene 1

A small, valid project used by the tests and as a shape to copy. It holds the
bible, one scene (`s01_pub`) and three shots (`sh06`, `sh07`, `sh08`).

What comes from the design doc, as written there: the shot spec for `sh07`, the
`dima` character file, the scene's seating and 180° axis, and the world rules
about the plaster walls and the calm beer.

Everything else — `sh06` and `sh08`, the `durik` and `zhmurik` files, the style
text — is filled in so the chain has two cuts to check and the prompts have
something to render. Replace it with the real design before using it for a film.

There are no images here: the sheets, refs, blockouts and takes a real project
accumulates are not in Git. `shotforge shot s01_pub sh07` will list them as
missing, which is what it should do.

```bash
shotforge validate -C examples/ulybka_vavilony
shotforge status   -C examples/ulybka_vavilony
shotforge serve    -C examples/ulybka_vavilony
```
