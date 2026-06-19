"""MCP OAuth 2.1 authorization-server primitives.

PR 1 deliverables (no routes mounted):

* ``tokens`` — opaque access/refresh token gen + hash + verify, plus the
  signed authorization-request blob carried to the consent page.
* ``provider`` — ``PulseOAuthProvider`` implementing the SDK's
  ``OAuthAuthorizationServerProvider`` protocol.
* ``verifier`` — ``PulseTokenVerifier`` unifying legacy ``pulse_`` API
  keys with OAuth access tokens.

PR 2 mounts the SDK auth routes + the consent page and flips the MCP
endpoint into resource-server mode using these primitives.
"""
