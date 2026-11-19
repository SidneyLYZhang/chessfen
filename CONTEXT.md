# ChessFEN

ChessFEN converts between chess board images, PGN games, and FEN positions, and renders positions back to board images. This glossary pins the canonical vocabulary for code and issues; user-facing labels are localized in Chinese, English, and German.

## Chess encodings

**FEN**:
Forsyth–Edwards Notation, the standard string encoding of a chess Position. Always qualify which form you mean: Placement or Full FEN.
_Avoid_: bare "FEN" when only the placement is meant

**Placement**:
The first field of a FEN string: eight ranks of piece letters separated by `/`, describing only where pieces stand. The output of Recognition.
_Avoid_: "board string", unqualified "FEN"

**Full FEN**:
All six FEN fields: placement, side-to-move, castling rights, en passant target, halfmove clock, and fullmove number. The output of PGN navigation and the input to Rendering.
_Avoid_: "complete FEN", "full position string"

**Position**:
A chess board state, identified by a Placement plus the metadata carried in a Full FEN.
_Avoid_: "setup", "arrangement"

**Ply**:
One half-move: a single move by one side. "After White's Nth move" is ply 2N−1; "after Black's Nth move" is ply 2N. Ply 0 is the initial position.
_Avoid_: "move" (a full move is two plies), "step", "turn"

**Game**:
One chess game within a PGN document, with its own metadata and move list.
_Avoid_: "match", "round"

**Mainline**:
The principal move sequence of a Game, excluding any variations.
_Avoid_: "variation" (variations are explicitly not followed)

**PGN**:
Portable Game Notation, a text format recording one or more Games with moves and metadata.
_Avoid_: "game file" (ambiguous)

## Recognition and rendering

**Recognition**:
Deriving a Placement from a Board image.
_Avoid_: "detection" (detection is one internal step), "OCR" (the model family name, not the task)

**Board image**:
A cropped, axis-aligned, two-dimensional image of a chess board — the supported input to Recognition.
_Avoid_: "photo" (perspective photos are out of scope)

**Square assignment**:
Mapping detected pieces onto the 8×8 grid by piece center to build a Placement.
_Avoid_: "per-square classification" (there is no per-square classifier; pieces are detected whole-image, then assigned)

**Square result**:
One entry of the 64-cell breakdown returned by Recognition: the piece assigned to that cell (or empty) and its detection confidence.
_Avoid_: "tile", "cell output"

**Rendering**:
Producing a Board image from a Placement or Full FEN.
_Avoid_: "drawing", "export"

## Access

**API key**:
A credential that grants access to the API, issued and revoked by the Admin.
_Avoid_: "token", "password"

**Admin**:
The single operator who manages API keys through the admin page.
_Avoid_: "user", "administrator account" (there is exactly one, password-based)

**Visitor**:
An anonymous person using the public web tools and introduction pages.
_Avoid_: "user" (overloaded across Visitor, Admin, and API key holder)