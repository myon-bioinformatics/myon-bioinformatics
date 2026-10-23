"""Keep node_launch aligned with cli_args node bridge helpers (cli_args vendors this file).

When changing resolution order, argv shape, or error codes here, update cli_args
``resolve_node`` / ``node_command`` / ``run_node`` / ``node_or_command`` in the
same change (or the follow-up vendor PR).
"""
from __future__ import annotations

import node_launch

# Documented contract shared with myon-bioinformatics/cli_args (node bridge section).
CLI_ARGS_NODE_BRIDGE_PARITY = {
    "min_node_major": 24,
    "default_env_var": "NODE",
    "public_helpers": (
        "resolve_node",
        "node_command",
        "run_node",
        "run",
        "run_argv",
        "node_or_command",
        "is_node_target",
    ),
    "error_codes": (
        "EINPUTTOOLARGE",
        "ERESPONSETOOLARGE",
        "ELAUNCHFAILURE",
        "ELAUNCHSTDINCLOSED",
    ),
    "resolution_order": (
        "explicit node argument",
        "os.environ[env_var] when set",
        "shutil.which('node')",
    ),
    "reject_windows_batch": (".cmd", ".bat"),
}


def test_cli_args_node_bridge_parity_contract():
    assert node_launch.MIN_NODE_MAJOR == CLI_ARGS_NODE_BRIDGE_PARITY["min_node_major"]
    assert node_launch.DEFAULT_ENV_VAR == CLI_ARGS_NODE_BRIDGE_PARITY["default_env_var"]
    assert node_launch.HOST_RESPONSE_MARGIN_BYTES == 512
    for name in CLI_ARGS_NODE_BRIDGE_PARITY["public_helpers"]:
        assert hasattr(node_launch, name), name
    assert node_launch.resolve_node.__doc__ is not None
    assert "24" in node_launch.resolve_node.__doc__
