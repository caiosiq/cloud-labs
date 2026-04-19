"""
Lab primitives: validated command bodies, registry metadata, dispatch.

See ``README.md`` in this directory for an overview. Design narrative: ``../../primitives.md``.
``POST /api/command`` uses parse + execute/schedule; read primitives
(``GET_TUNABLES``, ``GET_MEASURABLES``) use ``fetch_read_primitive`` with GET routes.
"""

from .dispatch import (
    execute_validated_command,
    fetch_read_primitive,
    parse_command_payload,
    schedule_validated_command,
    validation_error_detail,
)
from .ids import MACRO_PRIMITIVE_IDS, READ_PRIMITIVE_IDS, PrimitiveId, PrimitiveKind
from .protocol import LabPrimitiveSurface
from .registry import PRIMITIVE_REGISTRY
from .schemas import (
    ConfirmHoldingTagBody,
    HoverBody,
    MoveComponentBody,
    ObserveMeasurablesBody,
    PickComponentBody,
    PlaceFromHoverBody,
    ScanRotateInPlaceBody,
    TagQuery,
)

__all__ = [
    "PrimitiveId",
    "PrimitiveKind",
    "READ_PRIMITIVE_IDS",
    "MACRO_PRIMITIVE_IDS",
    "PRIMITIVE_REGISTRY",
    "TagQuery",
    "ObserveMeasurablesBody",
    "MoveComponentBody",
    "PickComponentBody",
    "HoverBody",
    "PlaceFromHoverBody",
    "ScanRotateInPlaceBody",
    "ConfirmHoldingTagBody",
    "LabPrimitiveSurface",
    "parse_command_payload",
    "execute_validated_command",
    "schedule_validated_command",
    "fetch_read_primitive",
    "validation_error_detail",
]
