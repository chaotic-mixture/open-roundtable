"""Fail-closed publication boundary, not a package builder.

Run directly from the source checkout. A future packaging implementation must
be independently audited for private source exclusion before replacing this.
"""


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise RuntimeError('Package publishing is not supported in Phase 0; run from the source checkout.')


def build_sdist(sdist_directory, config_settings=None):
    raise RuntimeError('Package publishing is not supported in Phase 0; run from the source checkout.')
