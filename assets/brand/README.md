# Brand assets

The profile icon and favicon are cropped from the supplied brand artwork, without
redrawing, recoloring, masking, or adding transparency. Both PNGs retain the
original crop pixels and background.

- [Profile icon](profile-icon.png): 384 × 384 pixels, displayed at 160 × 160 in the
  profile README.
- [Favicon source](favicon.png): 160 × 160 pixels, available as a reusable PNG asset.
- [Favicon ICO](favicon.ico): 16 × 16, 32 × 32, and 48 × 48 frames, resized from
  the favicon source with LANCZOS resampling. No recoloring, masking, or
  transparency was added.

These ICO and PNG files are **not** the same bytes as the
[portfolio website](https://myon-bioinformatics.github.io/) favicon. The
portfolio keeps its own copies (`favicon.ico` is 6,893 bytes with 24-bit frames,
source PNG 77 × 77, profile icon 170 × 170). This repository’s `favicon.ico` is
8,943 bytes with 32-bit frames; `favicon.png` is 160 × 160; `profile-icon.png`
is 384 × 384. The [concept diagram](concept-overview.jpg) *is* the same bytes as
the portfolio’s `assets/brand/myon-concept-diagram.jpg`.

This repository is a GitHub profile and shared-tooling repository. Its README
cannot configure the favicon of github.com or change the GitHub account avatar.
The audit GitHub Pages site
(https://myon-bioinformatics.github.io/myon-bioinformatics/) publishes copies of
`favicon.ico` and `favicon.png` at the site root, linked with relative hrefs
from the generated HTML (not `/favicon.ico`, which would resolve on the
portfolio host). The portfolio website continues to manage its own browser
icons separately.

The [concept diagram](concept-overview.jpg) is the supplied updated 1536 × 1024
JPEG, copied without cropping, recompression, or other image modifications. Its
reference to “this site” means the [portfolio website](https://myon-bioinformatics.github.io/),
not this GitHub profile repository.
