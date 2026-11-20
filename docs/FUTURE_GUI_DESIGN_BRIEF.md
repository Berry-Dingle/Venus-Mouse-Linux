# Future GUI design brief

The project still intends to ship a native Linux GUI, but the first GTK4 /
libadwaita implementation was discarded because its visual and interaction
direction was not satisfactory. The next implementation must begin only after
an approved visual mock-up or design review.

## Design direction

- polished dark utility for the wired Venus mouse;
- real top and side mouse visuals, or approved SVG-style artwork;
- hotspot-based physical-control selection;
- compact profile selector;
- tabs for Buttons, DPI, Lighting, Macros, and Device;
- assignment panel with bounded, evidence-backed choices;
- no raw protocol clutter in the primary UI;
- Advanced details for stable assignment IDs, raw records, addresses, and
  evidence paths;
- responsive layout for normal desktop and narrower laptop windows.

## Buttons view

The physical layout must be represented by real mouse-oriented visuals rather
than a generic button grid. The top view contains Fire, Left Click,
Wheel / Middle Click, Right Click, DPI+, and DPI−. The side view contains the
physical side buttons 1–12. Top and Side must be separate views or clearly
separated visual regions.

Protocol identity must remain independent from image coordinates. Stable
assignment IDs and the existing guarded write boundaries remain the backend
source of truth. A visual hotspot selects a semantic/group-qualified control;
it must not calculate protocol addresses.

## Implementation boundary

No live operation wiring is permitted until the visual shell is approved.
Apply, Restore, discovery, HID, USB, hidraw, and DeviceSession access must be
added only through existing guarded semantic boundaries in a later milestone.
The GUI must not claim persistence from immediate readback.
