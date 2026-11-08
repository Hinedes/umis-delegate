"""Code-defined command registry.

The registry is configuration, not data: no database table can add a command.
Each command owns its schema check, permission, scope/authorize callable,
preview, and executor. The gateway is the only caller of ``execute``.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

RISK_NORMAL = 'normal'
RISK_DESTRUCTIVE = 'destructive'


class CommandError(Exception):
    """Base class for command outcomes the gateway can summarize."""

    code = 'command_error'


class CommandValidationError(CommandError):
    code = 'invalid_arguments'


class CommandDenied(CommandError):
    code = 'denied'


class CommandNotFound(CommandError):
    code = 'not_found'


class CommandConflict(CommandError):
    code = 'conflict'


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    parameters: dict
    permission: str | None
    risk: str
    validator: Callable[[dict], dict]
    authorize: Callable[[Any, dict], dict]
    preview: Callable[[Any, dict], dict]
    execute: Callable[[Any, dict, Any, Any], dict]
    source_states: tuple = field(default_factory=tuple)
    # Object-scoped commands (assigned doctor) may satisfy authorization
    # through the authorize callable instead of a Django model permission.
    object_permission: bool = False

    @property
    def destructive(self):
        return self.risk == RISK_DESTRUCTIVE


_REGISTRY: dict[str, Command] = {}


def register(command):
    if command.name in _REGISTRY:
        raise RuntimeError(f"Duplicate command {command.name!r}")
    _REGISTRY[command.name] = command
    return command


def get_command(name):
    return _REGISTRY.get(name)


def all_commands():
    return dict(sorted(_REGISTRY.items()))


def command_definitions():
    """OpenAI tool definitions derived from the registry."""
    return [
        {
            'type': 'function',
            'function': {
                'name': command.name,
                'description': command.description,
                'parameters': command.parameters,
            },
        }
        for command in all_commands().values()
    ]
