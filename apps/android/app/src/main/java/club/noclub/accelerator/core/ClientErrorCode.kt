package club.noclub.accelerator.core

/**
 * The non-core fixed codes the mobile client can produce, mirroring the names already
 * fixed in `core/fairwind/errors.py` and used by the CLI and the control API
 * (spec 67, 65, 132).
 *
 * Separate from [CoreErrorCode] on purpose: "the core failed" and "the selection had
 * nothing eligible" are different failures with different user actions, and collapsing
 * them into one code would hide which one happened.
 *
 * | code                    | raised when                                                    |
 * |-------------------------|----------------------------------------------------------------|
 * | `NODE_NO_ELIGIBLE`      | nothing passed eligibility — the shipped state today (spec 65)  |
 * | `NODE_ID_INVALID`       | an explicit node id is malformed                                |
 * | `NODE_NOT_FOUND`        | an explicit node id matches nothing                             |
 * | `NODE_ID_AMBIGUOUS`     | a prefix matches more than one node                             |
 * | `VPN_CONSENT_REQUIRED`  | the user has not approved the VPN in the system dialog          |
 * | `VPN_REVOKED`           | the OS revoked the tunnel; the client did not stop it           |
 *
 * `// TODO(Gate B)`: assert this list against `domain/errors.py` in CI rather than trusting
 * two hand-maintained copies to stay in step.
 */
enum class ClientErrorCode(val wire: String) {
    NODE_NO_ELIGIBLE("NODE_NO_ELIGIBLE"),
    NODE_ID_INVALID("NODE_ID_INVALID"),
    NODE_NOT_FOUND("NODE_NOT_FOUND"),
    NODE_ID_AMBIGUOUS("NODE_ID_AMBIGUOUS"),
    VPN_CONSENT_REQUIRED("VPN_CONSENT_REQUIRED"),
    VPN_REVOKED("VPN_REVOKED"),
}
