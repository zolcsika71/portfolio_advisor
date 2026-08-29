"""Versioned central analytical database schemas."""

from .v3 import (
    SCHEMA_VERSION,
    ProjectionError,
    SchemaVersionError,
    connect,
    create_analytical_holding_projection,
    detect_schema_version,
    initialize_schema,
    validate_schema,
)

__all__ = [
    "SCHEMA_VERSION",
    "ProjectionError",
    "SchemaVersionError",
    "connect",
    "create_analytical_holding_projection",
    "detect_schema_version",
    "initialize_schema",
    "validate_schema",
]
