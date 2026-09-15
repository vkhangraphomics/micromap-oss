import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.load_knowledge_graph import _build_arg_parser, _dispatch


def _args(*argv):
    return _build_arg_parser().parse_args(list(argv))


def test_derive_flag_is_registered():
    assert _args("--derive", "--neo4j-database", "neo4j").derive is True


def test_derive_flag_runs_derive_only():
    args = _args("--derive", "--neo4j-database", "neo4j")
    driver = MagicMock()
    base = "database.load_knowledge_graph."
    with patch(base + "derive") as p_derive, \
         patch(base + "ensure_data_dir", return_value=Path("/tmp")), \
         patch(base + "create_indexes_and_constraints") as p_idx:
        _dispatch(args, driver, s3_manager=None)
    p_derive.assert_called_once_with(driver, "neo4j")
    p_idx.assert_not_called()   # --derive alone builds nothing else


def test_derive_not_run_without_flag():
    args = _args("--indexes", "--neo4j-database", "neo4j")
    driver = MagicMock()
    base = "database.load_knowledge_graph."
    with patch(base + "derive") as p_derive, \
         patch(base + "ensure_data_dir", return_value=Path("/tmp")), \
         patch(base + "create_indexes_and_constraints"):
        _dispatch(args, driver, s3_manager=None)
    p_derive.assert_not_called()
