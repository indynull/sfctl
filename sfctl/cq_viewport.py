"""Response summary terminal-width mode for arena review.

Constrains the response body to a classic terminal column count so reviewers
can judge wrapping and density at a realistic width.
"""

from __future__ import annotations

# Classic terminal width in character cells.
RESPONSE_TERMINAL_WIDTH = 80

# CSS class on the response summary body (fixed width + outline).
RESPONSE_WIDTH_CLASS = "response-terminal-width"

# CSS class on the host that centers the body when narrow mode is on.
RESPONSE_WRAP_CLASS = "response-wrap"
RESPONSE_WRAP_NARROW_CLASS = "response-wrap-narrow"
