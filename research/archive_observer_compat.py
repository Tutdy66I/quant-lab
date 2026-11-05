"""Exact observer-version compatibility for the 2.7A CLI write guard."""

# The only approved transition: 2.6 baseline to the audited 2.7A observer.
LEGACY_OBSERVER_SHA256 = 'b453c1fa758513ff458860d41633eb3d76231c2e03c1f28617ae941b9f926409'
GUARDED_OBSERVER_SHA256 = 'd21938b0c4bed3383d79cdd94df3ddf5db6fa5ca48721e5697c4674dc5669974'

# Internal cooperation marker, not an authentication mechanism against local code.
MANAGED_LEASE = object()


def supports_snapshot(recorded, current):
    return current == GUARDED_OBSERVER_SHA256 and recorded in (
        LEGACY_OBSERVER_SHA256, GUARDED_OBSERVER_SHA256
    )
