# ChessFEN

ChessFEN converts between chess board images, PGN games, and FEN positions, renders positions back to board images, and analyzes Games and Positions. This glossary pins the canonical vocabulary for code and issues; user-facing labels are localized in Chinese, English, and German.

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

## Analysis

**Analysis**:
The assessment of one Game's Mainline or one Position, comprising engine-backed evaluations, Move Assessments, Candidate Lines, and optional Explanations.
_Avoid_: "recognition", "rendering", unqualified "review"

**Game Analysis**:
Analysis of every Ply by both sides in one selected Game's Mainline; branch variations are outside its scope.
_Avoid_: "PGN analysis" when the input contains multiple Games or branch variations

**Position Analysis**:
Analysis of the side to move in one legal Position described by a Full FEN, optionally including a Move Assessment for a supplied actual move.
_Avoid_: "last-move analysis" from a Full FEN alone

**Evaluation**:
An engine's centipawn or mate score for a Position, normalized to White's perspective: positive favors White and negative favors Black. Mate scores remain distinct from centipawn scores.
_Avoid_: unqualified "score", side-to-move sign conventions, "advantage" without naming the favored side

**Move Assessment**:
The deterministic quality classification of an actual Ply by comparing its Evaluation with the best available alternative from the same Position.
_Avoid_: "LLM rating", "opinion"

**Move quality**:
The canonical Move Assessment labels: `Best`, `Excellent`, `Good`, `Inaccuracy`, `Mistake`, `Blunder`, and `Forced` when only one legal Ply exists.
_Avoid_: unqualified "good move" or "bad move", "brilliant", "book"

**Centipawn Loss (CPL)**:
The non-negative centipawn difference between the best alternative and the actual Ply from the mover's perspective; mate scores require a separate mate-aware comparison.
_Avoid_: raw Evaluation difference without normalizing perspective

**Candidate Line**:
One ranked engine alternative from a Position, containing its proposed first Ply and the Principal Variation that follows it.
_Avoid_: "best move" when referring to an entire sequence

**Principal Variation (PV)**:
The engine's predicted best sequence of Plies after a chosen initial Ply.
_Avoid_: "mainline" (reserved for the recorded Game), "future moves"

**MultiPV**:
The Visitor-selected number of Candidate Lines requested for each analyzed Position.
_Avoid_: "depth", "number of moves"

**Key Ply**:
A Ply selected for an automatically generated Explanation because of its Move quality, comparative importance, or a significant Evaluation change.
_Avoid_: "interesting move" without selection criteria

**Turning Point**:
A Ply at which the Evaluation changes significantly or a forced mate appears or disappears.
_Avoid_: unqualified "critical position", "mistake" (Move quality is a separate classification)

**Explanation**:
LLM-generated natural-language interpretation grounded in Evaluations, Candidate Lines, and structured Position facts; it does not determine Move quality.
_Avoid_: "analysis" when distinguishing generated prose from engine results

**LLM Profile**:
The Visitor's currently active third-party provider, region where applicable, model, reasoning preferences, and Explanation language and audience settings.
_Avoid_: "account", "server settings", "Admin configuration"

**Provider region**:
A provider-specific API deployment with its own Base URL, model catalog, credential, privacy consent, and request confirmation. Kimi exposes China and International regions.
_Avoid_: "locale", "country setting", implicit credential reuse across hosts

**Reasoning effort**:
The Visitor-requested level of third-party model reasoning for an Explanation, distinct from Stockfish search depth and subject to the selected model's capabilities.
_Avoid_: "thinking depth", "engine strength", "MultiPV"

**Reasoning dialect**:
The provider- and model-specific representation of reasoning enablement, Reasoning effort, and generation budget; OpenAI-compatible Chat Completions does not imply a shared reasoning parameter contract.
_Avoid_: "standard reasoning parameters", "universal effort field"

**Analysis Workspace**:
The Visitor's current resumable analysis input and results, retained locally until replaced or cleared.
_Avoid_: "account", "cloud project", "history"

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

**Orientation**:
The visual rotation of a Board image relative to canonical files and ranks: `normal` when the rank-1 marker sits below the board centre, `flipped` when it sits above, and `unknown` when no marker is detected.
_Avoid_: "rotation", "direction"

**Position warning**:
A machine-readable flag naming one way a FEN parses but describes an illegal Position, such as `no_white_king`. Warnings accompany a rendered image without blocking it; web labels are localized.
_Avoid_: "error" (warnings do not reject input)

**Rendering**:
Producing a Board image from a Placement or Full FEN.
_Avoid_: "drawing", "export"

## Access

**API key**:
A credential that grants access to the API, issued and revoked by the Admin.
_Avoid_: "token", "password"

**LLM API key**:
A Visitor-supplied credential for a third-party language-model provider; it is unrelated to a ChessFEN API key and is never issued by ChessFEN.
_Avoid_: bare "API key", "ChessFEN key", "Admin credential"

**Admin**:
The single operator who manages API keys through the admin page.
_Avoid_: "user", "administrator account" (there is exactly one, password-based)

**Visitor**:
An anonymous person using the public web tools and introduction pages.
_Avoid_: "user" (overloaded across Visitor, Admin, and API key holder)