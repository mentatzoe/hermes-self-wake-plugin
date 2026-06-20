"""Optional CLI subcommands: hermes self-wake ...

Registers CLI commands via ctx.register_cli_command if implemented.
Shares implementation with /self-wake slash command.
"""


def self_wake_cli(args):
    """Handle `hermes self-wake <subcommand>` CLI invocations.

    Subcommands:
    - sessions: resolve target sessions
    - subscribe: manage Kanban wake subscriptions
    - receipts: inspect wake receipts
    - doctor: run diagnostics
    """
    # TODO: implement
    # - Route to appropriate handler based on subcommand
    # - Share implementation with tool handlers
    pass
